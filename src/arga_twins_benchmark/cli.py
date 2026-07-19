from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from arga_twins_benchmark.arga_cli import SubprocessArgaCli
from arga_twins_benchmark.catalog import fingerprint_instance_bundle, validate_catalog, write_compiled_scenario
from arga_twins_benchmark.lifecycle import (
    SavedScenario,
    cleanup_instance,
    provision_instance,
    reset_instance,
    save_experiment_scenarios,
    save_scenario,
)

app = typer.Typer(no_args_is_help=True, help="Arga Twins Benchmark tools")
catalog_app = typer.Typer(no_args_is_help=True, help="Validate and inspect benchmark catalog files")
scenarios_app = typer.Typer(no_args_is_help=True, help="Save reusable benchmark Scenarios through the Arga CLI")
app.add_typer(catalog_app, name="catalog")
app.add_typer(scenarios_app, name="scenarios")


@catalog_app.command("validate")
def catalog_validate(
    path: Annotated[Path, typer.Argument(help="Catalog file or directory")] = Path("benchmark"),
) -> None:
    documents = validate_catalog(path)
    for document in documents:
        typer.echo(f"{document.fingerprint[:12]}  {document.path}")
    typer.echo(f"Validated {len(documents)} catalog documents")


@catalog_app.command("fingerprint")
def catalog_fingerprint(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    typer.echo(fingerprint_instance_bundle(root, instance_id))


@app.command("compile")
def compile_instance(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Compiled Arga Scenario JSON file")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    write_compiled_scenario(root, instance_id, output)
    typer.echo(output)


@scenarios_app.command("save")
def save_instance_scenario(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    saved = asyncio.run(_save_instance_scenario(catalog_root=root, instance_id=instance_id))
    typer.echo(json.dumps(saved.as_dict(), indent=2, sort_keys=True))


@scenarios_app.command("save-experiment")
def save_experiment(
    experiment_id: Annotated[str, typer.Argument(help="Experiment identifier")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    saved = asyncio.run(_save_experiment_scenarios(catalog_root=root, experiment_id=experiment_id))
    payload = {
        "experiment_id": experiment_id,
        "scenarios": [scenario.as_dict() for scenario in saved],
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command("provision")
def provision(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    control_output: Annotated[
        Path,
        typer.Option("--control-output", help="Private lifecycle record; contains control-plane secrets"),
    ],
    candidate_output: Annotated[
        Path,
        typer.Option("--candidate-output", help="Sanitized provider connection file for the agent"),
    ],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
    ttl_minutes: Annotated[int, typer.Option("--ttl", min=1, max=480)] = 60,
    timeout_seconds: Annotated[int, typer.Option("--timeout", min=1)] = 600,
) -> None:
    asyncio.run(
        provision_instance(
            catalog_root=root,
            instance_id=instance_id,
            control_output=control_output,
            candidate_output=candidate_output,
            ttl_minutes=ttl_minutes,
            timeout_seconds=timeout_seconds,
        )
    )
    typer.echo(f"control: {control_output}")
    typer.echo(f"candidate: {candidate_output}")


@app.command("reset")
def reset(
    control_file: Annotated[Path, typer.Argument(help="Private control record from provision")],
) -> None:
    typer.echo(json.dumps(asyncio.run(reset_instance(control_file)), indent=2, sort_keys=True))


@app.command("cleanup")
def cleanup(
    control_file: Annotated[Path, typer.Argument(help="Private control record from provision")],
) -> None:
    typer.echo(json.dumps(asyncio.run(cleanup_instance(control_file)), indent=2, sort_keys=True))


async def _save_instance_scenario(*, catalog_root: Path, instance_id: str) -> SavedScenario:
    async with SubprocessArgaCli() as arga:
        return await save_scenario(arga=arga, catalog_root=catalog_root, instance_id=instance_id)


async def _save_experiment_scenarios(*, catalog_root: Path, experiment_id: str) -> list[SavedScenario]:
    async with SubprocessArgaCli() as arga:
        return await save_experiment_scenarios(
            arga=arga,
            catalog_root=catalog_root,
            experiment_id=experiment_id,
        )


if __name__ == "__main__":
    app()
