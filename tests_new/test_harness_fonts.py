"""M0-AC3: fontTools helper returns cmap set + glyph count + name-table for a fixture TTF."""

from __future__ import annotations

from pathlib import Path

from tests_new.harness import fonts

FIXTURE = Path(__file__).parent.parent / "fonts" / "Noto" / "NotoSansAvestan-Regular.ttf"


def test_describe_returns_expected_shape() -> None:
    assert FIXTURE.exists(), f"fixture TTF not found: {FIXTURE}"
    result = fonts.describe(FIXTURE)

    assert isinstance(result["cmap"], set)
    assert len(result["cmap"]) > 0
    assert all(isinstance(cp, int) for cp in result["cmap"])

    assert isinstance(result["glyph_count"], int)
    assert result["glyph_count"] > 0

    assert isinstance(result["name_table"], dict)
    # Every conformant TTF has at least Font Family (nameID 1) and Subfamily (nameID 2).
    assert 1 in result["name_table"]
    assert 2 in result["name_table"]
