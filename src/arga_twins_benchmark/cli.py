from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

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
    write_private_json,
)
from arga_twins_benchmark.reporting.repeated_analysis import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    RepeatedAnalysisError,
    analyze_repeated_suite,
    render_repeated_analysis_markdown,
)
from arga_twins_benchmark.reporting.semantic_grader import SemanticGradeError, grade_saved_suite
from arga_twins_benchmark.runner import (
    MODEL_PROFILES,
    ModelProfile,
    TrialPlan,
    load_env_file,
    load_experiment_bundles,
    render_prompt_ledger_markdown,
    run_experiment_matrix,
    run_trial,
    write_prompt_ledger,
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
    arga_candidate_safe_profile: Annotated[
        bool,
        typer.Option(
            "--arga-candidate-safe-profile/--no-arga-candidate-safe-profile",
            help="Opt into the separately deployed Arga CLI/server --candidate-safe profile.",
        ),
    ] = False,
) -> None:
    asyncio.run(
        provision_instance(
            catalog_root=root,
            instance_id=instance_id,
            control_output=control_output,
            candidate_output=candidate_output,
            ttl_minutes=ttl_minutes,
            timeout_seconds=timeout_seconds,
            arga_candidate_safe_profile=arga_candidate_safe_profile,
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


@app.command("prompts")
def prompts(
    experiment_id: Annotated[str, typer.Argument(help="Experiment identifier")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Private JSON prompt ledger")],
    markdown_output: Annotated[
        Path | None,
        typer.Option("--markdown-output", help="Optional reader-friendly Markdown prompt ledger"),
    ] = None,
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    write_prompt_ledger(output, root, experiment_id)
    payload: object = json.loads(output.read_text())
    if not isinstance(payload, dict):
        raise AssertionError("prompt ledger must be an object")
    prompt_payload = cast(dict[str, Any], payload)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_prompt_ledger_markdown(prompt_payload))
        markdown_output.chmod(0o600)
    typer.echo(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "entries": prompt_payload.get("entry_count"),
                "json": str(output),
                "markdown": str(markdown_output) if markdown_output is not None else None,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("run-instance")
def run_instance(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    model: Annotated[str, typer.Option("--model", help="Exact requested model ID")],
    experiment_id: Annotated[
        str,
        typer.Option("--experiment", help="Experiment containing the instance"),
    ] = "development_pilot_48_v1",
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
    output_root: Annotated[Path, typer.Option("--output-root", help="Run artifact root")] = Path("runs"),
    env_file: Annotated[Path | None, typer.Option("--env-file", help="Ignored KEY=VALUE credentials file")] = Path(
        ".env"
    ),
    ttl_minutes: Annotated[int, typer.Option("--ttl", min=1, max=480)] = 60,
    candidate_safe_surface: Annotated[
        bool,
        typer.Option(
            "--candidate-safe-surface/--legacy-candidate-surface",
            help="Use the restricted twin API plus official provider_docs surface (secure default).",
        ),
    ] = True,
    arga_candidate_safe_profile: Annotated[
        bool,
        typer.Option(
            "--arga-candidate-safe-profile/--no-arga-candidate-safe-profile",
            help="Opt into the separately deployed Arga CLI/server --candidate-safe profile.",
        ),
    ] = False,
) -> None:
    if env_file is not None:
        load_env_file(env_file)
    profiles = _select_model_profiles(model)
    if len(profiles) != 1:
        raise typer.BadParameter("run-instance requires exactly one model")
    _, bundles = load_experiment_bundles(root, experiment_id)
    if instance_id not in bundles:
        raise typer.BadParameter(f"{instance_id!r} is not in experiment {experiment_id!r}")
    suite_run_id = f"canary-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    plan = TrialPlan(
        suite_run_id=suite_run_id,
        trial_id=f"{suite_run_id}--{instance_id}--{profiles[0].model_id}",
        repeat=1,
        instance_id=instance_id,
        model=profiles[0],
    )
    result = asyncio.run(
        run_trial(
            catalog_root=root,
            bundle=bundles[instance_id],
            plan=plan,
            output_root=output_root / suite_run_id,
            ttl_minutes=ttl_minutes,
            candidate_safe_surface=candidate_safe_surface,
            arga_candidate_safe_profile=arga_candidate_safe_profile,
        )
    )
    typer.echo(
        json.dumps(
            {
                "trial_id": result.get("trial_id"),
                "instance_id": result.get("instance_id"),
                "model": result.get("model"),
                "response_model": result.get("response_model"),
                "status": result.get("status"),
                "stop_reason": result.get("stop_reason"),
                "tool_calls": result.get("tool_calls"),
                "provider_tool_calls": result.get("provider_tool_calls"),
                "official_docs_tool_calls": result.get("official_docs_tool_calls"),
                "candidate_safe_surface": result.get("candidate_safe_surface"),
                "arga_candidate_safe_profile": result.get("arga_candidate_safe_profile"),
                "cleanup_succeeded": result.get("cleanup_succeeded"),
                "artifact_dir": str(output_root / suite_run_id / "trials" / plan.trial_id),
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("run-matrix")
def run_matrix(
    experiment_id: Annotated[str, typer.Argument(help="Experiment identifier")] = "development_pilot_48_v1",
    models: Annotated[
        str,
        typer.Option("--models", help="Comma-separated exact model IDs; defaults to the preregistered three"),
    ] = ",".join(profile.model_id for profile in MODEL_PROFILES),
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
    output_root: Annotated[Path, typer.Option("--output-root", help="Run artifact root")] = Path("runs"),
    env_file: Annotated[Path | None, typer.Option("--env-file", help="Ignored KEY=VALUE credentials file")] = Path(
        ".env"
    ),
    repeats: Annotated[int, typer.Option("--repeats", min=1, max=20)] = 1,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=16)] = 4,
    ttl_minutes: Annotated[int, typer.Option("--ttl", min=1, max=480)] = 60,
    suite_run_id: Annotated[
        str | None,
        typer.Option("--suite-run-id", help="Resume an existing suite directory with the same manifest"),
    ] = None,
    candidate_safe_surface: Annotated[
        bool,
        typer.Option(
            "--candidate-safe-surface/--legacy-candidate-surface",
            help="Use the restricted twin API plus official provider_docs surface (secure default).",
        ),
    ] = True,
    arga_candidate_safe_profile: Annotated[
        bool,
        typer.Option(
            "--arga-candidate-safe-profile/--no-arga-candidate-safe-profile",
            help="Opt into the separately deployed Arga CLI/server --candidate-safe profile.",
        ),
    ] = False,
) -> None:
    if env_file is not None:
        loaded = load_env_file(env_file)
        typer.echo(f"Loaded credential variables: {', '.join(loaded) if loaded else '(already set)'}", err=True)
    profiles = _select_model_profiles(models)
    summary = asyncio.run(
        run_experiment_matrix(
            catalog_root=root,
            experiment_id=experiment_id,
            output_root=output_root,
            model_profiles=profiles,
            repeats=repeats,
            concurrency=concurrency,
            ttl_minutes=ttl_minutes,
            suite_run_id=suite_run_id,
            candidate_safe_surface=candidate_safe_surface,
            arga_candidate_safe_profile=arga_candidate_safe_profile,
        )
    )
    typer.echo(
        json.dumps(
            {
                key: summary.get(key)
                for key in (
                    "suite_run_id",
                    "trial_count",
                    "terminal_count",
                    "runtime_error_count",
                    "cleanup_failure_count",
                    "trace_output_pass_count",
                    "state_grade_complete",
                    "candidate_safe_surface",
                    "arga_candidate_safe_profile",
                    "official_docs_tool_call_allowance",
                    "completed_at",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("grade-suite")
def grade_suite(
    suite_dir: Annotated[Path, typer.Argument(help="Saved matrix suite directory")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Derived semantic grade JSON; defaults inside the suite"),
    ] = None,
    fail_on_incomplete: Annotated[
        bool,
        typer.Option("--fail-on-incomplete", help="Exit 1 when any trial cannot be graded safely"),
    ] = False,
) -> None:
    try:
        report = grade_saved_suite(suite_dir, catalog_root=root)
    except SemanticGradeError as error:
        raise typer.BadParameter(str(error)) from error
    output_path = output or (suite_dir / "semantic-grade.json")
    write_private_json(output_path, report)
    typer.echo(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "grading_policy",
                    "suite_run_id",
                    "scheduled_trials",
                    "valid_trials",
                    "invalid_infrastructure_trials",
                    "invalid_grader_trials",
                    "passed_trials",
                    "failed_trials",
                    "unsafe_trials",
                    "trials_with_trace_policy_failures",
                    "trials_with_output_diagnostic_failures",
                    "trials_with_redundant_calls",
                    "trials_with_partial_efficiency_analysis",
                    "redundant_call_groups",
                    "flagged_repeat_attempts",
                    "state_grade_complete",
                    "semantic_grade_ready",
                    "suite_integrity_passed",
                    "matrix_fully_evaluable",
                    "scoring_ready",
                    "by_model",
                )
            }
            | {"artifact": str(output_path)},
            indent=2,
            sort_keys=True,
        )
    )
    if fail_on_incomplete and report.get("scoring_ready") is not True:
        raise typer.Exit(code=1)


@app.command("analyze-suite")
def analyze_suite(
    semantic_grade: Annotated[
        Path,
        typer.Argument(help="Completed semantic-grade JSON"),
    ],
    suite_dir: Annotated[
        Path | None,
        typer.Option("--suite-dir", help="Preserved suite directory; defaults to the grade file's parent"),
    ] = None,
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
    json_output: Annotated[
        Path | None,
        typer.Option("--json-output", help="Aggregate machine-readable JSON output"),
    ] = None,
    markdown_output: Annotated[
        Path | None,
        typer.Option("--markdown-output", help="Aggregate reader-facing Markdown output"),
    ] = None,
    bootstrap_seed: Annotated[
        int,
        typer.Option("--bootstrap-seed", help="Fixed task-cluster bootstrap seed"),
    ] = DEFAULT_BOOTSTRAP_SEED,
    bootstrap_resamples: Annotated[
        int,
        typer.Option("--bootstrap-resamples", min=100, max=1_000_000),
    ] = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> None:
    resolved_suite_dir = suite_dir or semantic_grade.parent
    resolved_json_output = json_output or (resolved_suite_dir / "repeated-analysis.json")
    resolved_markdown_output = markdown_output or (resolved_suite_dir / "repeated-analysis.md")
    if resolved_json_output.resolve() == resolved_markdown_output.resolve():
        raise typer.BadParameter("JSON and Markdown outputs must be different files")
    try:
        report = analyze_repeated_suite(
            semantic_grade,
            suite_dir=resolved_suite_dir,
            catalog_root=root,
            bootstrap_seed=bootstrap_seed,
            bootstrap_resamples=bootstrap_resamples,
        )
    except RepeatedAnalysisError as error:
        raise typer.BadParameter(str(error)) from error
    write_private_json(resolved_json_output, report)
    resolved_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_markdown_output.write_text(
        render_repeated_analysis_markdown(report),
        encoding="utf-8",
    )
    resolved_markdown_output.chmod(0o600)
    typer.echo(
        json.dumps(
            {
                "suite_run_id": report.get("suite_run_id"),
                "scheduled_trials": cast(dict[str, Any], report["design"]).get("scheduled_trials"),
                "models": cast(dict[str, Any], report["design"]).get("models"),
                "declared_repeats": cast(dict[str, Any], report["design"]).get("declared_repeats"),
                "json": str(resolved_json_output),
                "markdown": str(resolved_markdown_output),
            },
            indent=2,
            sort_keys=True,
        )
    )


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


def _select_model_profiles(models: str) -> tuple[ModelProfile, ...]:
    requested = {item.strip().lower() for item in models.split(",") if item.strip()}
    aliases = {profile.model_id.lower(): profile for profile in MODEL_PROFILES}
    aliases.update({profile.label.lower(): profile for profile in MODEL_PROFILES})
    unknown = requested - set(aliases)
    if unknown:
        raise typer.BadParameter(f"unknown models: {', '.join(sorted(unknown))}")
    selected = tuple(profile for profile in MODEL_PROFILES if profile in {aliases[item] for item in requested})
    if not selected:
        raise typer.BadParameter("at least one model is required")
    return selected


if __name__ == "__main__":
    app()
