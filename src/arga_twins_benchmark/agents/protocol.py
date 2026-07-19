from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentTarget:
    base_url: str
    invoke_path: str = "/invoke"


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    trial_id: str
    current_time: str
    principal: str
    tenant: str


@dataclass(frozen=True)
class InvocationResult:
    output: Any
    status_code: int
    latency_ms: int


class AgentAdapter(Protocol):
    async def health(self, target: AgentTarget) -> bool: ...

    async def invoke(
        self,
        target: AgentTarget,
        request: AgentRequest,
        timeout_seconds: int,
    ) -> InvocationResult: ...
