from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from typing import Final, cast
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import httpx
import yaml

PROVIDER_DOCS_TOOL_NAME: Final = "provider_docs"
SUPPORTED_DOC_PROVIDERS: Final = frozenset(
    {
        "discord",
        "github",
        "gitlab",
        "gmail",
        "google_calendar",
        "google_drive",
        "google_docs",
        "google_sheets",
        "hubspot",
        "jira",
        "linear",
        "linkedin",
        "notion",
        "salesforce",
        "slack",
        "stripe",
    }
)
_REDIRECT_STATUS_CODES: Final = frozenset({301, 302, 303, 307, 308})
_ALLOWED_CONTENT_TYPES: Final = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/x-yaml",
        "application/yaml",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/x-markdown",
        "text/yaml",
    }
)
_ALLOWED_TOOL_INPUT_KEYS: Final = frozenset({"action", "doc_id", "provider", "query", "url"})
_MAX_QUERY_LENGTH: Final = 200
_MAX_DOC_ID_LENGTH: Final = 120
_MAX_URL_LENGTH: Final = 2_048
_MAX_RETURNED_LINKS: Final = 100
_DEFAULT_PROVIDER_RESPONSE_BYTES: Final = 524_288
_PROVIDER_RESPONSE_BYTES: Final[dict[str, int]] = {
    # Atlassian's official Jira reference pages are currently 2.8-3.9 MiB
    # because the endpoint documentation follows a large static application
    # shell. The model-visible extraction remains independently capped.
    "jira": 4_194_304,
}
_MAX_PROVIDER_RESPONSE_BYTES: Final = max(
    _DEFAULT_PROVIDER_RESPONSE_BYTES,
    *_PROVIDER_RESPONSE_BYTES.values(),
)
_SKIPPED_HTML_ELEMENTS: Final = frozenset({"canvas", "noscript", "script", "style", "svg"})
_BLOCK_HTML_ELEMENTS: Final = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]{0,300}\]\(([^)\s]+)")
_PLAINTEXT_URL = re.compile(r"https://[^\s<>{}\"'`]+")


