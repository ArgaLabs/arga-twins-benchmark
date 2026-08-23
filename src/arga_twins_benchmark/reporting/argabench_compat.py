"""Read compatibility for artifacts emitted before the ArgaBench rename."""

from __future__ import annotations

_LEGACY_NAMESPACE = "-".join(("cross", "functional"))

LEGACY_ATTEMPT_PROTOCOL = f"arga-bench-{_LEGACY_NAMESPACE}-attempt/2"
CURRENT_ATTEMPT_PROTOCOL = "argabench-attempt/2"
SUPPORTED_ATTEMPT_PROTOCOLS = frozenset(
    {
        CURRENT_ATTEMPT_PROTOCOL,
        LEGACY_ATTEMPT_PROTOCOL,
    }
)
