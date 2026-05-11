"""M8-D regression tests: overflow assertion + byte-deterministic save."""

from __future__ import annotations

from pathlib import Path

import pytest

from engrish.font import (
    BuildError,
    assert_under_format_limits,
    save_font_deterministic,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOTO_GOTHIC = REPO_ROOT / "fonts" / "Noto" / "NotoSansGothic-Regular.ttf"

noto_gothic_required = pytest.mark.skipif(
    not NOTO_GOTHIC.exists(),
    reason="NotoSansGothic-Regular.ttf absent",
)


# --- M8-AC3: overflow assertion ---


@noto_gothic_required
def test_assert_under_format_limits_passes_for_noto_gothic() -> None:
    """A real, well-formed font should pass the limit check."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(NOTO_GOTHIC))
    assert_under_format_limits(font)  # must not raise


@noto_gothic_required
def test_assert_under_format_limits_raises_when_glyph_count_overflows() -> None:
    """Synthetic oversized: monkey-patch numGlyphs > 65534 → BuildError."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(NOTO_GOTHIC))
    font["maxp"].numGlyphs = 70000
    with pytest.raises(BuildError, match=r"Glyph count.*≥ TrueType.*ceiling"):
        assert_under_format_limits(font)


@noto_gothic_required
def test_assert_under_format_limits_raises_on_cmap_format4_smp_codepoint() -> None:
    """Synthetic broken cmap: format-4 subtable carrying SMP codepoint → BuildError."""
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    font = TTFont(str(NOTO_GOTHIC))
    # Inject a synthetic format-4 subtable whose cmap dict contains a SMP codepoint.
    bad_sub = CmapSubtable.newSubtable(4)
    bad_sub.platformID = 3
    bad_sub.platEncID = 1
    bad_sub.language = 0
    bad_sub.cmap = {0x10331: "g_synthetic"}  # SMP codepoint; format-4 can't carry it
    font["cmap"].tables.append(bad_sub)
    with pytest.raises(BuildError, match=r"cmap subtable.*format=4.*above BMP ceiling"):
        assert_under_format_limits(font)


# --- M8-AC11: byte-determinism ---


@noto_gothic_required
def test_save_font_deterministic_two_runs_byte_identical(tmp_path: Path) -> None:
    """Two ``save_font_deterministic`` runs with identical input produce identical bytes."""
    from fontTools.ttLib import TTFont

    out1 = tmp_path / "a.ttf"
    out2 = tmp_path / "b.ttf"

    font_a = TTFont(str(NOTO_GOTHIC))
    save_font_deterministic(font_a, out1)

    font_b = TTFont(str(NOTO_GOTHIC))
    save_font_deterministic(font_b, out2)

    assert out1.read_bytes() == out2.read_bytes(), (
        "two save_font_deterministic runs produced different bytes — likely a "
        "non-deterministic head field or table-order drift"
    )


@noto_gothic_required
def test_save_font_deterministic_pins_head_timestamps(tmp_path: Path) -> None:
    """``head.created`` and ``head.modified`` should be the fixed value after save.

    The fixed value (2082844800) corresponds to 1970-01-01 in OpenType seconds.
    fontTools accepts this verbatim without auto-reinterpretation as Unix-epoch.
    """
    from fontTools.ttLib import TTFont

    from engrish.font import _FIXED_HEAD_TIMESTAMP

    out = tmp_path / "pinned.ttf"
    font = TTFont(str(NOTO_GOTHIC))
    save_font_deterministic(font, out)

    reloaded = TTFont(str(out))
    assert reloaded["head"].created == _FIXED_HEAD_TIMESTAMP
    assert reloaded["head"].modified == _FIXED_HEAD_TIMESTAMP
