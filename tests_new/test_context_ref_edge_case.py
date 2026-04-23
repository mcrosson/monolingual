"""M1b-AC2 (file #4 context.py): regression case for ``<ref:...>`` truncation.

The ``<ref:...>`` cleanup in ``wikidict/context.py`` uses ``re.sub(r"<ref:[^>]+>", "", code)``.
The ``[^>]+`` class stops at the first ``>``, so a ``>`` appearing inside the ref
body (e.g., inside an embedded URL) truncates the match and leaves trailing
content after the first ``>``.

This test pins that behavior as known-and-acknowledged: future edits that
silently change the regex (for better or worse) will fire this test. Per
``task-rewrite-wikidict-disposition`` §#4 it's a negative-test docent, not a
bug-fix.

Lives in ``tests_new/`` rather than inside ``wikidict/context.py`` to avoid
widening fork divergence (D10: no upstream PRs from this fork).
"""

from __future__ import annotations

from wikidict import context


def test_ref_cleanup_happy_path() -> None:
    """Regression for the existing doctest happy path."""
    assert (
        context.clean_html_input(
            "* {{IPA|en|/pɹoʊ/<q:obsolete><ref:"
            "{{R:Critical Pronouncing Dictionary|section=principles|page=37}}>}}",
            "en",
        )
        == "* {{IPA|en|/pɹoʊ/<q:obsolete>}}"
    )


def test_ref_cleanup_truncates_on_embedded_gt() -> None:
    """Known edge case: ``>`` inside the ref body truncates the match.

    Input has ``<ref:a>b>`` — the regex matches ``<ref:a>`` (up to the first
    ``>``) and leaves ``b>`` in the output. Documenting this as the current
    contract; any change to ``wikidict/context.py``'s ``<ref:...>`` regex must
    explicitly re-justify.
    """
    assert context.clean_html_input("foo<ref:a>b>bar", "en") == "foob>bar"


def test_ref_cleanup_truncates_on_url_with_gt() -> None:
    """Concrete manifestation with an embedded URL containing ``>``.

    ``<ref:http://ex.com/x=>y>trailing`` → ``[^>]+`` matches ``http://ex.com/x=``,
    regex consumes ``<ref:http://ex.com/x=>``, output is ``y>trailing``.
    """
    assert (
        context.clean_html_input("<ref:http://ex.com/x=>y>trailing", "en")
        == "y>trailing"
    )
