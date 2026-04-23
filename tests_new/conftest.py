"""Engrish rewrite test suite.

Parallel to ``tests/`` (the AI-authored suite scheduled for deletion in M11 per D9).
Built incrementally from M0 onward per ``task-round-4-acceptance-criteria``.

Cross-cutting state management: pytest's collection phase imports every test
module to discover tests. Any test file that imports ``engrish.wikidict_shim``
transitively imports the ``engrish`` package, whose ``__init__.py`` calls
``activate()``. That leaves the shim engaged before ANY test runs — including
tests that depend on shim-disengaged state (Italian revert checks, parity
harness). This autouse fixture force-deactivates before and after every test
so each test sees a clean baseline regardless of collection-time side effects.
Tests that want engaged state call ``activate()`` (or use ``engrish_mode()``)
explicitly inside the test body.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_wikidict_shim() -> Any:
    from engrish.wikidict_shim import deactivate, is_active

    while is_active():
        deactivate()
    yield
    while is_active():
        deactivate()
