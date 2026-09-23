from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from arga_twins_benchmark.agents import InvocationStatus, ModelInvocationResult, ToolExecutor, ToolSchemaInput
from arga_twins_benchmark.computer_use import candidate
from arga_twins_benchmark.computer_use.browser_relay import BrowserRelay
from arga_twins_benchmark.lifecycle import write_private_json


def handoff() -> dict[str, Any]:
    return {
        "task_id": "WKS-01",
        "prompt": "Reconcile the handoff.",
        "resources": {"google_docs": [{"id": "doc-1", "title": "Handoff"}]},
        "tool_endpoint": "http://127.0.0.1:8001/tools",
        "completion_endpoint": "http://127.0.0.1:8001/complete",
        "tools": [{"name": name, "parameters": {"type": "object"}} for name in ("provider_api", "provider_docs")],
        "operator_secret": "MUST_NOT_REACH_MODEL",
        "expected_outcomes": "HIDDEN_EXPECTATIONS",
    }


@pytest.mark.parametrize("status", ["completed", "timed_out"])
def test_candidate_boundaries_trace_and_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: InvocationStatus
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "MUST_NOT_REACH_MODEL" not in request.content.decode()
        assert "HIDDEN_EXPECTATIONS" not in request.content.decode()
        assert "operator_secret" not in payload and "expected_outcomes" not in payload
        if request.url.path == "/tools":
            assert payload == {
                "name": "provider_api",
                "arguments": {"provider": "github", "method": "GET", "path": "/user"},
            }
        elif request.url.path == "/complete":
            assert payload == {"final_text": '{"result_facts":{}}'}
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async def invoke(
        model: str, system: str, prompt: str, tools: ToolSchemaInput, execute: ToolExecutor, **limits: Any
    ):
        assert "MUST_NOT_REACH_MODEL" not in prompt and "HIDDEN_EXPECTATIONS" not in prompt
        assert "doc-1" in prompt and "Reconcile the handoff" in prompt
        assert "http://" not in prompt
        assert (await execute("provider_api", {"provider": "github", "method": "GET", "path": "/user"})) == {"ok": True}
        assert (await execute("admin", {})) == {"error": "Unknown tool"}
        return ModelInvocationResult(
            requested_model=model,
            response_model=model,
            provider="openai",
            final_text='{"result_facts":{}}',
            status=status,
            stop_reason=status,
            system_prompt=system,
            user_prompt=prompt,
        )

    monkeypatch.setattr(candidate, "invoke_model", invoke)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await candidate.run_candidate(
                handoff(), model="gpt-5.6-sol", output=tmp_path / "candidate", client=client
            )

    result = asyncio.run(exercise())
    assert result.status == status
    assert [r.url.path for r in requests] == (["/tools", "/complete"] if status == "completed" else ["/tools"])
    assert json.loads((tmp_path / "candidate/model-invocation.json").read_text())["status"] == status
    assert json.loads((tmp_path / "candidate/adapter.json").read_text())["has_browser_tool"] is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.test/tools",
        "http://localhost:8001/tools",
        "http://127.0.0.1:8001/admin",
        "http://secret@127.0.0.1:8001/tools",
    ],
)
def test_candidate_rejects_unexpected_destinations(tmp_path: Path, endpoint: str):
    payload = handoff()
    payload["tool_endpoint"] = endpoint
    with pytest.raises(ValueError, match="local session portal"):
        asyncio.run(candidate.run_candidate(payload, model="gpt-5.6-sol", output=tmp_path / "candidate"))
    assert not (tmp_path / "candidate").exists()


def test_candidate_requires_one_portal_origin(tmp_path: Path):
    payload = handoff()
    payload["completion_endpoint"] = "http://127.0.0.1:8002/complete"
    with pytest.raises(ValueError, match="share one portal"):
        asyncio.run(candidate.run_candidate(payload, model="gpt-5.6-sol", output=tmp_path / "candidate"))


def test_local_codec_preserves_mime_unicode_and_rejects_invalid_data() -> None:
    message = "To: owner@example.test\r\nSubject: Résumé\r\n\r\nReviewed ✓"
    encoded = candidate.text_codec({"operation": "encode_base64url", "text": message})["text"]
    assert candidate.text_codec({"operation": "decode_base64url", "text": encoded}) == {"text": message}
    assert "error" in candidate.text_codec({"operation": "decode_base64url", "text": "!invalid"})
    assert "error" in candidate.text_codec({"operation": "read_file", "text": "/etc/passwd"})


async def answer_browser(directory: Path, sequence: int, *, wrong_nonce: bool = False) -> dict[str, Any]:
    request_path = directory / f"{sequence:04d}.request.json"
    async with asyncio.timeout(3):
        while not request_path.exists():
            await asyncio.sleep(0.01)
    request = json.loads(request_path.read_text())
    response = {key: request[key] for key in ("session", "id", "nonce")}
    response["observation"] = "1 button Save\n2 textbox Name"
    if wrong_nonce:
        response["nonce"] = "wrong"
    write_private_json(directory / f"{sequence:04d}.response.json", response)
    return request


