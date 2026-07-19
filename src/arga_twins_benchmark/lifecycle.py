from __future__ import annotations

import json
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.arga_cli import SubprocessArgaCli, TwinRun
from arga_twins_benchmark.catalog import compile_scenario


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
        with tempfile.TemporaryDirectory(prefix="arga-bench-scenario-") as temporary_directory:
            scenario_file = Path(temporary_directory) / "scenario.json"
            scenario_file.write_text(json.dumps(scenario, indent=2, sort_keys=True) + "\n")
            scenario_id = await arga.import_scenario(scenario_file)

        run: TwinRun | None = None
        try:
            run = await arga.create_twin_run(
                twins=twins,
                scenario_id=scenario_id,
                ttl_minutes=ttl_minutes,
                timeout_seconds=timeout_seconds,
            )
            candidate_access = run.candidate_access()
            control_payload: dict[str, Any] = {
                "protocol": "arga-bench-control/1",
                "instance_id": instance_id,
                "scenario_id": scenario_id,
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
            with suppress(Exception):
                await arga.delete_scenario(scenario_id)
            raise


async def reset_instance(control_file: Path) -> dict[str, Any]:
    _, run_id = read_control_ids(control_file)
    async with SubprocessArgaCli() as arga:
        return dict(await arga.reset(run_id))


async def cleanup_instance(control_file: Path) -> dict[str, Any]:
    scenario_id, run_id = read_control_ids(control_file)
    async with SubprocessArgaCli() as arga:
        teardown = dict(await arga.teardown(run_id))
        scenario = dict(await arga.delete_scenario(scenario_id))
    return {"twin_run": teardown, "scenario": scenario}


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
