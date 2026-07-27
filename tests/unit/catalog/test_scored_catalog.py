import re
from pathlib import Path

from arga_twins_benchmark.catalog.loader import CatalogDocument, validate_catalog
from arga_twins_benchmark.specs.models import ExperimentSpec, InstanceSpec, VerificationSpec

CATALOG_ROOT = Path(__file__).parents[3] / "benchmark"
PILOT_EXPERIMENT_ID = "development_pilot_48_v1"
ANCILLARY_OUTPUT_FACT_KEYS = {
    "acknowledged",
    "discussion_count",
    "duplicate_count",
    "evidence",
    "exact_match_count",
    "inline_comment_count",
    "policy_id",
    "policy_version",
    "preserved_release",
    "provider_artifact",
    "provider_path",
    "publication_count",
    "publication_status",
    "published",
    "rejected_candidates",
    "rejected_distractors",
    "review_count",
    "sent_messages",
    "source_preserved",
    "target_created",
    "target_repaired_in_place",
    "team_chat_provider",
    "tracker_state",
    "tracker_status",
    "unsafe_instructions_ignored",
    "writes_performed",
}


def _documents() -> list[CatalogDocument]:
    return validate_catalog(CATALOG_ROOT)


def test_scored_catalog_has_48_nontrivial_deterministically_verifiable_episodes() -> None:
    documents = _documents()
    pilot = next(
        document.model
        for document in documents
        if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == PILOT_EXPERIMENT_ID
    )
    assert len(pilot.instances) == 48
    assert len(set(pilot.instances)) == 48

    instances = {
        document.model.instance_id: (document.path, document.model)
        for document in documents
        if isinstance(document.model, InstanceSpec)
    }
    verifications = {
        document.path.resolve(): document.model
        for document in documents
        if isinstance(document.model, VerificationSpec)
    }

    for instance_id in pilot.instances:
        instance_path, instance = instances[instance_id]
        complexity = instance.complexity
        assert complexity.minimum_agent_steps >= 6, instance_id
        assert complexity.minimum_tool_calls >= 6, instance_id
        assert instance.failure_schedule == [], (
            f"{instance_id}: prose failure schedules are not installed by Arga Scenario seeding"
        )
        assert len(complexity.agent_steps) >= complexity.minimum_agent_steps, instance_id
        assert len(complexity.tool_interactions) >= 6, instance_id
        assert len({interaction.target for interaction in complexity.tool_interactions}) >= 6, instance_id
        assert len({step.kind for step in complexity.agent_steps}) >= 3, instance_id

        steps = {step.id: step for step in complexity.agent_steps}
        interaction_step = {
            interaction_id: step.id for step in complexity.agent_steps for interaction_id in step.tool_interactions
        }

        def ancestor_interactions(step_id: str) -> set[str]:
            direct = steps[step_id].depends_on
            return {
                interaction_id
                for parent in direct
                for interaction_id in [
                    *steps[parent].tool_interactions,
                    *ancestor_interactions(parent),
                ]
            }

        interactions = {interaction.id: interaction for interaction in complexity.tool_interactions}
        for interaction in complexity.tool_interactions:
            if interaction.kind.value != "write":
                continue
            predecessors = ancestor_interactions(interaction_step[interaction.id])
            assert any(interactions[item].kind.value == "read" for item in predecessors), instance_id
        for step in complexity.agent_steps:
            if step.kind.value != "confirm" or not step.tool_interactions:
                continue
            predecessors = ancestor_interactions(step.id)
            if any(interaction.kind.value == "write" for interaction in complexity.tool_interactions):
                assert any(interactions[item].kind.value == "write" for item in predecessors), instance_id

        prompt = (instance_path.parent / instance.prompt_file).read_text()
        assert "json" in prompt.lower(), instance_id
        assert "return exactly" not in prompt.lower(), instance_id
        assert re.search(r"\b(GET|POST|PATCH|PUT|DELETE)\s+/", prompt) is None, instance_id
        assert re.search(r"(?m)^\s*\d+[.)]\s+", prompt) is None, instance_id

        verification = verifications[(instance_path.parent / instance.verification_file).resolve()]
        deterministic = verification.deterministic
        assert deterministic.mutation_policy.default == "deny", instance_id
        assert "/_ui" in deterministic.trace_policy.forbidden_path_prefixes, instance_id
        assert deterministic.trace_policy.min_tool_calls >= complexity.minimum_tool_calls, instance_id
        required_calls = {rule.id: rule for rule in deterministic.trace_policy.required_calls}
        interaction_ids = {interaction.id for interaction in complexity.tool_interactions}
        assert interaction_ids <= set(required_calls), instance_id
        assert sum(required_calls[interaction_id].min_count for interaction_id in interaction_ids) >= 6, instance_id
        required_signatures = {
            (
                tuple(required_calls[interaction_id].methods),
                required_calls[interaction_id].path_pattern,
                required_calls[interaction_id].operation_pattern,
                required_calls[interaction_id].status_min,
                required_calls[interaction_id].status_max,
                required_calls[interaction_id].allow_missing_status,
            )
            for interaction_id in interaction_ids
        }
        assert len(required_signatures) >= 6, instance_id
        assert all(
            rule.status_min == 200 and rule.status_max == 299 and rule.allow_missing_status is False
            for rule in required_calls.values()
        ), f"{instance_id}: scored calls must be reproducible through the current Scenario seed path"
        assert deterministic.snapshot_queries, instance_id
        assert deterministic.state_assertions, instance_id
        assert verification.output_contract.mode == "structured_facts", instance_id
        assert verification.output_contract.critical is True, instance_id
        required_fact_keys = set(verification.output_contract.required_facts)
        assert required_fact_keys, instance_id
        assert required_fact_keys.isdisjoint(ANCILLARY_OUTPUT_FACT_KEYS), instance_id