def test_browser_relay_observe_action_and_stale_element_protection(tmp_path: Path):
    async def exercise():
        relay = BrowserRelay(tmp_path / "relay", {"gmail": "http://127.0.0.1:1234/"})
        first, request = await asyncio.gather(
            relay.execute({"provider": "gmail", "action": "observe"}), answer_browser(relay.directory, 1)
        )
        assert request["arguments"] == {"provider": "gmail", "action": "observe"}
        assert "Save" in first["observation"]
        assert "error" in await relay.execute({"provider": "gmail", "action": "click", "element": 1})
        action = {"provider": "gmail", "action": "click", "element": 1, "observation_id": first["observation_id"]}
        second, request = await asyncio.gather(relay.execute(action), answer_browser(relay.directory, 2))
        assert request["arguments"] == action
        assert second["observation_id"] != first["observation_id"]
        assert "error" in await relay.execute(action)
        assert relay.completed_calls == 2 and relay.calls == 2
        assert (relay.directory / "0001.request.json").stat().st_mode & 0o777 == 0o600
        assert "http://" not in json.dumps(relay.tool)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "workspace",
    [
        "https://gmail.com/",
        "http://localhost:1234/",
        "http://user:password@127.0.0.1:1234/",
        "http://127.0.0.1:1234/?secret=1",
    ],
)
def test_browser_relay_rejects_non_proxy_destinations(tmp_path: Path, workspace: str):
    with pytest.raises(ValueError, match="local candidate proxies"):
        BrowserRelay(tmp_path / "relay", {"gmail": workspace})
    assert not (tmp_path / "relay").exists()


@pytest.mark.parametrize("failure", ["timeout", "mismatched_response"])
def test_browser_relay_preserves_driver_failures_without_replaying_actions(tmp_path: Path, failure: str):
    async def exercise():
        relay = BrowserRelay(tmp_path / "relay", {"gmail": "http://127.0.0.1:1234/"}, timeout_seconds=0.2)
        operation = relay.execute({"provider": "gmail", "action": "observe"})
        if failure == "timeout":
            result = await operation
        else:
            result, _ = await asyncio.gather(operation, answer_browser(relay.directory, 1, wrong_nonce=True))
        assert result["infrastructure_error"] and relay.completed_calls == 0
        assert (relay.directory / "0001.failed.json").exists()
        assert await relay.execute({"provider": "gmail", "action": "observe"}) == result
        assert relay.calls == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("driver_works", [True, False])
def test_candidate_browser_and_api_tools_share_one_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, driver_works: bool
):
    payload = handoff()
    payload["workspaces"] = {"gmail": "http://127.0.0.1:1234/"}
    posted: list[str] = []

    def handler(request: httpx.Request):
        posted.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async def invoke(
        model: str, system: str, prompt: str, tools: ToolSchemaInput, execute: ToolExecutor, **limits: Any
    ) -> ModelInvocationResult:
        assert "browser_ui" in {tool["name"] for tool in cast(list[dict[str, Any]], tools)}
        assert "http://" not in prompt and "MUST_NOT_REACH_MODEL" not in prompt
        assert "Computer Use is encouraged" in system
        operation = execute("browser_ui", {"provider": "gmail", "action": "observe"})
        if driver_works:
            response, _ = await asyncio.gather(operation, answer_browser(tmp_path / "candidate/browser-relay", 1))
            assert "Save" in cast(dict[str, Any], response)["observation"]
        else:
            assert "infrastructure_error" in cast(dict[str, Any], await operation)
        assert await execute(
            "provider_api", {"provider": "gmail", "method": "GET", "path": "/gmail/v1/users/me/profile"}
        ) == {"ok": True}
        return ModelInvocationResult(
            requested_model=model,
            response_model=model,
            provider="openai",
            final_text='{"result_facts":{}}',
            status="completed",
            stop_reason="completed",
            system_prompt=system,
            user_prompt=prompt,
        )

    monkeypatch.setattr(candidate, "invoke_model", invoke)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await candidate.run_candidate(
                payload,
                model="gpt-5.6-sol",
                output=tmp_path / "candidate",
                client=client,
                browser_relay=True,
                browser_timeout_seconds=0.2,
            )

    result = asyncio.run(exercise())
    assert result.status == ("completed" if driver_works else "incomplete")
    assert posted == (["/tools", "/complete"] if driver_works else ["/tools"])
    adapter = json.loads((tmp_path / "candidate/adapter.json").read_text())
    assert adapter["has_browser_tool"] is True
    assert adapter["completed_browser_calls"] == int(driver_works)
