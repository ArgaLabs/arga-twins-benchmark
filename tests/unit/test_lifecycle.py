import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.arga_cli import ArgaCliError, TwinRun
from arga_twins_benchmark.catalog import compile_scenario
from arga_twins_benchmark.lifecycle import (
    cleanup_instance,
    cleanup_payload_proves_inert,
    provision_instance,
    read_control_ids,
    save_experiment_scenarios,
    save_scenario,
    write_private_json,
)


class FakeArgaCli:
    def __init__(self, *, scenarios: Sequence[Mapping[str, Any]] = ()) -> None:
        self.scenarios: list[Mapping[str, Any]] = [dict(scenario) for scenario in scenarios]
        self.listed_tags: list[str] = []
        self.imported_scenarios: list[dict[str, Any]] = []
        self.torn_down_runs: list[str] = []
        self.status_calls: list[str] = []

    async def list_scenarios(self, *, tag: str) -> list[Mapping[str, Any]]:
        self.listed_tags.append(tag)
        return [scenario for scenario in self.scenarios if tag in scenario.get("tags", [])]

    async def import_scenario(self, scenario_file: Path) -> Mapping[str, Any]:
        value: object = json.loads(scenario_file.read_text())
        if not isinstance(value, dict):
            raise AssertionError("expected an imported Scenario object")
        self.imported_scenarios.append(cast(dict[str, Any], value))
        import_number = len(self.imported_scenarios)
        scenario_id = "scenario-imported" if import_number == 1 else f"scenario-imported-{import_number}"
        saved: Mapping[str, Any] = {
            "id": scenario_id,
            **cast(dict[str, Any], value),
            "prompt": None,
            "is_preset": False,
        }
        self.scenarios.append(saved)
        return saved

    async def create_twin_run(
        self,
        *,
        twins: Sequence[str],
        scenario_id: str,
        ttl_minutes: int,
    ) -> TwinRun:
        raise AssertionError("create_twin_run is not expected in these tests")

    async def status(self, run_id: str) -> TwinRun:
        self.status_calls.append(run_id)
        return TwinRun.from_payload({"run_id": run_id, "status": "torn_down", "twins": {}})

    async def reset(self, run_id: str) -> Mapping[str, Any]:
        raise AssertionError("reset is not expected in these tests")

    async def teardown(self, run_id: str) -> Mapping[str, Any]:
        self.torn_down_runs.append(run_id)
        return {"run_id": run_id, "status": "torn_down"}


class MutatingImportArgaCli(FakeArgaCli):
    async def import_scenario(self, scenario_file: Path) -> Mapping[str, Any]:
        saved = dict(await super().import_scenario(scenario_file))
        saved["description"] = "server changed the task"
        return saved


class RacingImportArgaCli(FakeArgaCli):
    async def import_scenario(self, scenario_file: Path) -> Mapping[str, Any]:
        saved = dict(await super().import_scenario(scenario_file))
        self.scenarios.append({**saved, "id": "scenario-imported-concurrently"})
        return saved


