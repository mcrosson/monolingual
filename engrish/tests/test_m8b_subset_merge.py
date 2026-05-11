"""M8-B regression tests: subsetting + variable-font instantiation + pairwise merge.

Live data: real Noto fonts under ``fonts/Noto/``. Variable-font tests use
``NotoSans[wght].ttf``; static tests use ``NotoSansGothic-Regular.ttf`` and
``NotoSansAvestan-Regular.ttf``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from engrish.font import (
    get_subset_call_count,
    instantiate_variable,
    pairwise_merge,
    reset_subset_counter,
    source_font_cmap,
    subset_for_style_pair,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOTO_GOTHIC = REPO_ROOT / "fonts" / "Noto" / "NotoSansGothic-Regular.ttf"
NOTO_AVESTAN = REPO_ROOT / "fonts" / "Noto" / "NotoSansAvestan-Regular.ttf"
NOTO_SANS_VAR = REPO_ROOT / "fonts" / "Noto" / "NotoSans[wght].ttf"


noto_gothic_required = pytest.mark.skipif(
    not NOTO_GOTHIC.exists(),
    reason="NotoSansGothic-Regular.ttf absent",
)
noto_avestan_required = pytest.mark.skipif(
    not NOTO_AVESTAN.exists(),
    reason="NotoSansAvestan-Regular.ttf absent",
)
noto_var_required = pytest.mark.skipif(
    not NOTO_SANS_VAR.exists(),
    reason="NotoSans[wght].ttf absent",
)


# --- 2b #1 / M8-AC6: OverlapMode used, no overlap=0 ---


def test_font_module_uses_overlap_mode_member_not_zero() -> None:
    """AST-walk ``engrish/font.py`` — must call with ``overlap=OverlapMode.<X>`` form,
    never ``overlap=0`` (raw int). Docstring references to ``overlap=0`` are allowed
    (they document what was REMOVED)."""
    import ast

    src = (REPO_ROOT / "engrish" / "font.py").read_text(encoding="utf-8")
    assert "OverlapMode." in src, "engrish/font.py must use OverlapMode enum member"

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "overlap":
            if isinstance(node.value, ast.Constant) and node.value.value == 0:
                pytest.fail(
                    f"engrish/font.py line {node.value.lineno} uses ``overlap=0`` "
                    f"(raw int); switch to OverlapMode.<member>"
                )


# --- 2b #4 / M8-AC7: no manual axis clamp ---


def test_font_module_has_no_manual_axis_clamp() -> None:
    """grep — no leftover ``axis_limits.*clamp`` or hand-rolled clamping next to instancer."""
    src = (REPO_ROOT / "engrish" / "font.py").read_text(encoding="utf-8")
    m = re.match(r'\s*"""', src)
    code = src
    if m:
        end = src.find('"""', m.end())
        if end != -1:
            code = src[end + 3 :]
    # Allow ``axisLimits={"wght": ...}`` (the legitimate instancer argument);
    # forbid manual clamp helpers.
    assert "axis_clamp" not in code, "engrish/font.py code references manual axis_clamp"
    assert "_clamp_axis" not in code


# --- M8-AC8: 2 subset calls per form ---


@noto_gothic_required
def test_subset_call_counter_resets_and_counts() -> None:
    reset_subset_counter()
    assert get_subset_call_count() == 0

    cps = {0x10330, 0x10331, 0x10332}
    subset_for_style_pair(NOTO_GOTHIC, cps)
    assert get_subset_call_count() == 1

    subset_for_style_pair(NOTO_GOTHIC, cps)
    assert get_subset_call_count() == 2


@noto_gothic_required
def test_subset_for_style_pair_returns_per_source_list_with_subsetted_cmaps() -> None:
    """``subset_for_style_pair`` returns a list (no merge yet, since merge breaks
    on variable fonts). The single subsetted entry's cmap must equal the
    intersection of source cmap and requested codepoints (modulo dependency
    glyphs the subsetter may retain)."""
    reset_subset_counter()
    requested = {0x10330, 0x10331, 0x10332, 0x10333, 0x10334}
    subsetted_list = subset_for_style_pair(NOTO_GOTHIC, requested)
    assert isinstance(subsetted_list, list)
    assert len(subsetted_list) == 1
    sub_cmap = set(subsetted_list[0].getBestCmap().keys())
    src_cmap = source_font_cmap(NOTO_GOTHIC)
    expected_at_minimum = requested & src_cmap
    assert expected_at_minimum.issubset(sub_cmap), (
        f"requested intersection not preserved: missing {expected_at_minimum - sub_cmap}"
    )
    assert len(sub_cmap) < len(src_cmap), "subsetting should reduce cmap size"


# --- M8-AC9: pairwise merge ---


def test_pairwise_merge_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="at least one font"):
        pairwise_merge([])


@noto_gothic_required
def test_pairwise_merge_returns_single_input_unchanged() -> None:
    from fontTools.ttLib import TTFont

    font = TTFont(str(NOTO_GOTHIC))
    result = pairwise_merge([font])
    assert result is font


@noto_gothic_required
@noto_avestan_required
def test_pairwise_merge_combines_two_subsetted_fonts() -> None:
    """Merge subsetted Gothic + subsetted Avestan; verify merged cmap covers both.

    Uses single-source ``subset_for_style_pair`` calls (each returns a
    one-element list) and combines them via direct ``pairwise_merge``.
    Both inputs are static fonts so merge succeeds without
    instantiate-first preprocessing.
    """
    reset_subset_counter()
    gothic_sub = subset_for_style_pair(NOTO_GOTHIC, {0x10330, 0x10331, 0x10332})
    avestan_sub = subset_for_style_pair(NOTO_AVESTAN, {0x10B00, 0x10B01, 0x10B02})
    merged = pairwise_merge([gothic_sub[0], avestan_sub[0]])
    merged_cmap = set(merged.getBestCmap().keys())
    gothic_cmap = source_font_cmap(NOTO_GOTHIC)
    avestan_cmap = source_font_cmap(NOTO_AVESTAN)
    expected = ({0x10330, 0x10331, 0x10332} & gothic_cmap) | ({0x10B00, 0x10B01, 0x10B02} & avestan_cmap)
    assert expected.issubset(merged_cmap), (
        f"merged cmap missing expected codepoints: {expected - merged_cmap}"
    )


# --- M8-AC8 / 2b #5: instantiate_variable ---


@noto_var_required
def test_instantiate_variable_collapses_wght_axis() -> None:
    """Instantiating a variable font at a fixed weight should drop the fvar axis."""
    from fontTools.ttLib import TTFont

    src = TTFont(str(NOTO_SANS_VAR))
    assert "fvar" in src, "NotoSans[wght].ttf should be variable"
    instance = instantiate_variable(src, weight=400)
    # After instantiation the fvar table should be gone (or have no axes).
    if "fvar" in instance:
        assert instance["fvar"].axes == [], "instantiated font should have empty fvar.axes"
    # Otherwise fvar dropped entirely — also acceptable.


@noto_gothic_required
def test_instantiate_variable_passthrough_for_static_font() -> None:
    """Static (non-variable) fonts pass through unchanged."""
    from fontTools.ttLib import TTFont

    src = TTFont(str(NOTO_GOTHIC))
    assert "fvar" not in src
    result = instantiate_variable(src, weight=700)
    assert result is src
