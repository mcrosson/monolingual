"""M8-C regression tests: per-variant ``name`` table + ``hhea`` propagation.

Live data: real Noto fonts under ``fonts/Noto/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engrish.font import propagate_hhea, set_subfamily


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOTO_GOTHIC = REPO_ROOT / "fonts" / "Noto" / "NotoSansGothic-Regular.ttf"
NOTO_AVESTAN = REPO_ROOT / "fonts" / "Noto" / "NotoSansAvestan-Regular.ttf"

noto_gothic_required = pytest.mark.skipif(
    not NOTO_GOTHIC.exists(),
    reason="NotoSansGothic-Regular.ttf absent",
)
noto_avestan_required = pytest.mark.skipif(
    not NOTO_AVESTAN.exists(),
    reason="NotoSansAvestan-Regular.ttf absent",
)


# --- M8-AC4 set_subfamily ---


@noto_gothic_required
def test_set_subfamily_writes_name_table_id_2_across_canonical_records() -> None:
    """Every canonical (platformID, platEncID, langID) triple should carry the new label."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(NOTO_GOTHIC))
    set_subfamily(font, "Bold Italic")
    name_table = font["name"]
    for platform_id, plat_enc_id, lang_id in (
        (3, 1, 0x0409),
        (1, 0, 0),
        (0, 4, 0),
    ):
        rec = name_table.getName(2, platform_id, plat_enc_id, lang_id)
        assert rec is not None, f"missing name record for ({platform_id},{plat_enc_id},{lang_id})"
        assert rec.toUnicode() == "Bold Italic", (
            f"({platform_id},{plat_enc_id},{lang_id}) records {rec.toUnicode()!r}, expected 'Bold Italic'"
        )


@noto_gothic_required
def test_four_variant_pseudo_run_produces_four_distinct_subfamilies() -> None:
    """Simulate the M8 pipeline's four-variant emission: each TTF gets a distinct label."""
    from fontTools.ttLib import TTFont

    labels = ["Regular", "Bold", "Italic", "Bold Italic"]
    actuals: list[str] = []
    for label in labels:
        font = TTFont(str(NOTO_GOTHIC))
        set_subfamily(font, label)
        # Read back the Windows record (the one most readers consult).
        rec = font["name"].getName(2, 3, 1, 0x0409)
        assert rec is not None
        actuals.append(rec.toUnicode())
    assert actuals == labels
    assert len(set(actuals)) == 4, "expected 4 distinct subfamilies"


# --- M8-AC5 propagate_hhea ---


@noto_gothic_required
@noto_avestan_required
def test_propagate_hhea_aggregates_from_multiple_sources() -> None:
    """Aggregate rule: max(ascent), min(descent), max(lineGap)."""
    from fontTools.ttLib import TTFont

    sources = [NOTO_GOTHIC, NOTO_AVESTAN]
    src_metrics = []
    for sp in sources:
        f = TTFont(str(sp), lazy=True)
        src_metrics.append((f["hhea"].ascent, f["hhea"].descent, f["hhea"].lineGap))
        f.close()

    expected_ascent = max(m[0] for m in src_metrics)
    expected_descent = min(m[1] for m in src_metrics)
    expected_line_gap = max(m[2] for m in src_metrics)

    # Take a real font as the merge target and overwrite its metrics with junk.
    target = TTFont(str(NOTO_GOTHIC))
    target["hhea"].ascent = -9999
    target["hhea"].descent = 9999
    target["hhea"].lineGap = -9999

    propagate_hhea(target, sources)

    assert target["hhea"].ascent == expected_ascent
    assert target["hhea"].descent == expected_descent
    assert target["hhea"].lineGap == expected_line_gap


def test_propagate_hhea_no_hard_coded_constants_in_source() -> None:
    """AST-walk to ensure no literal 800 / -200 is assigned to hhea fields."""
    import ast

    src = (REPO_ROOT / "engrish" / "font.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # Look for assignments like ``target_hhea.ascent = 800``.
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr in ("ascent", "descent", "lineGap")
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, int)
                    and node.value.value in (800, -200)
                ):
                    pytest.fail(
                        f"engrish/font.py line {node.lineno} assigns "
                        f"hard-coded {node.value.value} to {tgt.attr}; "
                        f"M8-AC5 forbids this without inline justification"
                    )


@noto_gothic_required
def test_propagate_hhea_with_empty_sources_is_noop() -> None:
    """Defensive: empty source list leaves target unchanged."""
    from fontTools.ttLib import TTFont

    target = TTFont(str(NOTO_GOTHIC))
    before = (target["hhea"].ascent, target["hhea"].descent, target["hhea"].lineGap)
    propagate_hhea(target, [])
    after = (target["hhea"].ascent, target["hhea"].descent, target["hhea"].lineGap)
    assert before == after
