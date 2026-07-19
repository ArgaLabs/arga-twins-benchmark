from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ArgaControlPlane(Protocol):
    async def register_scenario(
        self,
        *,
        twins: list[str],
        seed_config: Mapping[str, Any],
        content_hash: str,
    ) -> str: ...

    async def create_sandbox(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def get_sandbox(self, run_id: str) -> Mapping[str, Any]: ...

    async def get_events(self, run_id: str) -> list[Mapping[str, Any]]: ...

    async def get_diagnostics(self, run_id: str) -> Mapping[str, Any]: ...

    async def get_logs(self, run_id: str) -> list[Mapping[str, Any]]: ...

    async def teardown(self, run_id: str) -> None: ...
