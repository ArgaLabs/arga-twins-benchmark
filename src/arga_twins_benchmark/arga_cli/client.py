from __future__ import annotations

import asyncio
import json
import os
import random
import shlex
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast


class ArgaCliError(RuntimeError):
    """Raised when the Arga CLI fails or returns an invalid lifecycle payload."""


@dataclass(frozen=True)
class ProvisionedTwin:
    name: str
    base_url: str
    admin_url: str | None = None
    env_vars: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    mcp_url: str | None = None

    def candidate_access(self, *, blocked_values: frozenset[str] = frozenset()) -> dict[str, object]:
        blocked_names = {"PROXY_TOKEN"}
        safe_env = {
            name: value
            for name, value in self.env_vars.items()
            if name not in blocked_names
            and not name.upper().startswith("ARGA_")
            and "ADMIN" not in name.upper()
            and value not in blocked_values
        }
        access: dict[str, object] = {"base_url": self.base_url, "env": safe_env}
        if self.mcp_url:
            access["mcp_url"] = self.mcp_url
        return access


@dataclass(frozen=True)
class TwinRun:
    run_id: str
    status: str
    twins: dict[str, ProvisionedTwin]
    is_public: bool
    proxy_token: str | None = None
    seed_results: object | None = None
    raw: Mapping[str, Any] = field(default_factory=lambda: dict[str, Any](), repr=False)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TwinRun:
        return _parse_twin_run(payload)

    def candidate_access(self) -> dict[str, dict[str, object]]:
        if not self.is_public:
            raise ArgaCliError("private twin runs cannot be passed to a candidate without a data-plane gateway")
        blocked_values: frozenset[str] = frozenset({self.proxy_token}) if self.proxy_token else frozenset[str]()
        return {name: twin.candidate_access(blocked_values=blocked_values) for name, twin in sorted(self.twins.items())}


class ArgaCli(Protocol):
    async def list_scenarios(self, *, tag: str) -> list[Mapping[str, Any]]: ...

    async def import_scenario(self, scenario_file: Path) -> Mapping[str, Any]: ...

    async def create_twin_run(
        self,
        *,
        twins: Sequence[str],
        scenario_id: str,
        ttl_minutes: int,
        candidate_safe: bool = False,
    ) -> TwinRun: ...

    async def status(self, run_id: str) -> TwinRun: ...

    async def reset(self, run_id: str) -> Mapping[str, Any]: ...

    async def teardown(self, run_id: str) -> Mapping[str, Any]: ...


