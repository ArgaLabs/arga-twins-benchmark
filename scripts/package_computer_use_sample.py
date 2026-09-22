#!/usr/bin/env python3
"""Build the operator-only five-task kit; never distribute it to candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(
    r"(?<![A-Za-z0-9])(?:arga_sk_[A-Za-z0-9_-]{40,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{30,})"
)


def sample_tree_sha256() -> str:
    """Bind release evidence to the source, fixtures, contracts and dependencies."""
    names: list[str] = []
    for folder in ("src", "samples/workspace", "tests/fixtures/workspace"):
        names.extend(
            str(path.relative_to(ROOT))
            for path in (ROOT / folder).rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix in {".py", ".yaml", ".json", ".md", ".txt", ".png", ".jpg", ".webp"}
            and "__pycache__" not in path.parts
            and path.name != "readiness.json"
            and not any(part.endswith(".egg-info") for part in path.parts)
        )
    names.extend(
        (
            "pyproject.toml",
            "uv.lock",
            "tests/test_computer_use_sample.py",
            "tests/test_workspace_outcomes.py",
            "tests/test_workspace_candidate.py",
            "scripts/package_computer_use_sample.py",
            "scripts/build_workspace_scenarios.py",
        )
    )
    digest = hashlib.sha256()
    for name in sorted(set(names)):
        digest.update(name.encode() + b"\0" + hashlib.sha256((ROOT / name).read_bytes()).digest())
    return digest.hexdigest()


def package_inputs() -> dict[str, bytes]:
    """Use the same input set for packaging and credential-scan evidence."""
    files: dict[str, bytes] = {}
    for folder in ("src", "samples/workspace", "tests/fixtures/workspace"):
        for path in (ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in {".py", ".yaml", ".json", ".md", ".txt", ".png", ".jpg", ".webp"}
                and "__pycache__" not in path.parts
                and not any(p.endswith(".egg-info") for p in path.parts)
            ):
                files[str(path.relative_to(ROOT))] = path.read_bytes()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "tests/test_computer_use_sample.py",
        "tests/test_workspace_outcomes.py",
        "tests/test_workspace_candidate.py",
        "scripts/package_computer_use_sample.py",
        "scripts/build_workspace_scenarios.py",
    ):
        files[name] = (ROOT / name).read_bytes()
    files["README.md"] = files["samples/workspace/README.md"]
    files["fidelity.md"] = files["samples/workspace/fidelity.md"]
    files["manifest.json"] = files["samples/workspace/manifest.json"]
    files["quickstart.sh"] = (
        b'#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\n'
        b'exec uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session "$@"\n'
    )
    return files


def package(output: Path) -> None:
    readiness = json.loads((ROOT / "samples/workspace/readiness.json").read_text())
    checks = readiness.get("checks", [])
    required = (
        {
            "frontend." + provider
            for provider in (
                "github",
                "google_calendar",
                "gmail",
                "google_sheets",
                "google_docs",
                "notion",
                "linear",
                "stripe",
            )
        }
        | {f"rollout.WKS-{i:02}" for i in range(1, 6)}
        | {"deployment", "package"}
    )
    if (
        readiness.get("release_allowed") is not True
        or {check.get("id") for check in checks} != required
        or len(checks) != len(required)
        or any(check.get("status") != "verified" or not check.get("evidence") for check in checks)
    ):
        raise ValueError(
            "Release blocked: all eight frontend audits, five hosted rollouts, "
            "deployment and packaging must have verified evidence"
        )
    if readiness.get("sample_tree_sha256") != sample_tree_sha256():
        raise ValueError("Release blocked: evidence does not match the current sample source")
    evidence_files: dict[str, bytes] = {}
    evidence_root = (ROOT / "samples/workspace/evidence").resolve()
    for check in checks:
        for evidence in check["evidence"]:
            path = (ROOT / evidence["path"]).resolve()
            if (
                not path.is_relative_to(evidence_root)
                or not path.is_file()
                or path.is_symlink()
                or path.suffix not in {".json", ".md", ".txt", ".png", ".jpg", ".webp"}
            ):
                raise ValueError(f"Missing release evidence for {check['id']}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                raise ValueError(f"Stale release evidence for {check['id']}")
            evidence_files[str(path.relative_to(ROOT))] = path.read_bytes()
    files = {**package_inputs(), **evidence_files}
    for name, data in files.items():
        if SECRET.search(data.decode(errors="ignore")):
            raise ValueError(f"Possible credential in {name}; value suppressed")
    files["PACKAGE-MANIFEST.json"] = (
        json.dumps(
            {
                "name": "ArgaBench Computer Use + API sample",
                "audience": "benchmark_operator_only",
                "candidate_handoff": "candidate.json and the local workspace/tool/completion URLs only",
                "contains_live_credentials": False,
                "task_count": len(json.loads(files["manifest.json"])["tasks"]),
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
            },
            indent=2,
        )
        + "\n"
    ).encode()
    # Never let an output path overwrite any reviewed source or evidence input.
    inputs = {(ROOT / name).resolve() for name in files}
    destinations = (output.resolve(), output.with_suffix(output.suffix + ".sha256").resolve())
    if any(path in inputs for path in destinations):
        raise ValueError("Package output overlaps a source or evidence input")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo("argabench-computer-use-sample/" + name)
            entry.create_system = 3
            entry.external_attr = (0o100755 if name == "quickstart.sh" else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n")
    print(f"{output} ({output.stat().st_size:,} bytes; {len(files)} files)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/argabench-computer-use-sample.zip")
    package(parser.parse_args().output)