class OfficialDocsConfigurationError(ValueError):
    """Raised when the checked-in official documentation catalog is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class OfficialDocEntry:
    id: str
    title: str
    url: str
    operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderDocsEntry:
    provider: str
    owner: str
    api_version: str
    allowed_hosts: frozenset[str]
    allowed_paths: Mapping[str, tuple[str, ...]]
    documents: tuple[OfficialDocEntry, ...]


@dataclass(frozen=True, slots=True)
class OfficialDocsCatalog:
    schema_version: int
    catalog_reviewed_at: str
    retrieval_policy: str
    providers: Mapping[str, ProviderDocsEntry]


@dataclass(frozen=True, slots=True)
class OfficialDocsTraceRecord:
    sequence: int
    started_at: str
    requested_provider: str
    provider: str | None
    action: str | None
    doc_id: str | None
    source_url: str | None
    final_url: str | None
    status_code: int | None
    latency_ms: int
    response_bytes: int
    content_sha256: str | None
    truncated: bool
    cache_hit: bool
    query_present: bool
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class _FetchedOfficialDoc:
    provider: str
    body: bytes
    text: str
    links: tuple[tuple[str, str], ...]
    source_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    status_code: int
    response_bytes: int
    content_sha256: str
    content_type: str
    retrieved_at: str
    truncated: bool
    etag: str | None
    last_modified: str | None


class OfficialDocsSnapshotCache:
    """A first-fetch snapshot shared by every trial in one benchmark suite."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _FetchedOfficialDoc] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._catalog_identity: str | None = None

    def bind_catalog(self, catalog: OfficialDocsCatalog) -> None:
        identity = _catalog_identity(catalog)
        if self._catalog_identity is None:
            self._catalog_identity = identity
        elif self._catalog_identity != identity:
            raise OfficialDocsConfigurationError("one official docs snapshot cache cannot mix different catalogs")

    async def get_or_fetch(
        self,
        key: tuple[str, str],
        fetcher: Callable[[], Awaitable[_FetchedOfficialDoc]],
    ) -> tuple[_FetchedOfficialDoc, bool]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._entries.get(key)
            if cached is not None:
                return cached, True
            fetched = await fetcher()
            self._entries[key] = fetched
            return fetched, False

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def load_artifacts(self, root: Path, *, catalog: OfficialDocsCatalog) -> None:
        """Restore a prior suite snapshot so resumed trials see identical bytes."""

        self.bind_catalog(catalog)
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            return
        try:
            raw_manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise OfficialDocsConfigurationError("official docs cache manifest is unreadable") from error
        if not isinstance(raw_manifest, Mapping):
            raise OfficialDocsConfigurationError("official docs cache manifest must be an object")
        manifest = cast(Mapping[object, object], raw_manifest)
        if (
            manifest.get("protocol") != "arga-bench-official-docs-cache/1"
            or manifest.get("catalog_identity_sha256") != self._catalog_identity
        ):
            raise OfficialDocsConfigurationError("official docs cache manifest protocol or catalog identity changed")
        raw_entries = manifest.get("entries")
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes, bytearray)):
            raise OfficialDocsConfigurationError("official docs cache entries must be an array")
        restored: dict[tuple[str, str], _FetchedOfficialDoc] = {}
        for raw_entry in cast(Sequence[object], raw_entries):
            if not isinstance(raw_entry, Mapping):
                raise OfficialDocsConfigurationError("official docs cache entry must be an object")
            entry = cast(Mapping[object, object], raw_entry)
            provider = entry.get("provider")
            requested_url = entry.get("requested_url")
            final_url = entry.get("final_url")
            digest = entry.get("content_sha256")
            content_type = entry.get("content_type")
            body_file = entry.get("body_file")
            if (
                not isinstance(provider, str)
                or provider not in catalog.providers
                or not isinstance(requested_url, str)
                or not isinstance(final_url, str)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(content_type, str)
                or content_type not in _ALLOWED_CONTENT_TYPES
                or body_file != f"responses/{digest}.body"
            ):
                raise OfficialDocsConfigurationError("official docs cache entry identity is invalid")
            provider_docs = catalog.providers[provider]
            checked_requested_url = _validate_official_url(requested_url, provider_docs=provider_docs)
            checked_final_url = _validate_official_url(final_url, provider_docs=provider_docs)
            body_path = root / cast(str, body_file)
            body = body_path.read_bytes()
            if hashlib.sha256(body).hexdigest() != digest or entry.get("response_bytes") != len(body):
                raise OfficialDocsConfigurationError("official docs cached response body failed integrity validation")
            raw_redirect_chain = entry.get("redirect_chain")
            if not isinstance(raw_redirect_chain, Sequence) or isinstance(raw_redirect_chain, (str, bytes, bytearray)):
                raise OfficialDocsConfigurationError("official docs cache redirect chain is invalid")
            redirect_values = cast(Sequence[object], raw_redirect_chain)
            redirect_items: list[str] = []
            for url in redirect_values:
                if not isinstance(url, str):
                    raise OfficialDocsConfigurationError("official docs cache redirect chain is invalid")
                redirect_items.append(_validate_official_url(url, provider_docs=provider_docs))
            redirect_chain = tuple(redirect_items)
            text, raw_links = _decode_official_content(body, content_type=content_type)
            restored[(provider, checked_requested_url)] = _FetchedOfficialDoc(
                provider=provider,
                body=body,
                text=text,
                links=_allowlisted_links(
                    raw_links,
                    text=text,
                    base_url=checked_final_url,
                    provider_docs=provider_docs,
                ),
                source_url=checked_requested_url,
                final_url=checked_final_url,
                redirect_chain=redirect_chain,
                status_code=_required_int(entry.get("http_status"), field="http_status"),
                response_bytes=len(body),
                content_sha256=digest,
                content_type=content_type,
                retrieved_at=_required_string(entry.get("retrieved_at"), field="retrieved_at"),
                truncated=_required_bool(entry.get("truncated"), field="truncated"),
                etag=_optional_string(entry.get("etag")),
                last_modified=_optional_string(entry.get("last_modified")),
            )
        if manifest.get("entry_count") != len(restored):
            raise OfficialDocsConfigurationError("official docs cache entry count does not match its manifest")
        self._entries = restored

    def write_artifacts(self, root: Path, *, catalog: OfficialDocsCatalog) -> None:
        self.bind_catalog(catalog)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        responses_dir = root / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        responses_dir.chmod(0o700)
        entries: list[dict[str, object]] = []
        written_digests: set[str] = set()
        for (provider, requested_url), fetched in sorted(self._entries.items()):
            digest = fetched.content_sha256
            relative_body_path = Path("responses") / f"{digest}.body"
            body_path = root / relative_body_path
            if digest not in written_digests:
                _write_private_bytes(body_path, fetched.body)
                written_digests.add(digest)
            provider_docs = catalog.providers[provider]
            entries.append(
                {
                    "provider": provider,
                    "official_owner": provider_docs.owner,
                    "api_version": provider_docs.api_version,
                    "catalog_reviewed_at": catalog.catalog_reviewed_at,
                    "requested_url": requested_url,
                    "source_url": fetched.source_url,
                    "final_url": fetched.final_url,
                    "redirect_chain": list(fetched.redirect_chain),
                    "retrieved_at": fetched.retrieved_at,
                    "http_status": fetched.status_code,
                    "content_type": fetched.content_type,
                    "content_sha256": digest,
                    "response_bytes": fetched.response_bytes,
                    "truncated": fetched.truncated,
                    "etag": fetched.etag,
                    "last_modified": fetched.last_modified,
                    "body_file": relative_body_path.as_posix(),
                }
            )
        manifest = {
            "protocol": "arga-bench-official-docs-cache/1",
            "scope": "suite_first_fetch_snapshot",
            "retrieval_policy": catalog.retrieval_policy,
            "catalog_schema_version": catalog.schema_version,
            "catalog_reviewed_at": catalog.catalog_reviewed_at,
            "catalog_identity_sha256": self._catalog_identity,
            "entry_count": len(entries),
            "entries": entries,
        }
        _write_private_bytes(
            root / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )


class _OfficialHtmlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._active_link: str | None = None
        self._active_link_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in _SKIPPED_HTML_ELEMENTS:
            self._skip_depth += 1
            return
        if self._skip_depth != 0:
            return
        if lowered in _BLOCK_HTML_ELEMENTS:
            self._parts.append("\n")
        if lowered == "a" and self._active_link is None:
            href = next((value for name, value in attrs if name.casefold() == "href" and value), None)
            if href is not None:
                self._active_link = href
                self._active_link_text = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in _SKIPPED_HTML_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth != 0:
            return
        if lowered == "a" and self._active_link is not None:
            label = " ".join("".join(self._active_link_text).split())
            self.links.append((label, self._active_link))
            self._active_link = None
            self._active_link_text = []
        if lowered in _BLOCK_HTML_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth != 0 or not data.strip():
            return
        self._parts.append(data)
        if self._active_link is not None:
            self._active_link_text.append(data)

    def text(self) -> str:
        value = "".join(self._parts).replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t\f\v]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def load_official_docs_catalog(raw_catalog: object | None = None) -> OfficialDocsCatalog:
    if raw_catalog is None:
        resource = files("arga_twins_benchmark.providers").joinpath("official_api_docs.yaml")
        raw_catalog = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(raw_catalog, Mapping):
        raise OfficialDocsConfigurationError("official docs catalog must be an object")
    catalog = cast(Mapping[object, object], raw_catalog)
    schema_version = catalog.get("schema_version")
    reviewed_at = catalog.get("catalog_reviewed_at")
    retrieval_policy = catalog.get("retrieval_policy")
    raw_providers = catalog.get("providers")
    if schema_version != 1:
        raise OfficialDocsConfigurationError("official docs catalog schema_version must be 1")
    if not isinstance(reviewed_at, str) or not reviewed_at:
        raise OfficialDocsConfigurationError("official docs catalog requires catalog_reviewed_at")
    if retrieval_policy != "strictly_allowlisted_live_official_docs":
        raise OfficialDocsConfigurationError("official docs catalog has an unsupported retrieval_policy")
    if not isinstance(raw_providers, Mapping):
        raise OfficialDocsConfigurationError("official docs catalog providers must be an object")

    providers: dict[str, ProviderDocsEntry] = {}
    for raw_provider, raw_config in cast(Mapping[object, object], raw_providers).items():
        if not isinstance(raw_provider, str) or raw_provider not in SUPPORTED_DOC_PROVIDERS:
            raise OfficialDocsConfigurationError(f"unsupported official docs provider {raw_provider!r}")
        if not isinstance(raw_config, Mapping):
            raise OfficialDocsConfigurationError(f"official docs provider {raw_provider!r} must be an object")
        config = cast(Mapping[object, object], raw_config)
        owner = config.get("owner")
        api_version = config.get("api_version")
        if not isinstance(owner, str) or not owner:
            raise OfficialDocsConfigurationError(f"official docs provider {raw_provider!r} requires owner")
        if not isinstance(api_version, str) or not api_version:
            raise OfficialDocsConfigurationError(f"official docs provider {raw_provider!r} requires api_version")
        hosts = _validated_hosts(config.get("allowed_hosts"), provider=raw_provider)
        allowed_paths = _validated_allowed_paths(
            config.get("allowed_paths"),
            provider=raw_provider,
            allowed_hosts=hosts,
        )
        provider_entry = ProviderDocsEntry(
            provider=raw_provider,
            owner=owner,
            api_version=api_version,
            allowed_hosts=hosts,
            allowed_paths=allowed_paths,
            documents=(),
        )
        documents = _validated_documents(
            config.get("documents"),
            provider=raw_provider,
            provider_docs=provider_entry,
        )
        providers[raw_provider] = ProviderDocsEntry(
            provider=raw_provider,
            owner=owner,
            api_version=api_version,
            allowed_hosts=hosts,
            allowed_paths=allowed_paths,
            documents=documents,
        )
    missing = SUPPORTED_DOC_PROVIDERS - set(providers)
    if missing:
        raise OfficialDocsConfigurationError(
            f"official docs catalog is missing providers: {', '.join(sorted(missing))}"
        )
    return OfficialDocsCatalog(
        schema_version=1,
        catalog_reviewed_at=reviewed_at,
        retrieval_policy=cast(str, retrieval_policy),
        providers=providers,
    )


def _validated_hosts(raw_hosts: object, *, provider: str) -> frozenset[str]:
    if not isinstance(raw_hosts, Sequence) or isinstance(raw_hosts, (str, bytes, bytearray)):
        raise OfficialDocsConfigurationError(f"official docs provider {provider!r} allowed_hosts must be a list")
    hosts: set[str] = set()
    for raw_host in cast(Sequence[object], raw_hosts):
        if (
            not isinstance(raw_host, str)
            or not raw_host
            or raw_host != raw_host.casefold()
            or urlsplit(f"https://{raw_host}").hostname != raw_host
        ):
            raise OfficialDocsConfigurationError(f"official docs provider {provider!r} has an invalid allowed host")
        hosts.add(raw_host)
    if not hosts:
        raise OfficialDocsConfigurationError(f"official docs provider {provider!r} requires allowed_hosts")
    return frozenset(hosts)