class TerminalTeardownArgaCli(FakeArgaCli):
    def __init__(
        self,
        *,
        status: str,
        teardown_error: str = "Cannot teardown run in status: cancelled",
        status_error: ArgaCliError | None = None,
        twins: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.run_status = status
        self.teardown_error = teardown_error
        self.status_error = status_error
        self.twins = dict(twins or {})

    async def teardown(self, run_id: str) -> Mapping[str, Any]:
        self.torn_down_runs.append(run_id)
        raise ArgaCliError(self.teardown_error)

    async def status(self, run_id: str) -> TwinRun:
        self.status_calls.append(run_id)
        if self.status_error is not None:
            raise self.status_error
        payload: Mapping[str, Any] = {
            "run_id": run_id,
            "status": self.run_status,
            "twins": self.twins,
        }
        return TwinRun.from_payload(payload)


class ProvisioningArgaCli(FakeArgaCli):
    def __init__(
        self,
        *,
        scenarios: Sequence[Mapping[str, Any]],
        control_path: Path,
        statuses: Sequence[TwinRun],
    ) -> None:
        super().__init__(scenarios=scenarios)
        self.control_path = control_path
        self.statuses = list(statuses)
        self.created_runs: list[dict[str, object]] = []

    async def __aenter__(self) -> "ProvisioningArgaCli":
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None

    async def create_twin_run(
        self,
        *,
        twins: Sequence[str],
        scenario_id: str,
        ttl_minutes: int,
    ) -> TwinRun:
        self.created_runs.append(
            {
                "twins": list(twins),
                "scenario_id": scenario_id,
                "ttl_minutes": ttl_minutes,
            }
        )
        return TwinRun.from_payload(
            {
                "run_id": "run-queued",
                "status": "queued",
                "twins": {},
                "is_public": True,
            }
        )

    async def status(self, run_id: str) -> TwinRun:
        assert run_id == "run-queued"
        assert self.control_path.is_file(), "run ID must be durable before the first status poll"
        return self.statuses.pop(0)


class CleanupPollingArgaCli(FakeArgaCli):
    def __init__(self, *, statuses: Sequence[TwinRun]) -> None:
        super().__init__()
        self.statuses = list(statuses)

    async def teardown(self, run_id: str) -> Mapping[str, Any]:
        self.torn_down_runs.append(run_id)
        return {"run_id": run_id, "status": "cleaning_up"}

    async def status(self, run_id: str) -> TwinRun:
        self.status_calls.append(run_id)
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]


