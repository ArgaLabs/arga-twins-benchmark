from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build_argabench_40.py"
SUITE_PATH = ROOT / "benchmark" / "argabench_40" / "suite.json"
SCENARIO_ROOT = SUITE_PATH.parent / "scenarios"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("argabench_40_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_argabench_40_suite_is_exact_and_reproducible() -> None:
    builder = _load_builder()
    suite = json.loads(SUITE_PATH.read_text())
    bundles = suite["tasks"]

    builder.validate_bundles(bundles)
    assert suite["task_count"] == 40
    assert Counter(bundle["domain"] for bundle in bundles) == builder.EXPECTED_DOMAINS
    assert len({bundle["id"] for bundle in bundles}) == 40

    for bundle in bundles:
        compiled_path = SCENARIO_ROOT / f"{bundle['id'].lower()}.json"
        assert json.loads(compiled_path.read_text()) == builder.scenario_payload(bundle)


def test_argabench_40_scenarios_are_exact_seed_only() -> None:
    builder = _load_builder()
    suite = json.loads(SUITE_PATH.read_text())

    for bundle in suite["tasks"]:
        payload = json.loads((SCENARIO_ROOT / f"{bundle['id'].lower()}.json").read_text())
        assert "prompt" not in payload
        assert set(payload["twins"]) == set(payload["seed_config"])
        assert all(isinstance(seed, dict) and seed for seed in payload["seed_config"].values())
        assert "arga-bench" in payload["tags"]
        assert "suite:argabench-40-v1" in payload["tags"]
        assert len(bundle["required_steps"]) >= 8
        assert any(
            outcome["id"] == "protected_candidate_mutation" and outcome["mutation_count"] == 0
            for outcome in bundle["verification"]["forbidden_outcomes"]
        )
        seed_text = "\n".join(builder.seed_strings(payload["seed_config"]))
        lowered_seed = seed_text.lower()
        assert bundle["id"].lower() not in lowered_seed
        assert not any(term in lowered_seed for term in builder.SEED_GUIDANCE_BLOCKLIST)
        primary_description = bundle["verification"]["required_outcomes"][0].get("description")
        assert not isinstance(primary_description, str) or primary_description not in seed_text
        assert bundle["prompt"].split("\n\n", 1)[1] not in seed_text
        verification_text = json.dumps(bundle["verification"], sort_keys=True).lower()
        assert bundle["id"].lower() not in verification_text
        structured = next(
            outcome for outcome in bundle["verification"]["required_outcomes"] if outcome["id"] == "structured_result"
        )
        assert "task_id" not in structured["facts"]
        assert all(bundle["id"].lower() not in step["purpose"].lower() for step in bundle["required_steps"])


def test_hardened_environments_have_multiple_plausible_business_records() -> None:
    suite = json.loads(SUITE_PATH.read_text())

    def record_count(provider: str, seed: dict) -> int:
        if provider == "slack":
            return sum(len(channel.get("messages", [])) for channel in seed["channels"])
        if provider == "jira":
            return sum(len(project.get("issues", [])) for project in seed["projects"])
        if provider == "github":
            return sum(len(repo.get("issues", [])) + len(repo.get("prs", [])) for repo in seed["repos"])
        if provider == "linear":
            return len(seed["issues"])
        if provider == "notion":
            return len(seed["pages"])
        if provider == "gmail":
            return len(seed["messages"])
        if provider == "google_drive":
            return sum(len(folder.get("files", [])) for folder in seed["folders"])
        if provider == "google_calendar":
            return sum(len(calendar.get("events", [])) for calendar in seed["calendars"])
        if provider == "hubspot":
            return sum(len(seed.get(key, [])) for key in ("contacts", "companies", "deals", "tickets"))
        if provider == "salesforce":
            return sum(len(seed.get(key, [])) for key in ("accounts", "contacts", "records"))
        if provider == "linkedin":
            return len(seed["posts"])
        if provider == "stripe":
            return sum(len(seed.get(key, [])) for key in ("customers", "products", "meters"))
        raise AssertionError(provider)

    for bundle in suite["tasks"]:
        for provider, seed in bundle["seed_config"].items():
            assert record_count(provider, seed) >= 3, f"{bundle['id']} {provider} is under-seeded"


def test_hardening_facts_are_observable_across_multiple_providers() -> None:
    builder = _load_builder()
    hardening = __import__("argabench_40_hardening").HARDENING_PROFILES
    suite = json.loads(SUITE_PATH.read_text())

    assert set(hardening) == {bundle["id"] for bundle in suite["tasks"]}
    for bundle in suite["tasks"]:
        profile = hardening[bundle["id"]]
        evidence_providers = set()
        for provider, seed in bundle["seed_config"].items():
            seed_text = "\n".join(builder.seed_strings(seed)).lower()
            if any(str(value).lower() in seed_text for value in profile["facts"].values()):
                evidence_providers.add(provider)
        assert len(evidence_providers) >= 2, f"{bundle['id']} evidence is not split across systems"
        assert len(profile["protected"]) >= 1


def test_it03_and_it06_seed_the_exact_github_and_drive_assets_named_by_evidence() -> None:
    suite = json.loads(SUITE_PATH.read_text())
    tasks = {bundle["id"]: bundle for bundle in suite["tasks"]}

    it03_repos = {repo["name"]: repo for repo in tasks["IT-03"]["seed_config"]["github"]["repos"]}
    assert set(it03_repos) == {"payments-api", "developer-docs"}
    assert it03_repos["payments-api"]["owner"] == "acme"
    assert it03_repos["payments-api"]["issues"][0]["title"] == "Contain exposed deployment credential tok_7A91"

    it06_repos = {repo["name"]: repo for repo in tasks["IT-06"]["seed_config"]["github"]["repos"]}
    assert {"vendor-portal", "pricing-tools", "finance-automation"} == set(it06_repos)
    for repo_name in ("vendor-portal", "pricing-tools"):
        repo = it06_repos[repo_name]
        assert repo["owner"] == "acme"
        assert repo["issues"][0]["title"] == "OFF-308 contractor access handoff"
        assert "lena.contractor@acme.example" in repo["issues"][0]["body"]

    it06_folders = {folder["name"]: folder for folder in tasks["IT-06"]["seed_config"]["google_drive"]["folders"]}
    assert set(it06_folders) == {"Procurement 2026", "Quarterly Planning"}
    signed_form = it06_folders["Procurement 2026"]["files"][0]["content"]
    assert "OFF-308" in signed_form
    assert "signed by Emil Navarro" in signed_form


def test_neutral_worlds_are_specific_and_present_without_answer_keys() -> None:
    builder = _load_builder()
    worlds_module = __import__("argabench_40_worlds")
    suite = json.loads(SUITE_PATH.read_text())
    task_specs = {task["id"]: task for task in builder.TASKS}
    serialized_seeds: set[str] = set()

    assert set(worlds_module.WORLD_FACTS) == {bundle["id"] for bundle in suite["tasks"]}
    for bundle in suite["tasks"]:
        world = worlds_module.WORLD_FACTS[bundle["id"]]
        seed = bundle["seed_config"]
        seed_text = "\n".join(builder.seed_strings(seed))
        assert world["asset"] in seed_text
        assert task_specs[bundle["id"]]["reporter"].split(" from ", 1)[0] in seed_text
        serialized = json.dumps(seed, sort_keys=True)
        assert serialized not in serialized_seeds
        serialized_seeds.add(serialized)


def test_workflow_policies_are_neutral_and_required_deliverables_are_public() -> None:
    builder = _load_builder()
    policies = __import__("argabench_40_seeds").WORKFLOW_POLICIES
    suite = json.loads(SUITE_PATH.read_text())
    tasks = {bundle["id"]: bundle for bundle in suite["tasks"]}
    reviewed_confirmation_tasks = {
        bundle["id"]
        for bundle in suite["tasks"]
        if any(
            outcome["id"] == "reviewed_unsent_confirmation"
            for outcome in bundle["verification"]["required_outcomes"]
        )
    }

    assert reviewed_confirmation_tasks == {"CRM-02", "CRM-03", "CRM-05", "ECOM-02", "ECOM-04"}
    assert set(policies) == {
        "CRM-02",
        "CRM-03",
        "CRM-05",
        "CRM-08",
        "MKT-08",
        "ECOM-02",
        "ECOM-04",
    }
    direct_instruction = re.compile(
        r"\b(?:create|write|save|prepare)\b[^.\n]{0,40}\b(?:email draft|draft email|calendar hold)\b",
        re.IGNORECASE,
    )
    for task_id, (provider, policy) in policies.items():
        bundle = tasks[task_id]
        assert provider in bundle["twins"]
        assert not direct_instruction.search(bundle["prompt"])
        assert "draft" not in policy.lower()
        assert not any(tool_name in policy.lower() for tool_name in bundle["twins"])
        policy_occurrences = sum(policy in value for value in builder.seed_strings(bundle["seed_config"]))
        assert policy_occurrences == 1, f"{task_id} policy must be present exactly once"
        if task_id in reviewed_confirmation_tasks:
            outcome = next(
                item
                for item in bundle["verification"]["required_outcomes"]
                if item["id"] == "reviewed_unsent_confirmation"
            )
            assert outcome["critical"] is True
            assert outcome["cardinality"] == 1
            assert outcome["selector"]["unsent"] is True
            assert outcome["selector"]["accountable_owner_review"] is True


def test_it01_does_not_prescribe_hidden_gmail_or_duplicate_tracker_work() -> None:
    from arga_twins_benchmark.reporting.argabench_it_dev_legacy import (
        semantic_requirement_contracts,
        semantic_requirement_group_contracts,
    )

    builder = _load_builder()
    suite = json.loads(SUITE_PATH.read_text())
    task = next(bundle for bundle in suite["tasks"] if bundle["id"] == "IT-01")
    primary = next(
        outcome for outcome in task["verification"]["required_outcomes"] if outcome["id"] == "primary_outcome"
    )["description"]
    seed_text = "\n".join(builder.seed_strings(task["seed_config"])).casefold()

    assert "gmail thread is quarantined" not in primary.casefold()
    assert "permitted but not required" in primary.casefold()
    assert "mail_quarantined" not in {item[0] for item in semantic_requirement_contracts("IT-01")}
    groups = semantic_requirement_group_contracts("IT-01")
    assert len(groups) == 1
    assertion_id, alternatives = groups[0]
    assert assertion_id == "incident_evidence_reconciled"
    assert {alternative[0] for alternative in alternatives} == {"jira", "github"}
    assert "quarantine policy" not in seed_text


def test_ecom01_does_not_require_hidden_jira_reconciliation() -> None:
    import arga_twins_benchmark.reporting.argabench_mkt_ecom_legacy as legacy

    suite = json.loads(SUITE_PATH.read_text())
    task = next(bundle for bundle in suite["tasks"] if bundle["id"] == "ECOM-01")
    required = {outcome["id"]: outcome for outcome in task["verification"]["required_outcomes"]}

    assert "jira" not in required["primary_outcome"]["description"].casefold()
    assert required["cross_system_correlation"]["providers"] == ["stripe", "slack"]
    assert [requirement.assertion_id for requirement in legacy._RULES["ECOM-01"].requirements] == [  # pyright: ignore[reportPrivateUsage]
        "empty_trial_archived"
    ]
    assert "jira" in legacy._RULES["ECOM-01"].allowed_writes  # pyright: ignore[reportPrivateUsage]


def test_every_required_quarantine_has_seeded_human_policy() -> None:
    from arga_twins_benchmark.reporting.argabench_fair import semantic_requirements_for_task

    builder = _load_builder()
    suite = json.loads(SUITE_PATH.read_text())
    quarantine_tasks = {
        task["id"]
        for task in suite["tasks"]
        if any("quarantin" in requirement.id for requirement in semantic_requirements_for_task(task))
    }

    assert quarantine_tasks == {"DEV-03"}
    dev03 = next(task for task in suite["tasks"] if task["id"] == "DEV-03")
    seed_text = "\n".join(builder.seed_strings(dev03["seed_config"])).casefold()
    assert "active reliability policy crp-6 permits a 24-hour bounded quarantine" in seed_text


def test_outcome_verifiers_do_not_grade_hidden_write_counts() -> None:
    suite = json.loads(SUITE_PATH.read_text())

    for bundle in suite["tasks"]:
        verification = bundle["verification"]
        mutation_policy = verification["mutation_policy"]
        allowed_scope = mutation_policy.get("allowed_scope", {})
        assert "maximum_writes_per_resource" not in allowed_scope
        assert "post_write_readback_required" not in allowed_scope
        assert "append_once" not in json.dumps(mutation_policy)

        structured = next(
            outcome for outcome in verification["required_outcomes"] if outcome["id"] == "structured_result"
        )
        assert structured["final_response_required"] is False
        assert {"provider_state", "authorized_internal_updates"} <= set(structured["evidence_sources"])

        updates = [
            outcome
            for outcome in verification["required_outcomes"]
            if outcome["id"] in {"originating_channel_update", "originating_thread_update"}
        ]
        assert len(updates) == 1
        assert updates[0]["minimum_cardinality"] == 1
        assert "cardinality" not in updates[0]


def test_repaired_low_pass_scenarios_expose_outcomes_and_seed_actionable_assets() -> None:
    suite = json.loads(SUITE_PATH.read_text())
    tasks = {task["id"]: task for task in suite["tasks"]}

    for task_id in ("CRM-06", "CRM-08", "DEV-04", "DEV-06"):
        assert "The resolved state should" in tasks[task_id]["prompt"]

    crm06 = tasks["CRM-06"]["seed_config"]
    assert any(owner["first_name"] == "Amina" and owner["last_name"] == "Yusuf" for owner in crm06["hubspot"]["owners"])
    assert any(
        record["object"] == "User" and record["values"]["Name"] == "Amina Yusuf"
        for record in crm06["salesforce"]["records"]
    )

    crm08 = tasks["CRM-08"]["seed_config"]
    calendar_events = crm08["google_calendar"]["calendars"][0]["events"]
    assert all("start" not in event and "end" not in event for event in calendar_events[:2])
    assert {event["start_time"] for event in calendar_events[:2]} == {"08:00:00", "12:00:00"}
    assert any(owner["first_name"] == "Iris" and owner["last_name"] == "Novak" for owner in crm08["hubspot"]["owners"])

    dev04_repo = tasks["DEV-04"]["seed_config"]["github"]["repos"][0]
    assert {branch["name"] for branch in dev04_repo["branches"]} >= {"release/4.8"}

    dev06 = tasks["DEV-06"]
    assert "Jira ENG-1" in dev06["target"]
    assert "Jira API-331" not in json.dumps(dev06)


def test_customer_review_policy_is_an_explicit_outcome_without_provider_prompting() -> None:
    import arga_twins_benchmark.reporting.argabench_mkt_ecom_legacy as legacy

    suite = json.loads(SUITE_PATH.read_text())
    tasks = {task["id"]: task for task in suite["tasks"]}
    for task_id in ("ECOM-02", "ECOM-04"):
        reviewed = next(
            outcome
            for outcome in tasks[task_id]["verification"]["required_outcomes"]
            if outcome["id"] == "reviewed_unsent_confirmation"
        )
        structured = next(
            outcome
            for outcome in tasks[task_id]["verification"]["required_outcomes"]
            if outcome["id"] == "structured_result"
        )
        assert reviewed["critical"] is True
        assert "email_state" not in structured["facts"]
        assert all(requirement.provider != "gmail" for requirement in legacy._RULES[task_id].requirements)  # pyright: ignore[reportPrivateUsage]
        assert legacy._RULES[task_id].gmail_drafts == 1  # pyright: ignore[reportPrivateUsage]

    assert [
        requirement.assertion_id
        for requirement in legacy._RULES["ECOM-08"].requirements  # pyright: ignore[reportPrivateUsage]
    ] == ["empty_evaluation_archived"]


def test_seed_validation_report_covers_every_task() -> None:
    builder = _load_builder()
    suite = json.loads(SUITE_PATH.read_text())
    report = json.loads((SUITE_PATH.parent / "seed-validation.json").read_text())

    assert report["checked"] == report["passed"] == 40
    assert report["failed"] == 0
    assert {result["task_id"] for result in report["results"]} == {bundle["id"] for bundle in suite["tasks"]}
    assert all(result["ok"] and result["status"] == "ready" for result in report["results"])
    hashes = {result["task_id"]: result["content_sha256"] for result in report["results"]}
    assert hashes == {bundle["id"]: builder.content_hash(bundle) for bundle in suite["tasks"]}


def test_dev05_calibration_requires_observable_codeowner_resolution() -> None:
    suite = json.loads(SUITE_PATH.read_text())
    bundle = next(task for task in suite["tasks"] if task["id"] == "DEV-05")
    github = bundle["seed_config"]["github"]
    repo = github["repos"][0]
    file_paths = {item["path"] for item in repo["files"]}

    assert {"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"} <= file_paths
    assert len(repo["prs"]) == 5
    assert sum("paycore-2026.08-r17" in pr["body"] for pr in repo["prs"]) == 4
    assert sum("Fingerprint c91d-7a40" in pr["body"] for pr in repo["prs"]) == 3

    verification = bundle["verification"]
    assert verification["version"] == 2
    assert verification["grading_basis"] == "observable_business_outcomes"
    assert verification["trajectory_policy"] == "any_valid_provider_order"
    serialized = json.dumps(verification, sort_keys=True)
    assert '"requested_team": "billing-storage"' in serialized
    assert '"policy_file": ".github/CODEOWNERS"' in serialized
    assert '"resource_type": "pull_request_review"' not in serialized
    structured = next(item for item in verification["required_outcomes"] if item["id"] == "structured_result")
    assert "task_id" not in structured["facts"]
