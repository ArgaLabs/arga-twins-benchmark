from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "preflight_cross_functional_snapshot_capture.py"
SPEC = importlib.util.spec_from_file_location("cross_functional_snapshot_preflight", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def test_preflight_plan_covers_every_task_and_query_contract() -> None:
    suite: object = json.loads(preflight.SUITE_PATH.read_text(encoding="utf-8"))
    assert isinstance(suite, dict)
    tasks = cast(list[dict[str, Any]], suite["tasks"])

    plan = preflight.preflight_plan(tasks)

    assert len(plan) == 40
    assert len({item["task_id"] for item in plan}) == 40
    assert all(item["query_count"] >= len(item["twins"]) for item in plan)
    assert all(len(item["query_ids"]) == len(set(item["query_ids"])) for item in plan)
    assert sum(int(item["query_count"]) for item in plan) > 160
