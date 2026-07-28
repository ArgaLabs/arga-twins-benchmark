from __future__ import annotations


class RetryableInfrastructureError(RuntimeError):
    """A benchmark infrastructure failure that invalidates and may retry an episode."""
