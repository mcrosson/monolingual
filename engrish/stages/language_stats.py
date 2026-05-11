"""``engrish language-stats`` — report headword / definition / variant counts.

Reads ``data/<locale>/en/data-<locale>.json`` and emits three counts per M4-AC3:
- headwords: total entries in the JSON
- definitions: entries that own ``definitions`` (non-empty list)
- variant-only: entries that own ``variants`` but NOT ``definitions``

D29 recursion: if the JSON is missing, render is invoked (which itself recurses
into parse / prepare as needed).
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

from engrish.stages.render import _existing_json, latest_json, run as render_run

log = logging.getLogger(__name__)


def count(data: dict[str, Any]) -> tuple[int, int, int]:
    """Return (headwords, definitions, variant_only).

    Headwords = number of top-level keys in the render JSON.
    Definitions = entries with at least one definition (``definitions`` list non-empty).
    Variant-only = entries with ``variants`` but no definitions.
    """
    headwords = len(data)
    definitions = 0
    variant_only = 0
    for entry in data.values():
        has_defs = bool(entry.get("definitions"))
        has_vars = bool(entry.get("variants"))
        if has_defs:
            definitions += 1
        if has_vars and not has_defs:
            variant_only += 1
    return headwords, definitions, variant_only


def run(locale: str) -> int:
    """Entry: ensure JSON, read it, print counts. Returns 0."""
    if _existing_json(locale) is None:
        render_run(locale)
    json_path = latest_json(locale)

    data = _json.loads(json_path.read_text(encoding="utf-8"))
    headwords, definitions, variant_only = count(data)

    print(f"[{locale}] headwords:    {headwords:>10,}")
    print(f"[{locale}] definitions:  {definitions:>10,}")
    print(f"[{locale}] variant-only: {variant_only:>10,}")
    return 0
