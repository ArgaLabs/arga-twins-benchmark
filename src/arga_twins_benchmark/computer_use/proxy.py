"""Local browser data plane. Upstream addresses and credentials stay in the runner."""

from __future__ import annotations

import json
import re
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from arga_twins_benchmark.providers import provider_request_headers

BLOCKED = frozenset(
    {
        "admin",
        "_admin",
        "_twin",
        "inspect",
        "reset",
        "seed",
        "grader",
        "grading",
        "openapi.json",
        "openapi.yaml",
        "swagger.json",
        "docs",
        "redoc",
        "mcp",
        "scenarios",
        "export",
        "fidelity",
        "control-plane",
        "control_plane",
        "schema",
        "schemas",
        "export-scenario",
    }
)

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


async def bounded_body(request: Request, limit: int = MAX_REQUEST_BYTES) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            count = int(length)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if count < 0:
            raise HTTPException(400, "Invalid Content-Length")
        if count > limit:
            raise HTTPException(413, "Request too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def introspection_query(request: Request, body: bytes) -> bool:
    queries = request.query_params.getlist("query")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        queries.append(body.decode(errors="replace"))
    else:
        for item in cast(list[Any], payload) if isinstance(payload, list) else [payload]:
            if isinstance(item, dict):
                query = cast(dict[str, Any], item).get("query")
                if isinstance(query, str):
                    queries.append(query)
    return any(re.search(r"\b__(?:schema|type)\b", query) for query in queries)


def allowed_path(path: str) -> bool:
    decoded = path
    for _ in range(8):
        updated = unquote(decoded)
        if updated == decoded:
            break
        decoded = updated
    if "%" in decoded or "\\" in decoded or not decoded.startswith("/") or "//" in decoded:
        return False
    segments = decoded.casefold().split("/")
    if any(segment in {".", ".."} for segment in segments):
        return False
    # Control endpoints live at the twin root (or its UI control prefix).
    # Provider data may legitimately be named "docs", "admin" or "schema",
    # and document export is an ordinary frontend feature.
    if segments[1] in BLOCKED:
        return False
    return not (segments[1] in {"ui", "_ui"} and len(segments) > 2 and segments[2] in BLOCKED)


class BrowserProxy:
    def __init__(self, provider: str, access: dict[str, Any], *, client: httpx.AsyncClient | None = None):
        self.provider = provider
        self.upstream = str(access["base_url"]).rstrip("/")
        self.env = cast(dict[str, str], access.get("env", {}))
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.origin = ""
        self.accepting = True
        self.events: list[dict[str, Any]] = []
        self.app = Starlette(
            routes=[Route("/{path:path}", self.forward, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])]
        )

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    def sanitized(self, text: str) -> str:
        # The shared twin middleware injects these exact operator-only elements.
        # Remove both the drawer and its code, not merely its visible toggle.
        for tag in ("style", "script", "aside"):
            text = re.sub(
                rf"<{tag}\b[^>]*\bdata-twin-control-plane[^>]*>.*?</{tag}>",
                "",
                text,
                flags=re.I | re.S,
            )
        text = re.sub(r"<button\b[^>]*\bdata-tcp-toggle[^>]*>.*?</button>", "", text, flags=re.I | re.S)
        text = text.replace(self.upstream, self.origin)
        for key, value in self.env.items():
            if len(value) >= 8 and any(s in key.upper() for s in ("TOKEN", "KEY", "SECRET")):
                text = text.replace(value, "twin-browser-session")
        # Scenario export is trusted runner functionality, not a provider control.
        text = re.sub(
            r"<form\b[^>]*action=[\"\'][^\"\']*(?:scenarios/export|export-scenario)[\"\'][^>]*>.*?</form>",
            "",
            text,
            flags=re.I | re.S,
        )
        return text

    async def forward(self, request: Request) -> Response:
        if not self.accepting:
            return JSONResponse({"error": "Rollout finished"}, status_code=410)
        if str(request.base_url).rstrip("/") != self.origin:
            return JSONResponse({"error": "Unexpected host"}, status_code=403)
        if request.headers.get("origin") not in {None, self.origin}:
            return JSONResponse({"error": "Cross-origin request denied"}, status_code=403)
        path = request.url.path
        if not allowed_path(path):
            self.events.append({"provider": self.provider, "method": request.method, "path": path, "blocked": True})
            return JSONResponse({"error": "This route is outside the provider workspace"}, status_code=403)
        body = await bounded_body(request)
        if path.rstrip("/") == "/graphql" and introspection_query(request, body):
            return JSONResponse({"error": "Use official provider documentation"}, status_code=403)
        headers = provider_request_headers(self.provider, self.env)
        for name in ("content-type", "accept", "if-match", "idempotency-key", "notion-version"):
            if name in request.headers:
                headers[name] = request.headers[name]
        target = self.upstream + path
        if request.url.query:
            target += "?" + request.url.query
        try:
            async with self.client.stream(request.method, target, content=body, headers=headers) as upstream:
                chunks: list[bytes] = []
                size = 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        self.events.append(
                            {
                                "provider": self.provider,
                                "method": request.method,
                                "path": path,
                                "status": 502,
                                "error": "response_limit",
                            }
                        )
                        return JSONResponse(
                            {"error": "Provider response exceeds browser transfer limit"}, status_code=502
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
        except httpx.HTTPError:
            self.events.append({"provider": self.provider, "method": request.method, "path": path, "status": 502})
            return JSONResponse({"error": "Provider unavailable; retry this action"}, status_code=502)
        self.events.append(
            {"provider": self.provider, "method": request.method, "path": path, "status": upstream.status_code}
        )
        response_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() in {"content-type", "cache-control", "etag", "retry-after", "content-disposition"}
        }
        response_headers["cache-control"] = "no-store"
        response_headers["x-content-type-options"] = "nosniff"
        if "location" in upstream.headers:
            location = upstream.headers["location"]
            parts = urlsplit(location)
            if parts.netloc and not location.startswith(self.upstream + "/"):
                return JSONResponse({"error": "External navigation is outside this workspace"}, status_code=403)
            location = location.replace(self.upstream, self.origin)
            if not allowed_path(urlsplit(location).path):
                return JSONResponse({"error": "Redirect outside workspace"}, status_code=403)
            response_headers["location"] = location
        content_type = upstream.headers.get("content-type", "")
        if any(t in content_type for t in ("text/", "json", "javascript", "xml")):
            content = self.sanitized(content.decode(upstream.encoding or "utf-8", errors="replace")).encode()
        return Response(content, status_code=upstream.status_code, headers=response_headers)
