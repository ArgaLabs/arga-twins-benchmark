from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.arga_cli import ArgaCli, ArgaCliError, SubprocessArgaCli, TwinRun
from arga_twins_benchmark.catalog import compile_scenario, validate_catalog
from arga_twins_benchmark.specs.models import ExperimentSpec

CONTENT_HASH_TAG_PREFIX = "content-sha256:"


@dataclass(frozen=True)
class SavedScenario:
    instance_id: str
    scenario_id: str
    name: str
    description: str
    content_sha256: str
    created: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "instance_id": self.instance_id,
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "content_sha256": self.content_sha256,
            "created": self.created,
        }


async def save_scenario(
    *,
    arga: ArgaCli,
    catalog_root: Path,
    instance_id: str,
) -> SavedScenario:
    """Save one compiled benchmark Scenario, or reuse its exact hash match."""

    scenario = compile_scenario(catalog_root, instance_id)
    return await _save_compiled_scenario(arga=arga, instance_id=instance_id, scenario=scenario)


async def _save_compiled_scenario(
    *,
    arga: ArgaCli,
    instance_id: str,
    scenario: dict[str, Any],
) -> SavedScenario:
    content_tag, content_sha256 = _compiled_content_identity(scenario)
    matches = await arga.list_scenarios(tag=content_tag)
    if len(matches) > 1:
        scenario_ids = [match.get("id") for match in matches]
        raise ArgaCliError(
            f"multiple saved Scenarios have benchmark content tag {content_tag!r}: {scenario_ids}; "
            "remove or retag duplicates before continuing"
        )

    if matches:
        scenario_id = _validate_saved_scenario(matches[0], scenario, content_tag)
        created = False
    else:
        with tempfile.TemporaryDirectory(prefix="arga-bench-scenario-") as temporary_directory:
            scenario_file = Path(temporary_directory) / "scenario.json"
            scenario_file.write_text(json.dumps(scenario, indent=2, sort_keys=True) + "\n")
            imported = await arga.import_scenario(scenario_file)
        scenario_id = _validate_saved_scenario(imported, scenario, content_tag)
        persisted_matches = await arga.list_scenarios(tag=content_tag)
        if len(persisted_matches) != 1:
            persisted_ids = [match.get("id") for match in persisted_matches]
            raise ArgaCliError(
                f"expected one saved Scenario after importing {scenario_id!r} with content tag {content_tag!r}, "
                f"found {len(persisted_matches)}: {persisted_ids}; a concurrent save may have created a duplicate"
            )
        persisted_id = _validate_saved_scenario(persisted_matches[0], scenario, content_tag)
        if persisted_id != scenario_id:
            raise ArgaCliError(
                f"Scenario import returned {scenario_id!r}, but content tag {content_tag!r} resolved to "
                f"{persisted_id!r}"
            )
        created = True

    name = scenario.get("name")
    description = scenario.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise AssertionError("compiled Scenario is missing its name or task description")
    return SavedScenario(
        instance_id=instance_id,
        scenario_id=scenario_id,
        name=name,
        description=description,
        content_sha256=content_sha256,
        created=created,
    )


async def save_experiment_scenarios(
    *,
    arga: ArgaCli,
    catalog_root: Path,
    experiment_id: str,
) -> list[SavedScenario]:
    """Save all Scenario fixtures in an experiment, in manifest order."""

    documents = validate_catalog(catalog_root)
    experiment = next(
        (
            document.model
            for document in documents
            if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == experiment_id
        ),
        None,
    )
    if experiment is None:
        raise ValueError(f"unknown experiment {experiment_id!r}")

    saved: list[SavedScenario] = []
    for instance_id in experiment.instances:
        saved.append(await save_scenario(arga=arga, catalog_root=catalog_root, instance_id=instance_id))
    return saved


