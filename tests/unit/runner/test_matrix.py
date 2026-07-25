from __future__ import annotations

import os
from pathlib import Path

import pytest

from arga_twins_benchmark.runner.matrix import (
    build_trial_plans,
    load_env_file,
    load_experiment_bundles,
)
from arga_twins_benchmark.runner.prompting import MODEL_PROFILES


def test_load_experiment_bundles_resolves_prompts_bindings_and_verifiers() -> None:
    experiment, bundles = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")

    assert len(experiment.instances) == 48
    assert len(bundles) == 48
    bundle = bundles["blocking_code_review_v1_github_clean_001"]
    assert bundle.binding.roles == {"code_host": "github"}
    assert bundle.prompt.startswith("Act as the security reviewer")
    assert bundle.verification.output_contract.required_facts["decision"] == "changes_requested"


def test_build_trial_plans_interleaves_models_per_instance() -> None:
    experiment, _ = load_experiment_bundles(Path("benchmark"), "development_pilot_48_v1")

    plans = build_trial_plans(
        experiment,
        suite_run_id="suite-1",
        model_profiles=MODEL_PROFILES,
        repeats=1,
    )

    assert len(plans) == 144
    assert [plan.model.model_id for plan in plans[:3]] == [profile.model_id for profile in MODEL_PROFILES]
    assert len({plan.trial_id for plan in plans}) == 144


def test_load_env_file_does_not_overwrite_existing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_VALUE=file\nNEW_VALUE=secret\n")
    monkeypatch.setenv("EXISTING_VALUE", "process")
    monkeypatch.delenv("NEW_VALUE", raising=False)

    loaded = load_env_file(env_path)

    assert loaded == ["NEW_VALUE"]
    assert os.environ["EXISTING_VALUE"] == "process"
    assert os.environ["NEW_VALUE"] == "secret"