class SubprocessArgaCli:
    """Typed wrapper over the installed and authenticated `arga` executable.

    This is the benchmark's only Arga control-plane boundary. Candidate and
    evaluator traffic goes to provisioned provider endpoints, not Arga APIs.
    """

    def __init__(
        self,
        *,
        executable: Sequence[str] | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        transient_retry_attempts: int = 7,
        transient_retry_base_seconds: float = 0.5,
    ) -> None:
        configured = os.environ.get("ARGA_CLI_BIN", "arga")
        self.executable = tuple(executable or shlex.split(configured))
        if not self.executable:
            raise ValueError("Arga CLI executable cannot be empty")
        self.api_url = api_url or os.environ.get("ARGA_API_URL", "https://api.argalabs.com")
        self.transient_retry_attempts = transient_retry_attempts
        self.transient_retry_base_seconds = transient_retry_base_seconds
        self._credential_home: tempfile.TemporaryDirectory[str] | None = None
        self._subprocess_env = os.environ.copy()
        supplied_key = api_key or os.environ.get("ARGA_API_KEY")
        if supplied_key:
            self._credential_home = tempfile.TemporaryDirectory(prefix="arga-bench-cli-")
            config_dir = Path(self._credential_home.name) / ".config" / "arga"
            config_dir.mkdir(parents=True, mode=0o700)
            config_path = config_dir / "config.json"
            config_path.write_text(json.dumps({"api_key": supplied_key}) + "\n")
            config_path.chmod(0o600)
            self._subprocess_env["HOME"] = self._credential_home.name

    async def __aenter__(self) -> SubprocessArgaCli:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._credential_home is not None:
            self._credential_home.cleanup()
            self._credential_home = None

    def command(self, *arguments: str) -> tuple[str, ...]:
        return (*self.executable, *arguments)

    @property
    def credential_home(self) -> Path | None:
        if self._credential_home is None:
            return None
        return Path(self._credential_home.name)

    async def list_scenarios(self, *, tag: str) -> list[Mapping[str, Any]]:
        payload = await self._run_json_value(
            "test-runner",
            "scenarios",
            "list",
            "--api-url",
            self.api_url,
            "--tag",
            tag,
            "--json",
        )
        if not isinstance(payload, list):
            raise ArgaCliError("Arga CLI scenario list JSON output must be an array")

        scenarios: list[Mapping[str, Any]] = []
        for index, item in enumerate(cast(list[object], payload)):
            if not isinstance(item, dict):
                raise ArgaCliError(f"Arga CLI scenario list item {index} must be an object")
            scenarios.append(cast(dict[str, Any], item))
        return scenarios

    async def import_scenario(self, scenario_file: Path) -> Mapping[str, Any]:
        return await self._run_json(
            "test-runner",
            "scenarios",
            "import",
            "--api-url",
            self.api_url,
            "--file",
            str(scenario_file),
            "--json",
        )

    async def create_twin_run(
        self,
        *,
        twins: Sequence[str],
        scenario_id: str,
        ttl_minutes: int = 60,
        candidate_safe: bool = False,
    ) -> TwinRun:
        """Create a run and return its ID immediately without hiding it in a wait subprocess."""

        if not twins:
            raise ValueError("at least one twin is required")
        arguments = [
            "twin-runs",
            "create",
            "--api-url",
            self.api_url,
            "--twins",
            ",".join(sorted(set(twins))),
            "--scenario-id",
            scenario_id,
            "--ttl",
            str(ttl_minutes),
        ]
        if candidate_safe:
            arguments.append("--candidate-safe")
        arguments.append("--json")
        payload = await self._run_json(*arguments)
        payload.setdefault("status", "queued")
        payload.setdefault("twins", {})
        run = _parse_twin_run(payload)
        return run

    async def status(self, run_id: str) -> TwinRun:
        payload = await self._run_json(
            "twin-runs",
            "status",
            "--api-url",
            self.api_url,
            run_id,
            "--json",
        )
        return _parse_twin_run(payload)

    async def reset(self, run_id: str) -> Mapping[str, Any]:
        payload = await self._run_json(
            "twin-runs",
            "reset",
            "--api-url",
            self.api_url,
            run_id,
            "--json",
        )
        if payload.get("status") not in {"ready", "reset_complete"}:
            raise ArgaCliError(f"twin reset returned unexpected status {payload.get('status')!r}")
        return payload

    async def teardown(self, run_id: str) -> Mapping[str, Any]:
        return await self._run_json(
            "twin-runs",
            "teardown",
            "--api-url",
            self.api_url,
            run_id,
            "--json",
        )

    async def _run_json_value(self, *arguments: str) -> object:
        for attempt in range(self.transient_retry_attempts):
            process = await asyncio.create_subprocess_exec(
                *self.command(*arguments),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._subprocess_env,
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                break
            message = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            retryable = any(
                marker in message
                for marker in (
                    "Failed to load current user",
                    "Failed to load twin provision status",
                    "Failed to tear down twins",
                )
            )
            if not retryable or attempt + 1 >= self.transient_retry_attempts:
                raise ArgaCliError(f"Arga CLI exited with {process.returncode}: {message}")
            delay = self.transient_retry_base_seconds * (2**attempt)
            await asyncio.sleep(delay + random.uniform(0, delay))
        else:  # pragma: no cover - the loop either breaks or raises
            raise AssertionError("Arga CLI retry loop exhausted without an outcome")
        try:
            payload: object = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ArgaCliError(f"Arga CLI did not return JSON: {stdout.decode(errors='replace').strip()}") from error
        return payload

    async def _run_json(self, *arguments: str) -> dict[str, Any]:
        payload = await self._run_json_value(*arguments)
        if not isinstance(payload, dict):
            raise ArgaCliError("Arga CLI JSON output must be an object")
        return cast(dict[str, Any], payload)


def _parse_twin_run(payload: Mapping[str, Any]) -> TwinRun:
    run_id = payload.get("run_id")
    status = payload.get("status")
    twins_payload = payload.get("twins")
    if not isinstance(run_id, str) or not run_id:
        raise ArgaCliError("twin-run payload is missing run_id")
    if not isinstance(status, str) or not status:
        raise ArgaCliError("twin-run payload is missing status")
    if not isinstance(twins_payload, dict):
        twins_payload = {}

    twins: dict[str, ProvisionedTwin] = {}
    for name, raw_value in cast(dict[object, object], twins_payload).items():
        if not isinstance(name, str) or not isinstance(raw_value, dict):
            raise ArgaCliError("twin-run twins must map names to objects")
        raw = cast(dict[str, Any], raw_value)
        base_url = raw.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise ArgaCliError(f"twin {name!r} is missing base_url")
        raw_env = raw.get("env_vars", {})
        if not isinstance(raw_env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in cast(dict[object, object], raw_env).items()
        ):
            raise ArgaCliError(f"twin {name!r} env_vars must contain strings")
        admin_url = raw.get("admin_url")
        mcp_url = raw.get("mcp_url") or raw.get("mcp_server_url")
        twins[name] = ProvisionedTwin(
            name=name,
            base_url=base_url,
            admin_url=admin_url if isinstance(admin_url, str) else None,
            env_vars=cast(dict[str, str], raw_env),
            mcp_url=mcp_url if isinstance(mcp_url, str) else None,
        )

    proxy_token = payload.get("proxy_token")
    return TwinRun(
        run_id=run_id,
        status=status,
        twins=twins,
        is_public=bool(payload.get("is_public")),
        proxy_token=proxy_token if isinstance(proxy_token, str) else None,
        seed_results=payload.get("seed_results"),
        raw=payload,
    )