async def provision_instance(
    *,
    catalog_root: Path,
    instance_id: str,
    control_output: Path,
    candidate_output: Path,
    ttl_minutes: int,
    timeout_seconds: int,
) -> None:
    scenario = compile_scenario(catalog_root, instance_id)
    twins_value = scenario.get("twins")
    if not isinstance(twins_value, list):
        raise AssertionError("compiled scenario has invalid twins")
    twin_items = cast(list[object], twins_value)
    if not all(isinstance(twin, str) for twin in twin_items):
        raise AssertionError("compiled scenario has invalid twins")
    twins = cast(list[str], twin_items)

    async with SubprocessArgaCli() as arga:
        saved_scenario = await _save_compiled_scenario(
            arga=arga,
            instance_id=instance_id,
            scenario=scenario,
        )

        run: TwinRun | None = None
        try:
            run = await arga.create_twin_run(
                twins=twins,
                scenario_id=saved_scenario.scenario_id,
                ttl_minutes=ttl_minutes,
                timeout_seconds=timeout_seconds,
            )
            candidate_access = run.candidate_access()
            control_payload: dict[str, Any] = {
                "protocol": "arga-bench-control/1",
                "instance_id": instance_id,
                "scenario_id": saved_scenario.scenario_id,
                "scenario_created": saved_scenario.created,
                "scenario_content_sha256": saved_scenario.content_sha256,
                "run_id": run.run_id,
                "twin_run": dict(run.raw),
            }
            candidate_payload: dict[str, Any] = {
                "protocol": "arga-bench-candidate-access/1",
                "provider_access": candidate_access,
            }
            write_private_json(control_output, control_payload)
            write_private_json(candidate_output, candidate_payload)
        except BaseException:
            if run is not None:
                with suppress(Exception):
                    await arga.teardown(run.run_id)
            raise


async def reset_instance(control_file: Path) -> dict[str, Any]:
    _, run_id = read_control_ids(control_file)
    async with SubprocessArgaCli() as arga:
        return dict(await arga.reset(run_id))


async def cleanup_instance(control_file: Path, *, arga: ArgaCli | None = None) -> dict[str, Any]:
    _, run_id = read_control_ids(control_file)
    if arga is not None:
        return {"twin_run": dict(await arga.teardown(run_id))}
    async with SubprocessArgaCli() as cli:
        return {"twin_run": dict(await cli.teardown(run_id))}


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def read_control_ids(path: Path) -> tuple[str, str]:
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("control file must contain a JSON object")
    payload = cast(dict[str, object], value)
    scenario_id = payload.get("scenario_id")
    run_id = payload.get("run_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("control file is missing scenario_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("control file is missing run_id")
    return scenario_id, run_id


def _compiled_content_identity(scenario: dict[str, Any]) -> tuple[str, str]:
    tags = scenario.get("tags")
    if not isinstance(tags, list):
        raise AssertionError("compiled Scenario is missing tags")
    content_tags = [
        tag for tag in cast(list[object], tags) if isinstance(tag, str) and tag.startswith(CONTENT_HASH_TAG_PREFIX)
    ]
    if len(content_tags) != 1:
        raise AssertionError("compiled Scenario must contain exactly one content hash tag")
    content_tag = content_tags[0]
    digest = content_tag.removeprefix(CONTENT_HASH_TAG_PREFIX)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AssertionError("compiled Scenario content hash tag must contain a lowercase SHA-256 digest")
    return content_tag, digest


def _validate_saved_scenario(
    saved: Mapping[str, Any],
    expected: dict[str, Any],
    content_tag: str,
) -> str:
    scenario_id = saved.get("id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ArgaCliError(f"saved Scenario matching {content_tag!r} has no non-empty id")

    mismatches = [
        field
        for field in ("name", "description", "twins", "seed_config", "tags")
        if saved.get(field) != expected.get(field)
    ]
    if saved.get("prompt") is not None:
        mismatches.append("prompt")
    if saved.get("is_preset", False) is not False:
        mismatches.append("is_preset")
    if mismatches:
        raise ArgaCliError(
            f"saved Scenario {scenario_id!r} matches {content_tag!r} but has conflicting fields: "
            f"{', '.join(mismatches)}"
        )
    return scenario_id
