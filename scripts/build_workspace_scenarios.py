"""Rebuild exact Scenario imports from the checked-in workspace task definitions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "samples/workspace/tasks"
    for path in sorted(root.glob("*/task.json")):
        task = json.loads(path.read_text())
        scenario = {
            "name": f"ArgaBench Workspace: {task['id']} {task['title']}",
            "description": task["prompt"],
            "twins": sorted(task["twins"]),
            "seed_config": task["seed_config"],
        }
        digest = hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scenario["tags"] = ["argabench-workspace-sample-v2", f"content-sha256:{digest}"]
        path.with_name("scenario.json").write_text(json.dumps(scenario, indent=2) + "\n")


if __name__ == "__main__":
    main()
