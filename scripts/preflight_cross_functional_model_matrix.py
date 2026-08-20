#!/usr/bin/env python3
"""Validate every requested provider model/effort profile without consuming a Scenario."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.agents import invoke_model
from arga_twins_benchmark.lifecycle import write_private_json

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "benchmark" / "cross_functional_40" / "model_matrix.json"
TOOL_SCHEMA = {
    "name": "adapter_echo",
    "description": "Return the supplied value unchanged.",
    "input_schema": {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def profiles() -> list[dict[str, Any]]:
    payload = json.loads(MATRIX_PATH.read_text())
    raw = payload.get("profiles")
    if not isinstance(raw, list):
        raise ValueError("model matrix profiles must be an array")
    values = [cast(dict[str, Any], item) for item in raw if isinstance(item, dict)]
    if len(values) != 32:
        raise ValueError("expected 32 model profiles")
    return values


async def preflight(profile: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        echo_inputs: list[dict[str, Any]] = []

        async def execute_tool(name: str, arguments: dict[str, Any]) -> object:
            if name != "adapter_echo":
                return {"error": {"type": "UnknownTool"}}
            echo_inputs.append(arguments)
            return {"value": arguments.get("value")}

        result = await invoke_model(
            model_id=profile["model_id"],
            system_prompt=(
                "This is a provider-adapter handshake, not a benchmark task. Call adapter_echo exactly once "
                "with value profile-ok, then reply with only OK."
            ),
            user_prompt="Perform the adapter handshake now.",
            tool_schema=TOOL_SCHEMA,
            execute_tool=execute_tool,
            max_tool_calls=1,
            timeout_seconds=300,
            api_effort=profile["api_effort"],
            thinking=profile["thinking"],
        )
        return {
            "profile_id": profile["id"],
            "provider": profile["provider"],
            "requested_model": profile["model_id"],
            "requested_effort": profile["requested_effort"],
            "api_effort": profile["api_effort"],
            "thinking": profile["thinking"],
            "status": result.status,
            "stop_reason": result.stop_reason,
            "response_model": result.response_model,
            "tool_calls": result.tool_calls,
            "echo_inputs": echo_inputs,
            "usage": result.usage,
            "effective_config": result.config,
            "passed": (
                result.status == "completed" and result.tool_calls == 1 and echo_inputs == [{"value": "profile-ok"}]
            ),
        }


async def async_main(args: argparse.Namespace) -> int:
    started_at = utc_now()
    semaphore = asyncio.Semaphore(args.concurrency)
    selected = profiles()
    if args.profiles:
        requested = {item.strip() for item in args.profiles.split(",") if item.strip()}
        selected = [profile for profile in selected if profile["id"] in requested]
        if {profile["id"] for profile in selected} != requested:
            missing = ", ".join(sorted(requested - {profile["id"] for profile in selected}))
            raise ValueError(f"unknown profiles: {missing}")
    results = await asyncio.gather(*(preflight(profile, semaphore) for profile in selected))
    payload = {
        "protocol": "arga-bench-cross-functional-model-preflight/1",
        "started_at": started_at,
        "finished_at": utc_now(),
        "concurrency": args.concurrency,
        "profile_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "results": results,
    }
    write_private_json(args.output.resolve(), payload)
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return 0 if payload["passed"] == len(selected) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 21), default=20)
    parser.add_argument("--profiles")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
