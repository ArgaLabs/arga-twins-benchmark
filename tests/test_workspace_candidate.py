from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from arga_twins_benchmark.agents import InvocationStatus, ModelInvocationResult, ToolExecutor, ToolSchemaInput
from arga_twins_benchmark.computer_use import candidate


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
