from __future__ import annotations

from arga_twins_benchmark.evaluation.canonicalizers.code_collab import (
    CODE_COLLAB_CANONICALIZERS,
)
from arga_twins_benchmark.evaluation.canonicalizers.productivity import (
    PRODUCTIVITY_CANONICALIZERS,
)
from arga_twins_benchmark.evaluation.state_capture import SnapshotCanonicalizer


def canonicalizers() -> dict[str, SnapshotCanonicalizer]:
    """Return the complete fail-closed snapshot canonicalizer registry."""

    overlap = set(CODE_COLLAB_CANONICALIZERS) & set(PRODUCTIVITY_CANONICALIZERS)
    if overlap:
        names = ", ".join(sorted(overlap))
        raise RuntimeError(f"canonicalizer registry contains duplicate names: {names}")
    return {
        **CODE_COLLAB_CANONICALIZERS,
        **PRODUCTIVITY_CANONICALIZERS,
    }


CANONICALIZERS = canonicalizers()

__all__ = [
    "CANONICALIZERS",
    "CODE_COLLAB_CANONICALIZERS",
    "PRODUCTIVITY_CANONICALIZERS",
    "canonicalizers",
]
