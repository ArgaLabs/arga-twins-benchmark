from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from arga_twins_benchmark.computer_use.proxy import BrowserProxy, allowed_path
from arga_twins_benchmark.computer_use.session import SAMPLES, load_task


@pytest.mark.parametrize("mode", ["pass", "fail", "unsafe", "evidence_gap", "cleanup", "timeout", "drain"])
def test_session_handoff_submission_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Exercise real local HTTP workspaces without provisioning or paid model calls."""
    from arga_twins_benchmark.arga_cli import ProvisionedTwin, TwinRun
    from arga_twins_benchmark.computer_use import session
    from arga_twins_benchmark.evaluation.state_capture import TrustedStateSnapshot
    from arga_twins_benchmark.lifecycle import SavedScenario

    run = TwinRun(
        "synthetic-run",
        "ready",
        {p: ProvisionedTwin(p, "https://synthetic.invalid") for p in load_task("WKS-01")["twins"]},
        True,
        raw={"run_id": "synthetic-run", "status": "ready", "twins": {}},
    )
    cli = AsyncMock()
    cli.__aenter__.return_value = cli
    cli.create_twin_run.return_value = run
    monkeypatch.setattr(session, "SubprocessArgaCli", lambda: cli)
    monkeypatch.setattr(
        session,
        "_save_compiled_scenario",
        AsyncMock(return_value=SavedScenario("WKS-01", "synthetic-scenario", "Sample", "Sample", "synthetic", False)),
    )
    monkeypatch.setattr(session, "_wait_for_twin_run", AsyncMock(return_value=run))
    reference = json.loads((Path(__file__).parent / "fixtures/workspace/wks-01.json").read_text())["baseline-state"]
    snapshot = Mock(spec=TrustedStateSnapshot)
    snapshot.artifact_payload.return_value = reference
    snapshot.providers = reference["providers"]
    capture = AsyncMock(return_value=snapshot)
    capturer = AsyncMock()
    capturer.capture = capture

    def make_capturer(**_: object) -> AsyncMock:
        return capturer

    monkeypatch.setattr(session, "TrustedStateCapturer", make_capturer)
    cleanup = AsyncMock(side_effect=RuntimeError("Synthetic cleanup failure") if mode == "cleanup" else None)
    cleanup.return_value = {"confirmation": {"outcome": "terminal_without_twins"}}
    monkeypatch.setattr(session, "cleanup_twin_run", cleanup)

    def fake_grade(*_: object) -> dict[str, Any]:
        statuses = ["unsafe", "fail"] if mode == "unsafe" else [mode] if mode in {"fail", "evidence_gap"} else []
        return {"assertions": [{"id": str(i), "status": status} for i, status in enumerate(statuses)]}

    monkeypatch.setattr(session, "grade_workspace_attempt", fake_grade)
    write_started = asyncio.Event()
    write_finished = False

    async def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal write_finished
        if request.method == "POST":
            write_started.set()
            await asyncio.sleep(0.2)
            write_finished = True
        return httpx.Response(
            200, text='<html><button id="open-comments">Comments</button></html>', headers={"content-type": "text/html"}
        )

    async def capture_after_drain(*_: object, **__: object) -> Mock:
        if mode == "drain" and capture.await_count > 1:
            assert write_finished, "Final snapshot must follow the accepted browser write"
        return snapshot

    capture.side_effect = capture_after_drain

    def make_proxy(provider: str, access: dict[str, object]) -> BrowserProxy:
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        proxy = BrowserProxy(provider, access, client=client)
        proxy.owns_client = True
        return proxy

    monkeypatch.setattr(session, "BrowserProxy", make_proxy)
    output = tmp_path / "attempt"

    async def exercise() -> None:
        running = asyncio.create_task(
            session.run(
                argparse.Namespace(
                    task="WKS-01",
                    credentials=None,
                    output=output,
                    minutes=0.005 if mode == "timeout" else 1,
                    reset_check=False,
                )
            )
        )
        try:
            for _ in range(100):
                if (output / "candidate.json").exists():
                    break
                if running.done():
                    await running
                await asyncio.sleep(0.05)
            candidate = json.loads((output / "candidate.json").read_text())
            assert set(candidate["workspaces"]) == {"github", "google_docs", "gmail"}
            assert "synthetic.invalid" not in json.dumps(candidate)
            async with httpx.AsyncClient() as client:
                assert (await client.get(candidate["workspaces"]["google_docs"])).status_code == 200
                blocked = await client.post(
                    candidate["tool_endpoint"],
                    json={
                        "name": "provider_api",
                        "arguments": {"provider": "google_docs", "method": "GET", "path": "/admin/state"},
                    },
                )
                assert blocked.status_code == 200
                assert "error" in blocked.json()
                if mode == "timeout":

                    async def slow_report():
                        yield b'{"final_text": "'
                        await asyncio.sleep(0.5)
                        yield b'Late report"}'

                    response = await client.post(
                        candidate["completion_endpoint"],
                        content=slow_report(),
                        headers={"content-type": "application/json"},
                    )
                    assert response.status_code == 410
                else:
                    invalid_payloads: list[Any] = [[], {"name": "provider_api", "arguments": []}]
                    for payload in invalid_payloads:
                        assert (await client.post(candidate["tool_endpoint"], json=payload)).status_code == 400
                    assert (
                        await client.post(candidate["completion_endpoint"], json={"final_text": "x" * 100001})
                    ).status_code == 400
                    assert (
                        await client.post(candidate["tool_endpoint"], content=b"x" * (1024 * 1024 + 1))
                    ).status_code == 413
                    pending_write = None
                    if mode == "drain":
                        pending_write = asyncio.create_task(
                            client.post(candidate["workspaces"]["google_docs"] + "ui/edit")
                        )
                        await asyncio.wait_for(write_started.wait(), 2)
                    response = await client.post(
                        candidate["completion_endpoint"], json={"final_text": "Fixture complete"}
                    )
                    assert response.status_code == 200
                    if pending_write is not None:
                        assert (await pending_write).status_code == 200
            if mode == "cleanup":
                with pytest.raises(RuntimeError, match="Synthetic cleanup"):
                    await running
            else:
                await running
            assert capture.await_count == 2
            cleanup.assert_awaited_once_with("synthetic-run", arga=cli)
            verdict = json.loads((output / "verifier.json").read_text())
            outcome = (
                "infrastructure_invalid"
                if mode in {"cleanup", "timeout", "evidence_gap"}
                else "pass"
                if mode == "drain"
                else mode
            )
            assert verdict["outcome"] == outcome
            assert verdict["reward"] == {"pass": 1, "fail": 0, "unsafe": -1, "infrastructure_invalid": None}[outcome]
            invocation = json.loads((output / "invocation.json").read_text())
            assert invocation["final_text"] == ("" if mode == "timeout" else "Fixture complete")
            assert len(invocation["events"]) == 1
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "path",
    [
        "/admin/state",
        "/%61dmin/state",
        "/%2561dmin/state",
        "/_ui/scenarios/export",
        "/docs",
        "//external.test",
        "/foo/../admin/state",
        "/mcp",
        "/openapi.json",
        "/schema",
        "/schemas",
        "/foo\\admin",
    ],
)
def test_browser_boundary_rejects_control_and_discovery_paths(path: str) -> None:
    assert not allowed_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/issue/ENG-1",
        "/ui/issues/issue-1/comments",
        "/billing/meters",
        "/graphql",
        "/_ui/repos/acme/platform/pull/2/reviewers",
        "/api/chat.postMessage",
    ],
)
def test_browser_boundary_accepts_frontend_and_provider_routes(path: str) -> None:
    assert allowed_path(path)


def test_proxy_shares_upstream_state_but_hides_credentials() -> None:
    async def exercise() -> None:
        seen: list[httpx.Request] = []

        def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<a href="https://twin.example/issue/ENG-1">Issue</a>'
                    '<script>var token="secret-synthetic-key"</script>'
                    "<style data-twin-control-plane>.tcp-panel{display:block}</style>"
                    "<button data-tcp-toggle>Controls</button>"
                    '<aside data-twin-control-plane><a href="/admin/state">State</a></aside>'
                    '<script data-twin-control-plane>fetch("/admin/state")</script>'
                ),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        proxy = BrowserProxy(
            "linear",
            {"base_url": "https://twin.example", "env": {"LINEAR_API_KEY": "secret-synthetic-key"}},
            client=client,
        )
        proxy.origin = "http://local.test"
        async with (
            client,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as browser,
        ):
            response = await browser.post("/ui/issues/one/comments", data={"body": "From browser"})
            assert response.status_code == 200
            assert seen[0].url == "https://twin.example/ui/issues/one/comments"
            assert seen[0].headers["authorization"] == "secret-synthetic-key"
            assert "secret-synthetic-key" not in response.text and "https://twin.example" not in response.text
            assert "http://local.test/issue/ENG-1" in response.text
            assert "Controls" not in response.text and "/admin/state" not in response.text
            assert (await browser.get("/admin/state")).status_code == 403
            assert (await browser.post("/graphql", json={"query": "{__schema{types{name}}}"})).status_code == 403
            assert (await browser.get("/graphql", params={"query": '{__type(name: "Query"){name}}'})).status_code == 403
            assert (
                await browser.post("/graphql", content=b'{"query":"{\\u005f_schema{types{name}}}"}')
            ).status_code == 403
            assert (await browser.post("/graphql", json=[{"query": "{__schema{types{name}}}"}])).status_code == 403
            assert (await browser.post("/ui/issues", headers={"origin": "http://attacker.test"})).status_code == 403
            assert len(seen) == 1
            proxy.accepting = False
            assert (await browser.get("/")).status_code == 410

    asyncio.run(exercise())


def test_streaming_transfer_limits_stop_before_consuming_every_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.exceptions import HTTPException
    from starlette.requests import Request

    from arga_twins_benchmark.computer_use import proxy as module

    async def exercise() -> None:
        consumed = 0

        async def receive():
            nonlocal consumed
            consumed += 1
            return {"type": "http.request", "body": b"x" * 64, "more_body": True}

        request = Request({"type": "http", "headers": []}, receive=receive)
        with pytest.raises(HTTPException) as rejected:
            await module.bounded_body(request, 128)
        assert rejected.value.status_code == 413 and consumed == 3

        returned = 0

        class ResponseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal returned
                for _ in range(100):
                    returned += 1
                    yield b"x" * 64

        upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=ResponseStream()))
        )
        proxy = BrowserProxy("github", {"base_url": "https://twin.example"}, client=upstream)
        proxy.origin = "http://local.test"
        monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 128)
        async with (
            upstream,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as client,
        ):
            assert (await client.get("/large")).status_code == 502
            assert returned == 3

    asyncio.run(exercise())


def test_five_argabench_style_tasks_have_all_modalities_and_exact_seed_files() -> None:
    manifest = json.loads((SAMPLES / "manifest.json").read_text())
    assert len(manifest["tasks"]) == 5
    assert all(t["provenance"]["type"] == "new_argabench_style_task" for t in manifest["tasks"])
    assert {p for task in manifest["tasks"] for p in task["twins"]} == {
        "github",
        "google_docs",
        "google_sheets",
        "gmail",
        "google_calendar",
    }
    for item in manifest["tasks"]:
        task = load_task(item["id"])
        folder = SAMPLES / "tasks" / item["id"].lower()
        assert task["minimum_semantic_steps"] >= 6
        assert "Computer Use" in task["prompt"] and "provider APIs" in task["prompt"]
        assert "through the provisioned provider_api" not in task["prompt"]
        assert task["interaction_modes"] == ["computer_use", "api", "mixed"]
        assert task["seed_config"] == json.loads((folder / "seed_config.json").read_text())
        scenario = json.loads((folder / "scenario.json").read_text())
        assert scenario["seed_config"] == task["seed_config"] and "prompt" not in scenario
        assert scenario["description"] == task["prompt"] and len(scenario["name"]) <= 80


def test_unknown_task_does_not_read_arbitrary_paths() -> None:
    with pytest.raises(ValueError):
        load_task("../../anything")


def test_scenario_hash_matches_seed_and_description() -> None:
    import hashlib

    for path in (SAMPLES / "tasks").glob("*/scenario.json"):
        scenario = json.loads(path.read_text())
        tags = scenario.pop("tags")
        digest = hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert "content-sha256:" + digest in tags
