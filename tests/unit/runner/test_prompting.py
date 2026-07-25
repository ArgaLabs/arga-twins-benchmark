from __future__ import annotations

import json
from pathlib import Path

from arga_twins_benchmark.runner.prompting import (
    MODEL_PROFILES,
    SYSTEM_PROMPT,
    compose_user_prompt,
    experiment_prompts,
    render_prompt_ledger_markdown,
    write_prompt_ledger,
)
from arga_twins_benchmark.specs.models import OutputContractSpec

CATALOG_ROOT = Path("benchmark")
EXPERIMENT_ID = "development_pilot_48_v1"


def test_experiment_prompt_ledger_is_balanced_and_exact() -> None:
    entries = experiment_prompts(CATALOG_ROOT, EXPERIMENT_ID)

    assert len(entries) == 48 * 3
    assert {entry.model_id for entry in entries} == {profile.model_id for profile in MODEL_PROFILES}
    assert {entry.system_prompt for entry in entries} == {SYSTEM_PROMPT}
    assert len({entry.instance_id for entry in entries}) == 48

    grouped: dict[str, list[str]] = {}
    for entry in entries:
        grouped.setdefault(entry.instance_id, []).append(entry.user_prompt)
    assert all(len(prompts) == 3 and len(set(prompts)) == 1 for prompts in grouped.values())
    assert all("Final response contract:" in prompts[0] for prompts in grouped.values())
    assert all("this contract specifies structure, not answers" in prompts[0] for prompts in grouped.values())


def test_write_prompt_ledger_is_private_and_markdown_lists_each_prompt(tmp_path: Path) -> None:
    ledger_path = tmp_path / "prompt-ledger.json"
    write_prompt_ledger(ledger_path, CATALOG_ROOT, EXPERIMENT_ID)

    payload = json.loads(ledger_path.read_text())
    rendered = render_prompt_ledger_markdown(payload)

    assert payload["entry_count"] == 144
    assert rendered.count("\n### ") == 48
    assert "claude-opus-4-8" in rendered
    assert "claude-fable-5" in rendered
    assert "gpt-5.6-sol" in rendered
    assert ledger_path.stat().st_mode & 0o077 == 0


def test_structured_output_instruction_exposes_shape_but_not_expected_values() -> None:
    contract = OutputContractSpec.model_validate(
        {
            "mode": "structured_facts",
            "required_facts": {
                "decision": "changes_requested",
                "target_change": 2,
                "acknowledged": True,
            },
            "critical": True,
        }
    )

    prompt = compose_user_prompt("Review the change.", contract)

    assert "`decision` (string)" in prompt
    assert "`target_change` (integer)" in prompt
    assert "`acknowledged` (boolean)" in prompt
    assert "changes_requested" not in prompt