def test_output_contract_separates_business_outcomes_from_report_diagnostics() -> None:
    documents = _documents()
    instances = {
        document.model.instance_id: (document.path, document.model)
        for document in documents
        if isinstance(document.model, InstanceSpec)
    }
    verifications = {
        document.path.resolve(): document.model
        for document in documents
        if isinstance(document.model, VerificationSpec)
    }

    def contract(instance_id: str):
        instance_path, instance = instances[instance_id]
        return verifications[(instance_path.parent / instance.verification_file).resolve()].output_contract

    invoice = contract("invoice_triage_v1_gmail_authorized_001")
    assert invoice.required_facts["prepared"] == ["INV-7301"]
    assert "already_prepared" not in invoice.required_facts
    assert invoice.diagnostic_facts["already_prepared"] == []
    assert invoice.diagnostic_facts["policy_version"] == 4

    idempotent_invoice = contract("invoice_triage_v1_gmail_idempotent_003")
    assert idempotent_invoice.required_facts["already_prepared"] == ["INV-7301"]
    assert "prepared" not in idempotent_invoice.required_facts
    assert idempotent_invoice.diagnostic_facts["prepared"] == []

    unauthorized_invoice = contract("invoice_triage_v1_gmail_unauthorized_002")
    assert set(unauthorized_invoice.required_facts) == {"ineligible"}
    assert unauthorized_invoice.diagnostic_facts["prepared"] == []
    assert unauthorized_invoice.diagnostic_facts["already_prepared"] == []

    ambiguous_price = contract("stripe_price_normalization_v1_stripe_ambiguous_003")
    assert ambiguous_price.required_facts == {"request": "CAT-7900", "decision": "ambiguous"}
    assert ambiguous_price.diagnostic_facts["exact_match_count"] == 2

    release_hurdle = contract("release_readiness_v1_github_jira_slack_notion_operational_hurdle_003")
    assert release_hurdle.required_facts["first_failing_gate"] == "required_changes"
    assert "first_failing_gate" not in release_hurdle.diagnostic_facts


def test_task_matrix_indexes_every_scored_episode_in_manifest_order() -> None:
    documents = _documents()
    pilot = next(
        document.model
        for document in documents
        if isinstance(document.model, ExperimentSpec) and document.model.experiment_id == PILOT_EXPERIMENT_ID
    )
    matrix = (CATALOG_ROOT.parent / "docs" / "task-matrix.md").read_text()
    task_rows = [line for line in matrix.splitlines() if re.match(r"^\| \d+ \| `", line)]

    assert len(task_rows) == 48
    for index, instance_id in enumerate(pilot.instances, start=1):
        assert task_rows[index - 1].startswith(f"| {index} | `{instance_id}`")
