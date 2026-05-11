"""M1b-AC2 (files #3 render.py, #4 context.py, #10 lang/ru/__init__.py): doctests.

The plan's original AC was to run ``python -m doctest`` (or ``doctest.testmod``)
against each of the three files and assert zero failures.

**Adjustment (2026-04-22, user-approved):** only ``wikidict.context`` is covered
here. ``wikidict.render`` (12/17 doctests) and ``wikidict.lang.ru`` (26/35
doctests) require a populated ``wikitextprocessor`` SQLite DB via
``wikidict.context.init()`` — the same heavyweight fixture that made M1b-AC1's
behavioral form infeasible. Replicating the upstream ``tests/conftest.py``
fixture infrastructure in ``engrish/tests/`` would duplicate work that D9/M11
explicitly plans to delete, and (c) a subprocess-bridge to the existing
``tests/`` files breaks at M11.

Decision: run ``context.py`` doctests in-suite (they are self-contained);
defer the runtime verification of ``render.py`` and ``lang/ru/__init__.py``
doctests to **M11-AC3** (full ``engrish.sh`` integration run, where the
populated Context exists naturally) and to the existing ``tests/test_2_render.py``
/ ``tests/test_ru.py`` which continue to run until M11 deletes them.
"""

from __future__ import annotations

import doctest


def test_wikidict_context_doctests() -> None:
    """Round-2 audit file #4: ``wikidict.context`` doctests pass (self-contained)."""
    from wikidict import context

    result = doctest.testmod(context, verbose=False)
    assert result.failed == 0, (
        f"wikidict.context doctests: {result.failed} failed of {result.attempted}"
    )
    assert result.attempted > 0, "expected at least one doctest in wikidict.context"
