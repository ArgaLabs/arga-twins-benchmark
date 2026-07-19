import json
import re
from pathlib import Path
from typing import cast

SUPPORTED_TOP_LEVEL: dict[str, set[str]] = {
    "gmail": {"labels", "messages", "drafts"},
    "google_calendar": {"calendars"},
    "github": {"users", "orgs", "repos", "owner"},
    "gitlab": {"users", "groups", "projects"},
    "jira": {"projects", "webhooks"},
    "linear": {"teams", "projects", "issues", "comments"},
    "slack": {"users", "channels", "config"},
    "discord": {"guilds"},
    "notion": {"pages", "databases"},
    "google_drive": {"folders"},
    "stripe": {"customers", "products", "meters"},
}


def _load_object(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text())
    assert isinstance(value, dict), path
    return cast(dict[str, object], value)


def _objects(value: object | None) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, object], item) for item in cast(list[object], value) if isinstance(item, dict)]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in cast(list[object], value) for text in _strings(item)]
    if isinstance(value, dict):
        return [text for item in cast(dict[object, object], value).values() for text in _strings(item)]
    return []


def test_seed_top_level_keys_match_pinned_scenario_runners() -> None:
    for path in Path("benchmark/instances/dev").glob("*/seed/*.json"):
        provider = path.stem
        assert provider in SUPPORTED_TOP_LEVEL, path
        assert set(_load_object(path)) <= SUPPORTED_TOP_LEVEL[provider], path


def test_github_merged_fixtures_are_not_declared_open() -> None:
    for path in Path("benchmark/instances/dev").glob("*/seed/github.json"):
        for repository in _objects(_load_object(path).get("repos")):
            for pull_request in _objects(repository.get("prs")):
                if pull_request.get("merged") is True:
                    assert pull_request.get("state") in {None, "merged"}, path


def test_slack_fixtures_do_not_rely_on_ignored_email_field() -> None:
    for path in Path("benchmark/instances/dev").glob("*/seed/slack.json"):
        for user in _objects(_load_object(path).get("users")):
            assert "email" not in user, path


def test_runbook_numbered_markdown_round_trips_through_notion() -> None:
    numbered_line = re.compile(r"^(\d+)\. ")
    for path in Path("benchmark/instances/dev").glob("runbook_publication_v1_*/seed/*.json"):
        for text in _strings(_load_object(path)):
            for line in text.splitlines():
                if match := numbered_line.match(line):
                    assert match.group(1) == "1", path