def _validated_allowed_paths(
    raw_allowed_paths: object,
    *,
    provider: str,
    allowed_hosts: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw_allowed_paths, Mapping):
        raise OfficialDocsConfigurationError(f"official docs provider {provider!r} allowed_paths must be an object")
    mapping = cast(Mapping[object, object], raw_allowed_paths)
    if set(mapping) != set(allowed_hosts):
        raise OfficialDocsConfigurationError(
            f"official docs provider {provider!r} allowed_paths must define every and only allowed host"
        )
    result: dict[str, tuple[str, ...]] = {}
    for raw_host, raw_prefixes in mapping.items():
        if not isinstance(raw_host, str):
            raise OfficialDocsConfigurationError(f"official docs provider {provider!r} has a non-string path host")
        if not isinstance(raw_prefixes, Sequence) or isinstance(raw_prefixes, (str, bytes, bytearray)):
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} path prefixes for {raw_host!r} must be a list"
            )
        prefixes: list[str] = []
        for raw_prefix in cast(Sequence[object], raw_prefixes):
            if (
                not isinstance(raw_prefix, str)
                or not raw_prefix.startswith("/")
                or raw_prefix.startswith("//")
                or "?" in raw_prefix
                or "#" in raw_prefix
                or any(character in raw_prefix for character in ("\r", "\n", "\x00", "\\"))
            ):
                raise OfficialDocsConfigurationError(
                    f"official docs provider {provider!r} has an invalid allowed path prefix"
                )
            decoded_prefix = _repeated_unquote(raw_prefix)
            if decoded_prefix != raw_prefix or any(segment in {".", ".."} for segment in raw_prefix.split("/")):
                raise OfficialDocsConfigurationError(
                    f"official docs provider {provider!r} has a non-canonical allowed path prefix"
                )
            prefixes.append(raw_prefix)
        if not prefixes:
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} requires path prefixes for {raw_host!r}"
            )
        result[raw_host] = tuple(prefixes)
    return result


def _validated_documents(
    raw_documents: object,
    *,
    provider: str,
    provider_docs: ProviderDocsEntry,
) -> tuple[OfficialDocEntry, ...]:
    if not isinstance(raw_documents, Sequence) or isinstance(raw_documents, (str, bytes, bytearray)):
        raise OfficialDocsConfigurationError(f"official docs provider {provider!r} documents must be a list")
    documents: list[OfficialDocEntry] = []
    seen_ids: set[str] = set()
    for raw_document in cast(Sequence[object], raw_documents):
        if not isinstance(raw_document, Mapping):
            raise OfficialDocsConfigurationError(f"official docs provider {provider!r} has a non-object document")
        document = cast(Mapping[object, object], raw_document)
        doc_id = document.get("id")
        title = document.get("title")
        url = document.get("url")
        raw_operations = document.get("operations")
        if not isinstance(doc_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", doc_id):
            raise OfficialDocsConfigurationError(f"official docs provider {provider!r} has an invalid document id")
        if doc_id in seen_ids:
            raise OfficialDocsConfigurationError(f"official docs provider {provider!r} repeats document id {doc_id!r}")
        if not isinstance(title, str) or not title:
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} document {doc_id!r} requires title"
            )
        if not isinstance(url, str):
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} document {doc_id!r} requires url"
            )
        checked_url = _validate_official_url(url, provider_docs=provider_docs)
        if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, (str, bytes, bytearray)):
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} document {doc_id!r} operations must be a list"
            )
        operations: list[str] = []
        for operation in cast(Sequence[object], raw_operations):
            if not isinstance(operation, str) or not operation:
                raise OfficialDocsConfigurationError(
                    f"official docs provider {provider!r} document {doc_id!r} has invalid operations"
                )
            operations.append(operation)
        if not operations:
            raise OfficialDocsConfigurationError(
                f"official docs provider {provider!r} document {doc_id!r} has invalid operations"
            )
        documents.append(
            OfficialDocEntry(
                id=doc_id,
                title=title,
                url=checked_url,
                operations=tuple(operations),
            )
        )
        seen_ids.add(doc_id)
    if not documents:
        raise OfficialDocsConfigurationError(f"official docs provider {provider!r} requires documents")
    return tuple(documents)


def _validate_official_url(url: str, *, provider_docs: ProviderDocsEntry) -> str:
    if not url or len(url) > _MAX_URL_LENGTH or any(character in url for character in ("\r", "\n", "\x00")):
        raise OfficialDocsConfigurationError("official documentation URL is invalid")
    without_fragment, _fragment = urldefrag(url)
    parsed = urlsplit(without_fragment)
    host = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError as error:
        raise OfficialDocsConfigurationError("official documentation URL has an invalid port") from error
    if parsed.scheme != "https" or host not in provider_docs.allowed_hosts:
        raise OfficialDocsConfigurationError(
            f"official documentation URL is outside the {provider_docs.provider!r} host allowlist"
        )
    if parsed.username is not None or parsed.password is not None or port not in {None, 443}:
        raise OfficialDocsConfigurationError("official documentation URL cannot contain credentials or a custom port")
    decoded_path = _repeated_unquote(parsed.path).replace("\\", "/")
    if (
        not decoded_path.startswith("/")
        or decoded_path.startswith("//")
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
    ):
        raise OfficialDocsConfigurationError("official documentation URL has a non-canonical path")
    prefixes = provider_docs.allowed_paths.get(host, ())
    if not any(_path_matches_prefix(decoded_path, prefix) for prefix in prefixes):
        raise OfficialDocsConfigurationError(
            f"official documentation URL is outside the {provider_docs.provider!r} path allowlist"
        )
    if len(parsed.query) > _MAX_URL_LENGTH:
        raise OfficialDocsConfigurationError("official documentation URL query is too long")
    return without_fragment


