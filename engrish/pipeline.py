"""Pipeline orchestration with D29 dependency-graph recursion.

Exposes ``require_artifact(path, producer_fn)`` — the M3-AC5 scaffolding helper
that every M4+ stage uses to guarantee its upstream input exists. Contract per
``[[decision-log]]`` D29: presence-on-disk = valid; no staleness check, no
timestamp comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def require_artifact(path: Path, producer_fn: Callable[[], None]) -> Path:
    """Ensure ``path`` exists by invoking ``producer_fn`` if absent. Return ``path``.

    Contract (D29):
    - If ``path`` already exists, ``producer_fn`` is NOT called; return ``path``.
    - If ``path`` does not exist, ``producer_fn()`` is called; ``path`` must exist
      on successful return; otherwise ``RuntimeError`` is raised.
    - No staleness check: if the file exists, it is considered valid. Recompute
      by deleting the artifact before calling.
    """
    if not path.exists():
        producer_fn()
        if not path.exists():
            raise RuntimeError(
                f"producer {producer_fn!r} did not create required artifact: {path}"
            )
    return path
