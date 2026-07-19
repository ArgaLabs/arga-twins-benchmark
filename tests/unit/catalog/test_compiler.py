from pathlib import Path

from arga_twins_benchmark.catalog import compile_scenario


def test_compiler_emits_readable_unique_name_and_task_description_without_scenario_prompt() -> None:
    catalog_root = Path("benchmark")
    instance_id = "blocking_code_review_v1_github_clean_001"
    scenario = compile_scenario(catalog_root, instance_id)
    other_variant = compile_scenario(catalog_root, "blocking_code_review_v1_github_distractor_002")
    task_description = (catalog_root / "instances" / "dev" / instance_id / "prompt.txt").read_text().strip()

    assert scenario["twins"] == ["github"]
    assert list(scenario["seed_config"]) == ["github"]
    assert "Blocking code review" in scenario["name"]
    assert instance_id in scenario["name"]
    assert scenario["name"] != other_variant["name"]
    assert task_description in scenario["description"]
    assert "prompt" not in scenario
    assert scenario["tags"][0] == "arga-bench"