def test_private_json_is_mode_0600(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "control.json"
    write_private_json(output, {"secret": "value"})

    assert json.loads(output.read_text()) == {"secret": "value"}
    assert oct(output.stat().st_mode & 0o777) == "0o600"


def test_control_ids_are_required(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    assert read_control_ids(control) == ("scenario-1", "run-1")

    control.write_text("{}")
    with pytest.raises(ValueError, match="scenario_id"):
        read_control_ids(control)


def test_save_scenario_imports_when_content_hash_is_new() -> None:
    arga = FakeArgaCli()
    expected = compile_scenario(Path("benchmark"), "blocking_code_review_v1_github_clean_001")

    saved = asyncio.run(
        save_scenario(
            arga=arga,
            catalog_root=Path("benchmark"),
            instance_id="blocking_code_review_v1_github_clean_001",
        )
    )

    assert saved.scenario_id == "scenario-imported"
    assert saved.created is True
    content_tag = next(tag for tag in expected["tags"] if tag.startswith("content-sha256:"))
    assert arga.listed_tags == [content_tag, content_tag]
    assert arga.imported_scenarios == [expected]


def test_save_scenario_reuses_one_exact_content_hash_match() -> None:
    expected = compile_scenario(Path("benchmark"), "blocking_code_review_v1_github_clean_001")
    arga = FakeArgaCli(scenarios=[{"id": "scenario-existing", **expected}])

    saved = asyncio.run(
        save_scenario(
            arga=arga,
            catalog_root=Path("benchmark"),
            instance_id="blocking_code_review_v1_github_clean_001",
        )
    )

    assert saved.scenario_id == "scenario-existing"
    assert saved.created is False
    assert arga.imported_scenarios == []


def test_save_scenario_rejects_duplicate_content_hash_matches() -> None:
    expected = compile_scenario(Path("benchmark"), "blocking_code_review_v1_github_clean_001")
    arga = FakeArgaCli(
        scenarios=[
            {"id": "scenario-one", **expected},
            {"id": "scenario-two", **expected},
        ]
    )

    with pytest.raises(ArgaCliError, match="multiple|duplicate"):
        asyncio.run(
            save_scenario(
                arga=arga,
                catalog_root=Path("benchmark"),
                instance_id="blocking_code_review_v1_github_clean_001",
            )
        )

    assert arga.imported_scenarios == []


def test_save_scenario_rejects_content_hash_match_with_different_payload() -> None:
    expected = compile_scenario(Path("benchmark"), "blocking_code_review_v1_github_clean_001")
    mismatched = {"id": "scenario-conflict", **expected, "description": "different task"}
    arga = FakeArgaCli(scenarios=[mismatched])

    with pytest.raises(ArgaCliError, match="match|mismatch|conflict"):
        asyncio.run(
            save_scenario(
                arga=arga,
                catalog_root=Path("benchmark"),
                instance_id="blocking_code_review_v1_github_clean_001",
            )
        )

    assert arga.imported_scenarios == []


def test_save_scenario_rejects_a_server_modified_import() -> None:
    arga = MutatingImportArgaCli()

    with pytest.raises(ArgaCliError, match="conflicting fields: description"):
        asyncio.run(
            save_scenario(
                arga=arga,
                catalog_root=Path("benchmark"),
                instance_id="blocking_code_review_v1_github_clean_001",
            )
        )


def test_save_scenario_detects_a_concurrent_duplicate_import() -> None:
    arga = RacingImportArgaCli()

    with pytest.raises(ArgaCliError, match="concurrent save|duplicate"):
        asyncio.run(
            save_scenario(
                arga=arga,
                catalog_root=Path("benchmark"),
                instance_id="blocking_code_review_v1_github_clean_001",
            )
        )


def test_save_experiment_saves_all_forty_eight_scenarios() -> None:
    arga = FakeArgaCli()

    saved = asyncio.run(
        save_experiment_scenarios(
            arga=arga,
            catalog_root=Path("benchmark"),
            experiment_id="development_pilot_48_v1",
        )
    )

    assert len(saved) == len(arga.imported_scenarios) == 48
    assert len({scenario.instance_id for scenario in saved}) == 48
    assert len({scenario.scenario_id for scenario in saved}) == 48
    assert all(scenario.created for scenario in saved)


def test_provision_persists_run_id_before_polling_and_exports_only_ready_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_id = "blocking_code_review_v1_github_clean_001"
    compiled = compile_scenario(Path("benchmark"), instance_id)
    control = tmp_path / "control.json"
    candidate = tmp_path / "candidate.json"
    ready = TwinRun.from_payload(
        {
            "run_id": "run-queued",
            "status": "ready",
            "twins": {
                "github": {
                    "base_url": "https://pub-github.example",
                    "admin_url": "https://admin-github.example",
                    "env_vars": {"GITHUB_TOKEN": "provider-token"},
                }
            },
            "is_public": True,
        }
    )
    arga = ProvisioningArgaCli(
        scenarios=[{"id": "scenario-existing", **compiled}],
        control_path=control,
        statuses=[ready],
    )
    monkeypatch.setattr("arga_twins_benchmark.lifecycle.SubprocessArgaCli", lambda: arga)
    monkeypatch.setattr("arga_twins_benchmark.lifecycle.TWIN_RUN_POLL_INTERVAL_SECONDS", 0)

    asyncio.run(
        provision_instance(
            catalog_root=Path("benchmark"),
            instance_id=instance_id,
            control_output=control,
            candidate_output=candidate,
            ttl_minutes=60,
            timeout_seconds=10,
        )
    )

    assert arga.created_runs == [
        {
            "twins": ["github"],
            "scenario_id": "scenario-existing",
            "ttl_minutes": 60,
        }
    ]
    assert json.loads(control.read_text())["run_id"] == "run-queued"
    assert json.loads(control.read_text())["twin_run"]["status"] == "ready"
    assert json.loads(candidate.read_text())["provider_access"] == {
        "github": {
            "base_url": "https://pub-github.example",
            "env": {"GITHUB_TOKEN": "provider-token"},
        }
    }


def test_cleanup_tears_down_run_but_preserves_saved_scenario(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = FakeArgaCli()

    result = asyncio.run(cleanup_instance(control, arga=arga))

    assert result["twin_run"] == {
        "run_id": "run-1",
        "status": "torn_down",
        "twins": {},
    }
    assert result["teardown"] == {
        "outcome": "accepted",
        "response": {"run_id": "run-1", "status": "torn_down"},
    }
    assert result["confirmation"]["outcome"] == "terminal_without_twins"
    assert arga.torn_down_runs == ["run-1"]
    assert arga.status_calls == ["run-1"]


def test_cleanup_polls_from_cleaning_up_until_terminal_without_twins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = CleanupPollingArgaCli(
        statuses=[
            TwinRun.from_payload({"run_id": "run-1", "status": "cleaning_up", "twins": {}}),
            TwinRun.from_payload({"run_id": "run-1", "status": "cancelled", "twins": {}}),
        ]
    )
    monkeypatch.setattr(
        "arga_twins_benchmark.lifecycle.TWIN_RUN_POLL_INTERVAL_SECONDS",
        0,
    )

    result = asyncio.run(cleanup_instance(control, arga=arga))

    assert result["twin_run"]["status"] == "cancelled"
    assert result["teardown"]["response"]["status"] == "cleaning_up"
    assert result["confirmation"]["confirmed_status"] == "cancelled"
    assert arga.status_calls == ["run-1", "run-1"]


def test_cleanup_rejects_status_payload_for_a_different_run(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = CleanupPollingArgaCli(
        statuses=[TwinRun.from_payload({"run_id": "run-other", "status": "cancelled", "twins": {}})]
    )

    with pytest.raises(
        ArgaCliError,
        match="returned run 'run-other'.*cleanup for 'run-1'",
    ):
        asyncio.run(cleanup_instance(control, arga=arga))


def test_cleanup_artifact_must_match_expected_run_id() -> None:
    payload: dict[str, Any] = {
        "twin_run": {
            "run_id": "run-other",
            "status": "cancelled",
            "twins": {},
        }
    }

    assert cleanup_payload_proves_inert(payload)
    assert not cleanup_payload_proves_inert(
        payload,
        expected_run_id="run-expected",
    )


def test_cleanup_fails_when_cleaning_up_never_becomes_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = CleanupPollingArgaCli(
        statuses=[TwinRun.from_payload({"run_id": "run-1", "status": "cleaning_up", "twins": {}})]
    )
    monkeypatch.setattr(
        "arga_twins_benchmark.lifecycle.CLEANUP_CONFIRM_TIMEOUT_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        "arga_twins_benchmark.lifecycle.TWIN_RUN_POLL_INTERVAL_SECONDS",
        0.001,
    )

    with pytest.raises(
        ArgaCliError,
        match="timed out confirming cleanup.*last status was 'cleaning_up'",
    ):
        asyncio.run(cleanup_instance(control, arga=arga))

    assert arga.torn_down_runs == ["run-1"]
    assert arga.status_calls


@pytest.mark.parametrize("terminal_status", ["cancelled", "expired", "failed", "torn_down"])
def test_cleanup_confirms_already_clean_terminal_run_via_cli_status(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = TerminalTeardownArgaCli(status=terminal_status)

    result = asyncio.run(cleanup_instance(control, arga=arga))

    assert result["twin_run"] == {
        "run_id": "run-1",
        "status": terminal_status,
        "twins": {},
    }
    assert result["teardown"] == {
        "outcome": "already_terminal",
        "response": None,
    }
    assert result["confirmation"] == {
        "outcome": "terminal_without_twins",
        "confirmed_status": terminal_status,
    }
    assert arga.torn_down_runs == ["run-1"]
    assert arga.status_calls == ["run-1"]


def test_cleanup_does_not_swallow_ambiguous_teardown_error(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = TerminalTeardownArgaCli(
        status="cancelled",
        teardown_error="Arga CLI exited with 1: connection reset",
    )

    with pytest.raises(ArgaCliError, match="connection reset"):
        asyncio.run(cleanup_instance(control, arga=arga))

    assert arga.status_calls == []


def test_cleanup_rejects_failed_status_when_twins_still_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = TerminalTeardownArgaCli(
        status="failed",
        twins={"github": {"base_url": "https://pub-github.example"}},
    )
    monkeypatch.setattr(
        "arga_twins_benchmark.lifecycle.CLEANUP_CONFIRM_TIMEOUT_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        "arga_twins_benchmark.lifecycle.TWIN_RUN_POLL_INTERVAL_SECONDS",
        0.001,
    )

    with pytest.raises(ArgaCliError, match="timed out confirming cleanup.*last status was 'failed'"):
        asyncio.run(cleanup_instance(control, arga=arga))

    assert arga.status_calls


def test_cleanup_does_not_swallow_status_lookup_error(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    arga = TerminalTeardownArgaCli(
        status="cancelled",
        status_error=ArgaCliError("status lookup timed out"),
    )

    with pytest.raises(ArgaCliError, match="could not confirm cleanup.*status lookup timed out"):
        asyncio.run(cleanup_instance(control, arga=arga))

    assert arga.status_calls == ["run-1"]
