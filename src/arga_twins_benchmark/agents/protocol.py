from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    trial_id: str
    current_time: str
    principal: str
    tenant: str
    provider_access: dict[str, dict[str, object]]


@dataclass(frozen=True)
class InvocationResult:
    output: Any
    status_code: int
    latency_ms: int


class AgentAdapter(Protocol):
    async def invoke(
        self,
        request: AgentRequest,
        timeout_seconds: int,
    ) -> InvocationResult: ...
