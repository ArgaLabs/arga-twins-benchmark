from __future__ import annotations

from pathlib import Path

from arga_twins_benchmark.catalog import validate_catalog
from arga_twins_benchmark.specs.models import ExperimentSpec, InstanceSpec, TemplateSpec, VerificationSpec

EXPERIMENT_ID = "development_pilot_48_v1"


def _link(repository_root: Path, path: Path, label: str) -> str:
    relative = path.relative_to(repository_root)
    return f"[{label}](../{relative.as_posix()})"


def _result_summary(verification: VerificationSpec) -> str:
    facts = verification.output_contract.required_facts
    result_key = next(
        (key for key in ("decision", "outcome", "disposition", "tracker_action") if key in facts),
        None,
    )
    if result_key is not None:
        result = f"{result_key}={facts[result_key]}"
    elif "prepared" in facts and "already_prepared" in facts:
        result = f"prepared={facts['prepared']}; already_prepared={facts['already_prepared']}"
    else:
        result = "structured result"
    mutations = verification.deterministic.mutation_policy.required
    if not mutations:
        return f"verified no-op; {result}"
    effects = ", ".join(f"{rule.operation} {rule.resource_type} x{rule.min_count}" for rule in mutations)
    return f"{effects}; {result}"


def render_task_matrix(repository_root: Path) -> str:
    catalog_root = repository_root / "benchmark"
    documents = validate_catalog(catalog_root)
    experiment = next(
        document.model
        for document in documents
        if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == EXPERIMENT_ID
    )
    instances = {
        document.model.instance_id: (document.path, document.model)
        for document in documents
        if isinstance(document.model, InstanceSpec)
    }
    templates = {
        document.model.template_id: document.model for document in documents if isinstance(document.model, TemplateSpec)
    }
    verifications = {
        document.path.resolve(): document.model
        for document in documents
        if isinstance(document.model, VerificationSpec)
    }

    lines = [
        "# Development pilot matrix (not a released score set)",
        "",
        (
            "This is the generated inventory for the retained `development_pilot_48_v1` catalog. It is "
            "separate from the active 40-task ArgaBench v1 release. Each row links the exact candidate prompt, "
            "checked-in twin seeds, and executable verification manifest. Counts are authored semantic steps "
            "and candidate-only provider calls, not suggested padding."
        ),
        "",
        "Regenerate after catalog changes with `uv run python scripts/regenerate_task_matrix.py`.",
        "",
        (
            "| # | Task and variant | Prompt | Exact twin seeds and seeded condition | "
            "Steps / calls | Required result | Verifier |"
        ),
        "| ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    for index, instance_id in enumerate(experiment.instances, start=1):
        instance_path, instance = instances[instance_id]
        template = templates[instance.template_id]
        verification_path = (instance_path.parent / instance.verification_file).resolve()
        verification = verifications[verification_path]
        prompt_link = _link(repository_root, instance_path.parent / instance.prompt_file, "prompt")
        seed_links = ", ".join(
            _link(repository_root, instance_path.parent / seed_file, twin)
            for twin, seed_file in sorted(instance.seed_files.items())
        )
        seeded_condition = "; ".join(patch.replace("_", " ") for patch in instance.setup_patches)
        verifier_link = _link(repository_root, verification_path, "manifest")
        title = f"{template.title} - {instance.variant.value.replace('_', ' ')}"
        lines.append(
            f"| {index} | `{instance_id}`<br>{title} | {prompt_link} | {seed_links}<br>{seeded_condition} | "
            f"{instance.complexity.minimum_agent_steps} / {instance.complexity.minimum_tool_calls} | "
            f"{_result_summary(verification)} | {verifier_link} |"
        )

    lines.extend(
        [
            "",
            "The manifest combines canonical final-state assertions, exact before/after mutation rules, a "
            "default-deny mutation policy, provider-host egress checks, critical structured result facts, and a "
            "reference call graph for non-gating trajectory diagnostics. The checked-in conformance audit remains "
            "`leaderboard_ready=false`; these instances must not be described as scored until the gold, negative-"
            "control, semantic-equivalence, reset, and isolation gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    output = repository_root / "docs" / "task-matrix.md"
    output.write_text(render_task_matrix(repository_root))


if __name__ == "__main__":
    main()
