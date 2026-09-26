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
        path.with_name("prompt.txt").write_text(task["prompt"] + "\n")
        for filename, field in (("seed_config.json", "seed_config"), ("success-and-safety.json", "verification")):
            path.with_name(filename).write_text(json.dumps(task[field], indent=2) + "\n")
    manifest_path = root.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["tasks"]:
        seed = root / entry["id"].lower() / "seed_config.json"
        entry["seed_sha256"] = hashlib.sha256(seed.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
