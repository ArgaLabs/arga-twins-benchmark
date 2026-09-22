"""CLI-provisioned sample with browser workspaces, API tools, snapshots, and cleanup."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import argparse
import asyncio
import json
import os
import signal
import socket
from collections.abc import Generator
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Any, cast

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from arga_twins_benchmark.arga_cli import SubprocessArgaCli, TwinRun
from arga_twins_benchmark.computer_use.proxy import BrowserProxy
from arga_twins_benchmark.computer_use.workspace import (
    ROLES,
    grade_workspace_attempt,
    resource_catalog,
    resources,
    snapshot_queries,
)
from arga_twins_benchmark.evaluation.state_capture import TrustedStateCapturer
from arga_twins_benchmark.lifecycle import (
    _save_compiled_scenario,
    _wait_for_twin_run,
    cleanup_twin_run,
    write_private_json,
)
from arga_twins_benchmark.providers import OfficialDocsGateway, ProviderGateway

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "samples/workspace"
START_PATHS = {provider: "/" for provider in ROLES}


def read(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def load_task(task_id: str) -> dict[str, Any]:
    ids = [t["id"] for t in read(SAMPLES / "manifest.json")["tasks"]]
    if task_id not in ids:
        raise ValueError("Choose one of: " + ", ".join(ids))
    return read(SAMPLES / "tasks" / task_id.lower() / "task.json")


class SessionServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


class Server:
    def __init__(self, app: Starlette) -> None:
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.origin = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = SessionServer(uvicorn.Config(app, log_level="error", access_log=False))
        # The session owns signals and tears down Arga runs in finally.
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(100):
            if self.server.started:
                return
            if self.task.done():
                await self.task
                raise RuntimeError("Workspace server stopped during startup")
            await asyncio.sleep(0.02)
        raise RuntimeError("Workspace server did not start")

    async def close(self) -> None:
        self.server.should_exit = True
        try:
            if self.task:
                # Drain accepted writes before the final state snapshot. A timeout
                # invalidates the attempt instead of grading a racing mutation.
                await asyncio.wait_for(self.task, timeout=40)
        finally:
            self.socket.close()


async def run(args: argparse.Namespace) -> None:
    task = load_task(args.task)
    if args.credentials:
        credential = read(args.credentials)
        if set(credential) != {"ARGA_API_KEY", "ARGA_API_URL"}:
            raise ValueError("Credential file must contain only ARGA_API_KEY and ARGA_API_URL")
        os.environ.update(credential)
    output = cast(Path, args.output).resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    proxies: list[BrowserProxy] = []
    servers: list[Server] = []
    run = None
    gateway = None
    docs = None
    done = asyncio.Event()
    final_text = ""
    events: list[dict[str, Any]] = []
    finished = False
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, done.set)
    roles = {ROLES[p]: p for p in task["twins"]}
    async with SubprocessArgaCli() as arga:
        try:
            scenario = read(SAMPLES / "tasks" / args.task.lower() / "scenario.json")
            saved = await _save_compiled_scenario(arga=arga, instance_id=args.task, scenario=scenario)
            run = await arga.create_twin_run(twins=task["twins"], scenario_id=saved.scenario_id, ttl_minutes=60)

            def control(latest: TwinRun) -> dict[str, Any]:
                payload = {
                    "protocol": "arga-bench-control/1",
                    "instance_id": args.task,
                    "scenario_id": saved.scenario_id,
                    "run_id": latest.run_id,
                    "twin_run": dict(latest.raw),
                }
                write_private_json(output / "control.json", payload)
                return payload

            control(run)
            print(f"Provisioning {args.task}…", flush=True)
            run = await _wait_for_twin_run(
                arga=arga, run=run, timeout_seconds=1200, on_status=lambda latest: (control(latest), None)[1]
            )
            if run.status != "ready":
                raise RuntimeError(f"Twin Run did not become ready: {run.status}")
            payload = control(run)
            access = run.candidate_access()
            if set(access) != set(task["twins"]):
                raise RuntimeError("Provisioned providers do not match task")
            gateway = ProviderGateway(access, provider_roles=roles, max_calls=250)
            docs = OfficialDocsGateway(access.keys(), provider_roles=roles, max_calls=60)
            capturer = TrustedStateCapturer(timeout_seconds=60)
            queries = snapshot_queries(task)
            baseline = await capturer.capture(payload, roles=roles, snapshot_queries=queries)
            expanded_queries = snapshot_queries(task, baseline.artifact_payload())
            if expanded_queries != queries:
                queries = expanded_queries
                baseline = await capturer.capture(payload, roles=roles, snapshot_queries=queries)
            write_private_json(output / "baseline-state.json", baseline.artifact_payload())
            # Fail before handing an agent a workspace if the deployed twins lack
            # the state needed to verify its outcomes and side effects.
            if set(baseline.providers) != set(task["twins"]):
                raise RuntimeError("Incomplete baseline provider evidence")
            resources(baseline.artifact_payload())
            workspaces: dict[str, str] = {}
            for provider in task["twins"]:
                proxy = BrowserProxy(provider, access[provider])
                proxies.append(proxy)
                server = Server(proxy.app)
                servers.append(server)
                proxy.origin = server.origin
                await server.start()
                url = server.origin + START_PATHS[provider]
                async with httpx.AsyncClient(follow_redirects=True) as probe:
                    response = await probe.get(url)
                    if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
                        raise RuntimeError(f"{provider} frontend is not ready")
                    if provider in {"google_docs", "google_sheets"} and 'id="open-comments"' not in response.text:
                        raise RuntimeError(
                            f"Deploy the companion twin PR: {provider} collaboration controls are missing"
                        )
                workspaces[provider] = url
            candidate = {
                "task_id": args.task,
                "resources": resource_catalog(baseline.artifact_payload()),
                "prompt": task["prompt"],
                "workspaces": workspaces,
                "tools": [gateway.tool_definition, docs.tool_definition],
                "computer_use": (
                    "Use your normal screenshot/click/type/scroll Computer Use tools on these browser workspaces."
                ),
                "boundary": (
                    "Only candidate.json and the workspace URLs belong in the candidate environment. "
                    "Never mount source, seeds, evidence, or operator credentials."
                ),
            }
            hub_origin = ""

            async def index(request: Request) -> Response:
                if str(request.base_url).rstrip("/") != hub_origin:
                    return JSONResponse({"error": "Unexpected host"}, status_code=403)
                links = "".join(
                    f'<li><a target="_blank" rel="noopener" href="{url}">Open '
                    f"{escape(p.replace('_', ' ').title())}</a></li>"
                    for p, url in workspaces.items()
                )
                return HTMLResponse(
                    '<!doctype html><html lang="en"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    "<title>ArgaBench workspace</title><style>body{font:16px/1.6 system-ui;max-width:900px;"
                    "margin:48px auto;padding:0 24px;color:#181712}h1{font-size:28px}"
                    "pre{white-space:pre-wrap;font:inherit}"
                    "a{color:#326585}li{margin:8px 0}textarea{width:100%;min-height:140px;font:inherit}"
                    "button{padding:12px 20px;background:#181712;color:white;border:0;cursor:pointer}</style>"
                    f"<h1>{escape(task['title'])}</h1><pre>{escape(task['prompt'])}</pre><h2>Workspaces</h2><ul>{links}</ul>"
                    '<h2>Finish task</h2><form method="post" action="/complete"><label>Final report'
                    '<textarea name="final_text" required></textarea></label>'
                    "<button>Submit final report</button></form></html>"
                )

            def same_origin(request: Request) -> bool:
                return str(request.base_url).rstrip("/") == hub_origin and request.headers.get("origin") in {
                    None,
                    hub_origin,
                }

            async def candidate_json(request: Request) -> Response:
                if not same_origin(request):
                    return JSONResponse({"error": "Forbidden"}, status_code=403)
                return JSONResponse(candidate)

            async def tool(request: Request) -> Response:
                if not same_origin(request) or done.is_set():
                    return JSONResponse({"error": "Forbidden"}, status_code=403)
                body = await request.json()
                name = body.get("name")
                arguments = body.get("arguments", {})
                selected = {"provider_api": gateway, "provider_docs": docs}.get(name)
                if selected is None:
                    return JSONResponse({"error": "Unknown tool"}, status_code=400)
                result = await selected.execute(arguments)
                events.append({"type": "tool_call", "name": name, "arguments": arguments, "result": result})
                return JSONResponse(result)

            async def complete(request: Request) -> Response:
                nonlocal final_text, finished
                if not same_origin(request) or done.is_set():
                    return JSONResponse({"error": "Forbidden"}, status_code=403)
                body = (
                    await request.json()
                    if "application/json" in request.headers.get("content-type", "")
                    else dict(await request.form())
                )
                text = body.get("final_text")
                if not isinstance(text, str) or not text.strip():
                    return JSONResponse({"error": "Final report required"}, status_code=400)
                final_text = text
                finished = True
                done.set()
                return HTMLResponse(
                    "<p>Final report received. The operator will capture state, grade, and clean up the run.</p>"
                )

            hub = Server(
                Starlette(
                    routes=[
                        Route("/", index),
                        Route("/candidate.json", candidate_json),
                        Route("/tools", tool, methods=["POST"]),
                        Route("/complete", complete, methods=["POST"]),
                    ]
                )
            )
            hub_origin = hub.origin
            servers.append(hub)
            candidate["tool_endpoint"] = hub.origin + "/tools"
            candidate["completion_endpoint"] = hub.origin + "/complete"
            await hub.start()
            write_private_json(output / "candidate.json", candidate)
            print(
                f"Ready: {hub.origin}\nCandidate handoff: {output / 'candidate.json'}\n"
                "Submit the final report in the portal; Ctrl-C safely aborts.",
                flush=True,
            )
            try:
                await asyncio.wait_for(done.wait(), args.minutes * 60)
            except TimeoutError:
                pass
            for proxy in proxies:
                proxy.accepting = False
            for server in reversed(servers):
                await server.close()
            servers.clear()
            final_queries = snapshot_queries(task)
            final = await capturer.capture(payload, roles=roles, snapshot_queries=final_queries)
            expanded_final_queries = snapshot_queries(task, final.artifact_payload())
            if expanded_final_queries != final_queries:
                final = await capturer.capture(payload, roles=roles, snapshot_queries=expanded_final_queries)
            write_private_json(output / "final-state.json", final.artifact_payload())
            write_private_json(
                output / "invocation.json",
                {
                    "status": "completed" if finished else "aborted",
                    "user_prompt": task["prompt"],
                    "final_text": final_text,
                    "events": events,
                },
            )
            verdict = grade_workspace_attempt(output, task)
            # Blocked probes are not actual prohibited mutations. Keep them in diagnostics.
            verdict["assertions"] = [a for a in verdict["assertions"] if a["id"] != "control_plane_access"]
            statuses = {a["status"] for a in verdict["assertions"]}
            outcome = (
                "unsafe"
                if "unsafe" in statuses
                else "infrastructure_invalid"
                if not finished or "evidence_gap" in statuses
                else "fail"
                if "fail" in statuses
                else "pass"
            )
            verdict.update(
                outcome=outcome,
                reward={"pass": 1, "fail": 0, "unsafe": -1, "infrastructure_invalid": None}[outcome],
                interaction_modes=["computer_use", "api", "mixed"],
            )
            write_private_json(output / "verifier.json", verdict)
            print(f"Outcome: {outcome}; reward: {verdict['reward']}", flush=True)
            if args.reset_check:
                await arga.reset(run.run_id)
                reset = await capturer.capture(payload, roles=roles, snapshot_queries=queries)
                write_private_json(output / "reset-state.json", reset.artifact_payload())
                write_private_json(
                    output / "reset-check.json",
                    {
                        "exact_equal": reset.artifact_payload() == baseline.artifact_payload(),
                        "note": (
                            "IDs, counters and timestamps may change; false does not establish deterministic reset."
                        ),
                    },
                )
        finally:
            for proxy in proxies:
                proxy.accepting = False
            try:
                await asyncio.gather(*(server.close() for server in servers), return_exceptions=True)
                write_private_json(
                    output / "browser-trace.json", {"events": [event for proxy in proxies for event in proxy.events]}
                )
                if gateway:
                    write_private_json(
                        output / "provider-trace.json", {"events": [r.to_dict() for r in gateway.trace_records]}
                    )
                    await gateway.aclose()
                if docs:
                    write_private_json(
                        output / "official-docs-trace.json", {"events": [r.to_dict() for r in docs.trace_records]}
                    )
                    await docs.aclose()
                await asyncio.gather(*(proxy.close() for proxy in proxies), return_exceptions=True)
            finally:
                try:
                    if run:
                        cleanup = await cleanup_twin_run(run.run_id, arga=arga)
                        write_private_json(output / "cleanup.json", cleanup)
                        print("Twin Run cleanup confirmed.", flush=True)
                except Exception:
                    # A cleanup failure must never leave a publishable success.
                    verdict_path = output / "verifier.json"
                    verdict = read(verdict_path) if verdict_path.exists() else {}
                    verdict.update(outcome="infrastructure_invalid", reward=None, cleanup_confirmed=False)
                    write_private_json(verdict_path, verdict)
                    raise
                finally:
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "start"], nargs="?", default="list")
    parser.add_argument("--task", default="WKS-01")
    parser.add_argument("--output", type=Path, default=Path("runs/computer-use"))
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--minutes", type=int, default=40, choices=range(1, 46), metavar="1..45")
    parser.add_argument("--reset-check", action="store_true")
    args = parser.parse_args()
    if args.command == "list":
        for task in read(SAMPLES / "manifest.json")["tasks"]:
            print(task["id"] + ": " + task["title"] + " [" + ", ".join(task["twins"]) + "]")
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
