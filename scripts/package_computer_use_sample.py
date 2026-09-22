#!/usr/bin/env python3
"""Package only the five-task sample and required runner source; never account files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"arga_sk_[A-Za-z0-9_-]{40,}|sk-proj-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{25,}")


def package(output: Path) -> None:
    files: dict[str, bytes] = {}
    for folder in ("src", "samples/computer-use"):
        for path in (ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in {".py", ".yaml", ".json", ".md", ".txt"}
                and "__pycache__" not in path.parts
                and not any(p.endswith(".egg-info") for p in path.parts)
            ):
                files[str(path.relative_to(ROOT))] = path.read_bytes()
    for name in ("pyproject.toml", "uv.lock", "tests/test_computer_use_sample.py"):
        files[name] = (ROOT / name).read_bytes()
    files["README.md"] = files["samples/computer-use/README.md"]
    files["fidelity.md"] = files["samples/computer-use/fidelity.md"]
    files["manifest.json"] = files["samples/computer-use/manifest.json"]
    files["quickstart.sh"] = (
        b'#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\n'
        b'exec uv run --extra computer-use python -m arga_twins_benchmark.computer_use.session "$@"\n'
    )
    for name, data in files.items():
        if SECRET.search(data.decode()):
            raise ValueError(f"Possible credential in {name}; value suppressed")
    files["PACKAGE-MANIFEST.json"] = (
        json.dumps(
            {
                "name": "ArgaBench Computer Use + API sample",
                "contains_live_credentials": False,
                "task_count": 5,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
            },
            indent=2,
        )
        + "\n"
    ).encode()
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
