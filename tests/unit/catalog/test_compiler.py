from pathlib import Path

from arga_twins_benchmark.catalog import compile_scenario


def test_compiler_emits_exact_seed_without_task_prompt() -> None:
    scenario = compile_scenario(Path("benchmark"), "blocking_code_review_v1_github_clean_001")

    assert scenario["twins"] == ["github"]
    assert list(scenario["seed_config"]) == ["github"]
    assert "prompt" not in scenario
    assert scenario["tags"][0] == "arga-bench"
