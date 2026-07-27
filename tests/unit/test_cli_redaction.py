from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from arga_twins_benchmark.cli import app
from arga_twins_benchmark.cli_redaction import REDACTED, redact_cli_payload


def test_recursive_redaction_scrubs_nested_credentials_without_mutating_evidence() -> None:
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "seed_results": {
            "slack": {
                "env_vars": {
                    "SLACK_BOT_TOKEN": "xoxb-123456789012-secret",
                    "SLACK_SIGNING_SECRET": "signing-secret-value",
                    "SLACK_TEAM_ID": "T012345",
                },
                "nested": [
                    {"client_secret": "client-secret-value"},
                    "Authorization: Bearer bearer-secret-value",
                ],
            }
        },
        "proxy_token": "proxy-secret-value",
        "message": "request failed with api_key=embedded-secret-value",
        "usage": {"token_count": 42},
    }
    original = copy.deepcopy(payload)

    redacted = redact_cli_payload(payload)

    assert redacted == {
        "run_id": "run-1",
        "seed_results": {
            "slack": {
                "env_vars": {
                    "SLACK_BOT_TOKEN": REDACTED,
                    "SLACK_SIGNING_SECRET": REDACTED,
                    "SLACK_TEAM_ID": "T012345",
                },
                "nested": [
                    {"client_secret": REDACTED},
                    f"Authorization: Bearer {REDACTED}",
                ],
            }
        },
        "proxy_token": REDACTED,
        "message": f"request failed with api_key={REDACTED}",
        "usage": {"token_count": 42},
    }
    assert payload == original


def test_cleanup_cli_redacts_nested_slack_seed_result_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_payload: dict[str, Any] = {
        "twin_run": {
            "run_id": "run-1",
            "status": "cancelled",
            "twins": {},
            "seed_results": {
                "slack": {
                    "env_vars": {
                        "SLACK_BOT_TOKEN": "xoxb-123456789012-private",
                        "SLACK_APP_TOKEN": "xapp-123456789012-private",
                        "SLACK_TEAM_ID": "T012345",
                    }
                }
            },
            "proxy_token": "private-proxy-token",
        },
        "confirmation": {
            "password": "private-password",
            "details": "Authorization: Bearer private-bearer-token",
        },
    }
    original = copy.deepcopy(raw_payload)

    async def fake_cleanup(control_file: Path) -> dict[str, Any]:
        assert control_file == tmp_path / "control.json"
        return raw_payload

    monkeypatch.setattr("arga_twins_benchmark.cli.cleanup_instance", fake_cleanup)

    result = CliRunner().invoke(app, ["cleanup", str(tmp_path / "control.json")])

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    env_vars = output["twin_run"]["seed_results"]["slack"]["env_vars"]
    assert env_vars == {
        "SLACK_APP_TOKEN": REDACTED,
        "SLACK_BOT_TOKEN": REDACTED,
        "SLACK_TEAM_ID": "T012345",
    }
    assert output["twin_run"]["proxy_token"] == REDACTED
    assert output["confirmation"]["password"] == REDACTED
    assert output["confirmation"]["details"] == f"Authorization: Bearer {REDACTED}"
    for secret in (
        "xoxb-123456789012-private",
        "xapp-123456789012-private",
        "private-proxy-token",
        "private-password",
        "private-bearer-token",
    ):
        assert secret not in result.stdout
    assert raw_payload == original
