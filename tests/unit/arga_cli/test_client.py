import pytest

from arga_twins_benchmark.arga_cli import ArgaCliError, SubprocessArgaCli, TwinRun


def test_command_uses_configured_executable_and_api_url() -> None:
    client = SubprocessArgaCli(executable=("uv", "run", "arga"), api_url="https://arga.example")
    assert client.command("twin-runs", "status", "run-1", "--json") == (
        "uv",
        "run",
        "arga",
        "twin-runs",
        "status",
        "run-1",
        "--json",
    )


def test_supplied_api_key_is_scoped_to_temporary_cli_home() -> None:
    client = SubprocessArgaCli(executable=("arga",), api_key="secret-test-key")
    temporary_home = client.credential_home
    assert temporary_home is not None
    config = temporary_home / ".config/arga/config.json"
    assert config.read_text() == '{"api_key": "secret-test-key"}\n'
    assert oct(config.stat().st_mode & 0o777) == "0o600"

    client.close()
    assert not temporary_home.exists()


def test_candidate_access_excludes_control_plane_values() -> None:
    run = TwinRun.from_payload(
        {
            "run_id": "run-1",
            "status": "ready",
            "is_public": True,
            "proxy_token": "do-not-leak",
            "twins": {
                "github": {
                    "base_url": "https://pub-github.example",
                    "admin_url": "https://admin-github.example",
                    "env_vars": {
                        "GITHUB_TOKEN": "provider-token",
                        "GITHUB_BAD_TOKEN": "do-not-leak",
                        "GITHUB_ADMIN_URL": "https://admin-github.example",
                        "ARGA_PROXY_TOKEN": "do-not-leak",
                    },
                }
            },
        }
    )

    assert run.candidate_access() == {
        "github": {
            "base_url": "https://pub-github.example",
            "env": {"GITHUB_TOKEN": "provider-token"},
        }
    }


def test_private_run_cannot_be_exported_to_candidate() -> None:
    run = TwinRun.from_payload(
        {
            "run_id": "run-1",
            "status": "ready",
            "is_public": False,
            "twins": {"github": {"base_url": "https://private-github.example"}},
        }
    )
    with pytest.raises(ArgaCliError, match="data-plane gateway"):
        run.candidate_access()
