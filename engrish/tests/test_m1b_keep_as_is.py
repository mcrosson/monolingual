"""M1b-AC2 (files #1 stubs.py, #11 lang/ru/variant_handlers, #12 lang/sv/variant_handlers).

Grouped here because each is a single short verification.

File #1 — ``wikidict/stubs.py`` defines ``Variants = dict[str, set[str]]``. The
Round-2 disposition asked to confirm no external caller uses direct indexed
access (``d[key]``) on the aggregate map — only ``.get()``, ``in``, or iteration.
We grep the ``wikidict/`` source tree and allow only the one known safe hit
(``convert.py:717`` accesses ``details.variants[0]`` where ``details`` is a
``Word`` instance and ``Word.variants`` is ``list[str]`` — list indexing, not
dict).

Files #11 and #12 — ``lang/ru/variant_handlers.py`` and ``lang/sv/variant_handlers.py``
each carry fork-additive regex/cleanup patterns. Their existing doctests are
self-contained (no ``wikidict.context.Context`` required). We run them via
``doctest.testmod`` and assert zero failures.
"""

from __future__ import annotations

import doctest
import importlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WIKIDICT_ROOT = REPO_ROOT / "wikidict"

# Known-safe ``.variants[...]`` accesses on the ``Word.variants`` list (not the
# ``Variants`` aggregate dict). Update explicitly if legitimate new hits appear.
_KNOWN_SAFE_HITS = frozenset(
    {
        (REPO_ROOT / "wikidict" / "convert.py", "details.variants[0]"),
    }
)


def _find_variants_indexed_access() -> list[tuple[Path, int, str]]:
    """Return ``(path, line_no, line)`` for every ``.variants[`` occurrence in wikidict/."""
    pattern = re.compile(r"\.variants\[")
    hits: list[tuple[Path, int, str]] = []
    for py_file in WIKIDICT_ROOT.rglob("*.py"):
        for line_no, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append((py_file, line_no, line.strip()))
    return hits


def test_stubs_variants_aggregate_no_unsafe_indexed_access() -> None:
    hits = _find_variants_indexed_access()
    unknown = [
        (path, lineno, line)
        for (path, lineno, line) in hits
        if not any(safe_expr in line for (safe_path, safe_expr) in _KNOWN_SAFE_HITS if safe_path == path)
    ]
    assert not unknown, (
        "unknown .variants[ indexed accesses (potential KeyError risk on the "
        f"Variants aggregate dict): {unknown}"
    )


def test_ru_variant_handlers_doctests() -> None:
    mod = importlib.import_module("wikidict.lang.ru.variant_handlers")
    result = doctest.testmod(mod, verbose=False)
    assert result.failed == 0, (
        f"wikidict.lang.ru.variant_handlers doctests: {result.failed} failed of {result.attempted}"
    )
    assert result.attempted > 0, "expected at least one doctest"


def test_sv_variant_handlers_doctests() -> None:
    mod = importlib.import_module("wikidict.lang.sv.variant_handlers")
    result = doctest.testmod(mod, verbose=False)
    assert result.failed == 0, (
        f"wikidict.lang.sv.variant_handlers doctests: {result.failed} failed of {result.attempted}"
    )
    assert result.attempted > 0, "expected at least one doctest"