def _path_matches_prefix(path: str, prefix: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(f"{prefix}/")


def _repeated_unquote(value: str) -> str:
    decoded = value
    for _ in range(5):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


class OfficialDocsGateway:
    """Provider-scoped, read-only retrieval of actual official API documentation."""

    def __init__(
        self,
        providers: Iterable[str],
        provider_roles: Mapping[str, str] | None = None,
        *,
        catalog: OfficialDocsCatalog | None = None,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = _MAX_PROVIDER_RESPONSE_BYTES,
        max_text_chars: int = 20_000,
        max_redirects: int = 4,
        max_calls: int | None = None,
        snapshot_cache: OfficialDocsSnapshotCache | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        provider_set = frozenset(providers)
        unknown = provider_set - SUPPORTED_DOC_PROVIDERS
        if not provider_set or unknown:
            raise OfficialDocsConfigurationError("official docs gateway providers must be a non-empty supported subset")
        self._catalog = catalog or load_official_docs_catalog()
        if not provider_set <= set(self._catalog.providers):
            raise OfficialDocsConfigurationError("official docs catalog does not cover every provisioned provider")
        self._providers = provider_set
        self._roles = _validated_roles(provider_roles or {}, provider_set)
        if (
            timeout_seconds <= 0
            or max_response_bytes <= 0
            or max_text_chars <= 0
            or max_redirects < 0
            or (max_calls is not None and (isinstance(max_calls, bool) or max_calls < 1))
        ):
            raise OfficialDocsConfigurationError("official docs gateway limits must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_text_chars = max_text_chars
        self._max_redirects = max_redirects
        self._max_calls = max_calls
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
        self._owns_client = client is None
        self._snapshot_cache = snapshot_cache or OfficialDocsSnapshotCache()
        self._snapshot_cache.bind_catalog(self._catalog)
        self._discovered_urls: dict[str, set[str]] = {provider: set() for provider in provider_set}
        self._trace_records: list[OfficialDocsTraceRecord] = []
        provider_tokens = sorted(provider_set | set(self._roles))
        self._tool_definition: dict[str, object] = {
            "name": PROVIDER_DOCS_TOOL_NAME,
            "description": (
                "Discover and read actual official API documentation for this task's provisioned providers. "
                "This read-only tool fetches only provider-specific official host/path allowlists, follows only "
                "allowlisted redirects, and never exposes twin URLs or credentials. Search the official-doc "
                "index, fetch a doc_id, then follow only URLs returned in that document's links."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": provider_tokens,
                        "description": "Provisioned provider name or task-specific provider role.",
                    },
                    "action": {"type": "string", "enum": ["search", "fetch"]},
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional search terms. For fetch, matching excerpts from the official document are "
                            "returned; omit it to read from the beginning."
                        ),
                        "maxLength": _MAX_QUERY_LENGTH,
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Catalog document ID returned by search. Use either doc_id or url for fetch.",
                        "maxLength": _MAX_DOC_ID_LENGTH,
                    },
                    "url": {
                        "type": "string",
                        "description": (
                            "An official documentation URL previously returned by this tool. It must remain inside "
                            "the selected provider's exact host/path allowlist."
                        ),
                        "maxLength": _MAX_URL_LENGTH,
                    },
                },
                "required": ["provider", "action"],
                "additionalProperties": False,
            },
        }

    @property
    def tool_definition(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(self._tool_definition)))

    @property
    def trace_records(self) -> tuple[OfficialDocsTraceRecord, ...]:
        return tuple(self._trace_records)

    @property
    def cached_document_count(self) -> int:
        return self._snapshot_cache.entry_count

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute(self, tool_input: Mapping[str, object]) -> dict[str, object]:
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        requested_provider = _optional_string(tool_input.get("provider")) or ""
        resolved_provider = self._roles.get(requested_provider, requested_provider) or None
        action = _optional_string(tool_input.get("action"))
        doc_id = _optional_string(tool_input.get("doc_id"))
        requested_url = _optional_string(tool_input.get("url"))
        query = _optional_string(tool_input.get("query"))
        source_url: str | None = requested_url
        final_url: str | None = None
        status_code: int | None = None
        response_bytes = 0
        content_sha256: str | None = None
        truncated = False
        cache_hit = False
        try:
            if self._max_calls is not None and len(self._trace_records) >= self._max_calls:
                raise ValueError(f"provider_docs call limit of {self._max_calls} has been reached")
            unknown_keys = set(tool_input) - _ALLOWED_TOOL_INPUT_KEYS
            if unknown_keys:
                raise ValueError(f"unsupported provider_docs input fields: {', '.join(sorted(unknown_keys))}")
            provider = self._resolve_provider(requested_provider)
            resolved_provider = provider
            checked_action = _validated_action(action)
            checked_query = _validated_query(query)
            provider_docs = self._catalog.providers[provider]
            if checked_action == "search":
                if doc_id is not None or requested_url is not None:
                    raise ValueError("doc_id and url are not accepted for action 'search'")
                documents = _search_documents(provider_docs, checked_query)
                self._discovered_urls[provider].update(cast(str, document["source_url"]) for document in documents)
                payload: dict[str, object] = {
                    "ok": True,
                    "requested_provider": requested_provider,
                    "provider": provider,
                    "action": checked_action,
                    "catalog": _provider_provenance(self._catalog, provider_docs),
                    "documents": documents,
                }
            else:
                document, checked_url = _resolve_fetch_target(
                    doc_id=doc_id,
                    requested_url=requested_url,
                    provider_docs=provider_docs,
                )
                if document is None and checked_url not in self._discovered_urls[provider]:
                    raise ValueError("fetch url was not previously returned by provider_docs for this provider")
                source_url = checked_url
                cache_key = (provider, checked_url)
                fetched, cache_hit = await self._snapshot_cache.get_or_fetch(
                    cache_key,
                    lambda: self._fetch(checked_url, provider_docs),
                )
                self._discovered_urls[provider].update(url for _title, url in fetched.links)
                final_url = fetched.final_url
                status_code = fetched.status_code
                response_bytes = fetched.response_bytes
                content_sha256 = fetched.content_sha256
                truncated = fetched.truncated
                content, excerpted, content_truncated_for_model = _document_content(
                    fetched.text,
                    query=checked_query,
                    max_text_chars=self._max_text_chars,
                )
                payload = {
                    "ok": True,
                    "requested_provider": requested_provider,
                    "provider": provider,
                    "action": checked_action,
                    "document": {
                        "id": document.id if document is not None else None,
                        "title": document.title if document is not None else _title_from_url(checked_url),
                        "content": content,
                        "excerpted_for_query": excerpted,
                        "content_truncated_for_model": content_truncated_for_model,
                        "links": [{"title": title, "url": url} for title, url in fetched.links],
                    },
                    "provenance": {
                        **_provider_provenance(self._catalog, provider_docs),
                        "source_url": fetched.source_url,
                        "final_url": fetched.final_url,
                        "redirect_chain": list(fetched.redirect_chain),
                        "retrieved_at": fetched.retrieved_at,
                        "http_status": fetched.status_code,
                        "content_type": fetched.content_type,
                        "retrieved_content_sha256": fetched.content_sha256,
                        "retrieved_bytes": fetched.response_bytes,
                        "retrieval_truncated": fetched.truncated,
                        "etag": fetched.etag,
                        "last_modified": fetched.last_modified,
                        "cache_hit": cache_hit,
                    },
                }
            self._append_trace(
                started_at=started_at,
                requested_provider=requested_provider,
                provider=resolved_provider,
                action=action,
                doc_id=doc_id,
                source_url=source_url,
                final_url=final_url,
                status_code=status_code,
                latency_ms=_elapsed_ms(started),
                response_bytes=response_bytes,
                content_sha256=content_sha256,
                truncated=truncated,
                cache_hit=cache_hit,
                query_present=bool(query),
                error=None,
            )
            return payload
        except (OfficialDocsConfigurationError, httpx.HTTPError, TypeError, ValueError) as error:
            message = str(error)
            self._append_trace(
                started_at=started_at,
                requested_provider=requested_provider,
                provider=resolved_provider,
                action=action,
                doc_id=doc_id,
                source_url=source_url,
                final_url=final_url,
                status_code=status_code,
                latency_ms=_elapsed_ms(started),
                response_bytes=response_bytes,
                content_sha256=content_sha256,
                truncated=truncated,
                cache_hit=cache_hit,
                query_present=bool(query),
                error=message,
            )
            return {
                "ok": False,
                "requested_provider": requested_provider,
                "provider": resolved_provider,
                "action": action,
                "doc_id": doc_id,
                "url": requested_url,
                "error": message,
            }

    async def _fetch(
        self,
        source_url: str,
        provider_docs: ProviderDocsEntry,
    ) -> _FetchedOfficialDoc:
        current_url = _validate_official_url(source_url, provider_docs=provider_docs)
        redirect_chain: list[str] = []
        response: httpx.Response | None = None
        for redirect_count in range(self._max_redirects + 1):
            request = self._client.build_request(
                "GET",
                current_url,
                headers={
                    "Accept": "text/html,text/plain,text/markdown,application/json,application/yaml;q=0.9",
                    "User-Agent": "arga-twins-benchmark-official-docs/1",
                },
                timeout=self._timeout_seconds,
            )
            response = await self._client.send(request, stream=True, follow_redirects=False)
            if response.status_code not in _REDIRECT_STATUS_CODES:
                break
            location = response.headers.get("location")
            redirect_chain.append(str(response.url))
            await response.aclose()
            if location is None:
                raise ValueError("official documentation redirect omitted Location")
            if redirect_count >= self._max_redirects:
                raise ValueError("official documentation exceeded the redirect limit")
            current_url = _validate_official_url(
                urljoin(current_url, location),
                provider_docs=provider_docs,
            )
        if response is None:
            raise AssertionError("official documentation request did not produce a response")
        try:
            provider_response_limit = _PROVIDER_RESPONSE_BYTES.get(
                provider_docs.provider,
                _DEFAULT_PROVIDER_RESPONSE_BYTES,
            )
            body, truncated = await _read_bounded(
                response,
                min(self._max_response_bytes, provider_response_limit),
            )
        finally:
            await response.aclose()
        if not 200 <= response.status_code < 300:
            raise ValueError(f"official documentation returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "text/plain").split(";", 1)[0].strip().casefold()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"official documentation returned unsupported content type {content_type!r}")
        text, raw_links = _decode_official_content(body, content_type=content_type)
        final_url = _validate_official_url(str(response.url), provider_docs=provider_docs)
        links = _allowlisted_links(
            raw_links,
            text=text,
            base_url=final_url,
            provider_docs=provider_docs,
        )
        return _FetchedOfficialDoc(
            provider=provider_docs.provider,
            body=body,
            text=text,
            links=links,
            source_url=source_url,
            final_url=final_url,
            redirect_chain=tuple((*redirect_chain, final_url)),
            status_code=response.status_code,
            response_bytes=len(body),
            content_sha256=hashlib.sha256(body).hexdigest(),
            content_type=content_type,
            retrieved_at=datetime.now(UTC).isoformat(),
            truncated=truncated,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )

    def write_cache_artifacts(self, root: Path) -> None:
        """Persist exact bounded response bodies and replay metadata for this trial."""

        self._snapshot_cache.write_artifacts(root, catalog=self._catalog)

    def _resolve_provider(self, requested_provider: str) -> str:
        provider = self._roles.get(requested_provider, requested_provider)
        if provider not in self._providers:
            allowed = ", ".join(sorted(self._providers | set(self._roles)))
            raise ValueError(f"provider must be one of: {allowed}")
        return provider

    def _append_trace(self, **values: object) -> None:
        self._trace_records.append(
            OfficialDocsTraceRecord(
                sequence=len(self._trace_records) + 1,
                started_at=cast(str, values["started_at"]),
                requested_provider=cast(str, values["requested_provider"]),
                provider=cast(str | None, values["provider"]),
                action=cast(str | None, values["action"]),
                doc_id=cast(str | None, values["doc_id"]),
                source_url=cast(str | None, values["source_url"]),
                final_url=cast(str | None, values["final_url"]),
                status_code=cast(int | None, values["status_code"]),
                latency_ms=cast(int, values["latency_ms"]),
                response_bytes=cast(int, values["response_bytes"]),
                content_sha256=cast(str | None, values["content_sha256"]),
                truncated=cast(bool, values["truncated"]),
                cache_hit=cast(bool, values["cache_hit"]),
                query_present=cast(bool, values["query_present"]),
                error=cast(str | None, values["error"]),
            )
        )


def _validated_roles(roles: Mapping[str, str], providers: frozenset[str]) -> dict[str, str]:
    validated: dict[str, str] = {}
    for role, provider in roles.items():
        if not role or provider not in providers:
            raise OfficialDocsConfigurationError("official docs provider roles must resolve to provisioned providers")
        if role in providers and role != provider:
            raise OfficialDocsConfigurationError("official docs provider role conflicts with a provisioned provider")
        validated[role] = provider
    return validated


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise OfficialDocsConfigurationError(f"official docs cache {field} must be a non-empty string")
    return value


def _required_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OfficialDocsConfigurationError(f"official docs cache {field} must be an integer")
    return value


def _required_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise OfficialDocsConfigurationError(f"official docs cache {field} must be a boolean")
    return value


def _validated_action(action: str | None) -> str:
    if action not in {"search", "fetch"}:
        raise ValueError("action must be 'search' or 'fetch'")
    return action


def _validated_query(query: str | None) -> str | None:
    if query is None:
        return None
    normalized = " ".join(query.split())
    if not normalized or len(normalized) > _MAX_QUERY_LENGTH:
        raise ValueError(f"query must contain 1 to {_MAX_QUERY_LENGTH} characters")
    return normalized


def _validated_doc_id(doc_id: str | None) -> str:
    if doc_id is None or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", doc_id):
        raise ValueError("fetch requires a valid catalog doc_id")
    return doc_id


def _resolve_fetch_target(
    *,
    doc_id: str | None,
    requested_url: str | None,
    provider_docs: ProviderDocsEntry,
) -> tuple[OfficialDocEntry | None, str]:
    if (doc_id is None) == (requested_url is None):
        raise ValueError("fetch requires exactly one of doc_id or url")
    if doc_id is not None:
        document = _document_by_id(provider_docs, _validated_doc_id(doc_id))
        return document, document.url
    assert requested_url is not None
    return None, _validate_official_url(requested_url, provider_docs=provider_docs)


def _document_by_id(provider_docs: ProviderDocsEntry, doc_id: str) -> OfficialDocEntry:
    for document in provider_docs.documents:
        if document.id == doc_id:
            return document
    raise ValueError(f"doc_id {doc_id!r} is not allowlisted for provider {provider_docs.provider!r}")


def _provider_provenance(catalog: OfficialDocsCatalog, provider_docs: ProviderDocsEntry) -> dict[str, object]:
    return {
        "catalog_schema_version": catalog.schema_version,
        "catalog_reviewed_at": catalog.catalog_reviewed_at,
        "retrieval_policy": catalog.retrieval_policy,
        "official_owner": provider_docs.owner,
        "api_version": provider_docs.api_version,
        "allowed_hosts": sorted(provider_docs.allowed_hosts),
        "allowed_paths": {host: list(prefixes) for host, prefixes in sorted(provider_docs.allowed_paths.items())},
    }


def _catalog_identity(catalog: OfficialDocsCatalog) -> str:
    payload = {
        "schema_version": catalog.schema_version,
        "catalog_reviewed_at": catalog.catalog_reviewed_at,
        "retrieval_policy": catalog.retrieval_policy,
        "providers": {
            provider: {
                "owner": entry.owner,
                "api_version": entry.api_version,
                "allowed_hosts": sorted(entry.allowed_hosts),
                "allowed_paths": {host: list(prefixes) for host, prefixes in sorted(entry.allowed_paths.items())},
                "documents": [
                    {
                        "id": document.id,
                        "title": document.title,
                        "url": document.url,
                        "operations": list(document.operations),
                    }
                    for document in entry.documents
                ],
            }
            for provider, entry in sorted(catalog.providers.items())
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _search_documents(provider_docs: ProviderDocsEntry, query: str | None) -> list[dict[str, object]]:
    terms = query.casefold().split() if query else []
    results: list[dict[str, object]] = []
    for document in provider_docs.documents:
        haystack = " ".join((document.id, document.title, *document.operations)).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        results.append(
            {
                "id": document.id,
                "title": document.title,
                "source_url": document.url,
            }
        )
    return results


def _document_content(
    text: str,
    *,
    query: str | None,
    max_text_chars: int,
) -> tuple[str, bool, bool]:
    if query is None:
        return text[:max_text_chars], False, len(text) > max_text_chars
    lowered = text.casefold()
    positions = [match.start() for term in query.casefold().split() for match in re.finditer(re.escape(term), lowered)]
    if not positions:
        return (
            f"No matching excerpt was found for query {query!r}. "
            "Fetch this target again without query to read the official document.",
            True,
            False,
        )
    ranges: list[tuple[int, int]] = []
    for position in sorted(positions):
        start = max(0, position - 1_200)
        end = min(len(text), position + 2_800)
        if ranges and start <= ranges[-1][1]:
            previous_start, previous_end = ranges[-1]
            ranges[-1] = (previous_start, max(previous_end, end))
        else:
            ranges.append((start, end))
    excerpts: list[str] = []
    for start, end in ranges:
        excerpts.append(text[start:end].strip())
        if sum(len(excerpt) for excerpt in excerpts) >= max_text_chars:
            break
    joined = "\n\n--- matching official-doc excerpt ---\n\n".join(excerpts)
    return joined[:max_text_chars], True, len(joined) > max_text_chars


def _allowlisted_links(
    raw_links: Sequence[tuple[str, str]],
    *,
    text: str,
    base_url: str,
    provider_docs: ProviderDocsEntry,
) -> tuple[tuple[str, str], ...]:
    candidates = list(raw_links)
    candidates.extend(("", match.group(1)) for match in _MARKDOWN_LINK.finditer(text))
    candidates.extend(("", match.group(0).rstrip(".,);]")) for match in _PLAINTEXT_URL.finditer(text))
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, raw_url in candidates:
        try:
            checked_url = _validate_official_url(
                urljoin(base_url, raw_url),
                provider_docs=provider_docs,
            )
        except OfficialDocsConfigurationError:
            continue
        if checked_url in seen or checked_url == base_url:
            continue
        links.append((" ".join(title.split())[:300], checked_url))
        seen.add(checked_url)
        if len(links) >= _MAX_RETURNED_LINKS:
            break
    return tuple(links)


async def _read_bounded(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    consumed = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        remaining = max_bytes - consumed
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return b"".join(chunks), truncated


def _decode_official_content(
    body: bytes,
    *,
    content_type: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    decoded = body.decode("utf-8", errors="replace")
    if content_type == "text/html":
        parser = _OfficialHtmlExtractor()
        parser.feed(decoded)
        parser.close()
        return parser.text(), tuple(parser.links)
    if content_type in {"application/json", "application/ld+json"}:
        try:
            return json.dumps(json.loads(decoded), indent=2, ensure_ascii=False), ()
        except json.JSONDecodeError:
            return decoded, ()
    return decoded, ()


def _title_from_url(url: str) -> str:
    parsed = urlsplit(url)
    leaf = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return leaf.replace("-", " ").replace("_", " ") or parsed.hostname or "official documentation"


def _write_private_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1_000))
