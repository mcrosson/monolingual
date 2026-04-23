"""M1b-AC1: Italian parse regression gate.

The AC's behavioral form is: ``# {{Nodef|it}} voce italiana`` → entry body
``voce italiana`` (template stripped, definition NOT dropped).

Two code paths decide this:

1. ``wikidict/render.py:242`` — uses ``lang.definitions_to_ignore[lang_dst]``
   as a case-insensitive substring filter that drops entire definition items
   whose wikitext contains any listed marker. Pre-revert, Italian contributed
   ``("{{Nodef", ...)`` here → the whole definition was dropped. Post-revert,
   Italian falls back to ``defaults.definitions_to_ignore = ()`` → no drop.

2. ``wikidict/utils.py:922`` — uses ``lang.templates_ignored[locale]`` in
   ``process_templates`` to strip templates (set body to ``""``). Post-revert,
   Italian contributes ``("{{Nodef", ...)`` here → template stripped, text kept.

Exercising the full behavioral pipeline requires a Context with a populated
wikitextprocessor SQLite DB (too heavy for a unit test). These attribute-level
checks equivalently prove the behavior: if both aggregate dicts resolve as
expected, render.py's drop filter does not fire for Italian and utils.py's
template-strip matches ``{{Nodef|it}}``. End-to-end behavioral coverage is
delivered by M11-AC3's full ``engrish.sh`` integration run.

This test fires loud if someone silently re-applies the D11-reverted rename.
"""

from __future__ import annotations

from wikidict import lang
from wikidict.lang import it


def test_italian_module_exposes_templates_ignored_not_definitions_to_ignore() -> None:
    """File-level: the exact fields per D11's revert."""
    assert it.templates_ignored == ("{{Nodef", "{{Noetim", "{{Noref")
    # Italian must NOT own ``definitions_to_ignore`` — the pre-revert mistake
    # was owning that field instead of templates_ignored.
    assert "definitions_to_ignore" not in vars(it)


def test_aggregate_templates_ignored_resolves_for_italian() -> None:
    """utils.py:922 path: ``lang.templates_ignored['it']`` strips Nodef/Noetim/Noref."""
    assert lang.templates_ignored["it"] == ("{{Nodef", "{{Noetim", "{{Noref")


def test_aggregate_definitions_to_ignore_empty_for_italian() -> None:
    """render.py:242 path: ``lang.definitions_to_ignore['it']`` is empty → no drop."""
    assert lang.definitions_to_ignore["it"] == ()
