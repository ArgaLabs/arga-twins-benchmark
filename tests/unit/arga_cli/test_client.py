import asyncio
import json
import sys
from pathlib import Path

import pytest

from arga_twins_benchmark.arga_cli import ArgaCliError, SubprocessArgaCli, TwinRun


def test_command_uses_configured_executable_and_api_url() -> None:
    client = SubprocessArgaCli(executable=("uv", "run", "arga"), api_url="https://arga.example")
    assert client.command("twin-runs", "status", "run-1", "--json") == (
        "uv",
        "run",
        "arga",
        "twin-runs",
        "status",
        "run-1",
        "--json",
    )


def test_list_scenarios_filters_by_content_hash_tag_and_accepts_json_array(tmp_path: Path) -> None:
    executable = tmp_path / "fake_arga.py"
    executable.write_text(
        """\
import json
import sys

expected = [
    "test-runner",
    "scenarios",
    "list",
    "--api-url",
    "https://arga.example",
    "--tag",
    "content-sha256:abc123",
    "--json",
]
if sys.argv[1:] != expected:
    raise SystemExit(f"unexpected arguments: {sys.argv[1:]!r}")
print(json.dumps([{"id": "scenario-1", "name": "Saved benchmark task"}]))
"""
    )
    client = SubprocessArgaCli(executable=(sys.executable, str(executable)), api_url="https://arga.example")

    scenarios = asyncio.run(client.list_scenarios(tag="content-sha256:abc123"))

    assert scenarios == [{"id": "scenario-1", "name": "Saved benchmark task"}]


def test_import_scenario_returns_the_full_saved_record(tmp_path: Path) -> None:
    executable = tmp_path / "fake_arga.py"
    executable.write_text(
        """\
import json
import sys

scenario_file = sys.argv[sys.argv.index("--file") + 1]
payload = json.loads(open(scenario_file).read())
print(json.dumps({"id": "scenario-1", **payload, "prompt": None, "is_preset": False}))
"""
    )
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "name": "Saved benchmark task",
                "description": "Do the task",
                "twins": ["github"],
                "seed_config": {"github": {"repositories": []}},
                "tags": ["arga-bench", "content-sha256:abc123"],
            }
        )
    )
    client = SubprocessArgaCli(executable=(sys.executable, str(executable)), api_url="https://arga.example")

    saved = asyncio.run(client.import_scenario(scenario_file))

    assert saved["id"] == "scenario-1"
    assert saved["description"] == "Do the task"
    assert saved["seed_config"] == {"github": {"repositories": []}}
    assert saved["prompt"] is None


def test_create_twin_run_returns_immediate_queued_run_without_waiting(tmp_path: Path) -> None:
    executable = tmp_path / "fake_arga.py"
    executable.write_text(
        """\
import json
import sys

expected = [
    "twin-runs",
    "create",
    "--api-url",
    "https://arga.example",
    "--twins",
    "github,slack",
    "--scenario-id",
    "scenario-1",
    "--ttl",
    "60",
    "--json",
]
if sys.argv[1:] != expected:
    raise SystemExit(f"unexpected arguments: {sys.argv[1:]!r}")
print(json.dumps({"run_id": "run-1"}))
"""
    )
    client = SubprocessArgaCli(
        executable=(sys.executable, str(executable)),
        api_url="https://arga.example",
    )

    run = asyncio.run(
        client.create_twin_run(
            twins=["slack", "github"],
            scenario_id="scenario-1",
            ttl_minutes=60,
        )
    )

    assert run.run_id == "run-1"
    assert run.status == "queued"


def test_create_twin_run_adds_candidate_safe_only_when_feature_gate_is_enabled(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake_arga.py"
    executable.write_text(
        """\
import json
import sys

if "--candidate-safe" not in sys.argv[1:]:
    raise SystemExit(f"missing candidate-safe flag: {sys.argv[1:]!r}")
print(json.dumps({"run_id": "run-safe"}))
"""
    )
    client = SubprocessArgaCli(
        executable=(sys.executable, str(executable)),
        api_url="https://arga.example",
    )

    run = asyncio.run(
        client.create_twin_run(
            twins=["github"],
            scenario_id="scenario-1",
            ttl_minutes=60,
            candidate_safe=True,
        )
    )

    assert run.run_id == "run-safe"


def test_supplied_api_key_is_scoped_to_temporary_cli_home() -> None:
    client = SubprocessArgaCli(executable=("arga",), api_key="secret-test-key")
    temporary_home = client.credential_home
    assert temporary_home is not None
    config = temporary_home / ".config/arga/config.json"
    assert config.read_text() == '{"api_key": "secret-test-key"}\n'
    assert oct(config.stat().st_mode & 0o777) == "0o600"

    client.close()
    assert not temporary_home.exists()


def test_candidate_access_excludes_control_plane_values() -> None:
    run = TwinRun.from_payload(
        {
            "run_id": "run-1",
            "status": "ready",
            "is_public": True,
            "proxy_token": "do-not-leak",
            "twins": {
                "github": {
                    "base_url": "https://pub-github.example",
                    "admin_url": "https://admin-github.example",
                    "env_vars": {
                        "GITHUB_TOKEN": "provider-token",
                        "GITHUB_BAD_TOKEN": "do-not-leak",
                        "GITHUB_ADMIN_URL": "https://admin-github.example",
                        "ARGA_PROXY_TOKEN": "do-not-leak",
                    },
                }
            },
        }
    )

    assert run.candidate_access() == {
        "github": {
            "base_url": "https://pub-github.example",
            "env": {"GITHUB_TOKEN": "provider-token"},
        }
    }


def test_private_run_cannot_be_exported_to_candidate() -> None:
    run = TwinRun.from_payload(
        {
            "run_id": "run-1",
            "status": "ready",
            "is_public": False,
            "twins": {"github": {"base_url": "https://private-github.example"}},
        }
    )
    with pytest.raises(ArgaCliError, match="data-plane gateway"):
        run.candidate_access()
