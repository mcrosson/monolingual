"""M8-F: live got integration sweep + per-primitive unit tests.

Live data: ``data/engrish/got/fonts/{Regular,Bold,Italic,BoldItalic}.ttf`` +
``coverage_gaps.txt``, produced by ``engrish font --form got`` against
the M6-built got merged .df.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOT_FONTS = REPO_ROOT / "data" / "engrish" / "got" / "fonts"
GOT_DF = REPO_ROOT / "data" / "engrish" / "got" / "got-en-20260401.df"


def _all_four_ttfs() -> bool:
    if not GOT_FONTS.exists():
        return False
    return all((GOT_FONTS / f"{name}.ttf").exists() for name in ("Regular", "Bold", "Italic", "BoldItalic"))


live_required = pytest.mark.skipif(
    not _all_four_ttfs(),
    reason="got fonts not built; run `engrish font --form got` first",
)
got_df_required = pytest.mark.skipif(
    not GOT_DF.exists(),
    reason="got merged .df absent",
)


# --- M8-AC1: 4 TTFs produced ---


@live_required
def test_got_font_run_produced_all_four_ttfs() -> None:
    for name in ("Regular", "Bold", "Italic", "BoldItalic"):
        path = GOT_FONTS / f"{name}.ttf"
        assert path.exists() and path.stat().st_size > 0, f"missing or empty: {path}"


# --- M8-AC15: coverage_gaps.txt artifact ---


@live_required
def test_got_coverage_gaps_artifact_exists_and_format() -> None:
    """Coverage gaps file present; each line ``U+XXXX <locale>:<headword>``."""
    artifact = GOT_FONTS / "coverage_gaps.txt"
    assert artifact.exists()
    text = artifact.read_text(encoding="utf-8")
    if not text.strip():
        return  # empty is acceptable (means no gaps)
    import re

    line_re = re.compile(r"^U\+[0-9A-F]{4,6} [^:]+:.+$")
    for line in text.rstrip("\n").split("\n"):
        assert line_re.match(line), f"malformed coverage_gaps line: {line!r}"


# --- M8-AC4: per-variant Subfamily distinct ---


@live_required
def test_got_four_ttfs_have_distinct_subfamilies() -> None:
    """Each generated TTF carries a distinct ``name`` table ID 2 (Subfamily)."""
    from fontTools.ttLib import TTFont

    expected_pairs = {
        "Regular.ttf": "Regular",
        "Bold.ttf": "Bold",
        "Italic.ttf": "Italic",
        "BoldItalic.ttf": "Bold Italic",
    }
    actuals: dict[str, str] = {}
    for filename, expected in expected_pairs.items():
        font = TTFont(str(GOT_FONTS / filename))
        rec = font["name"].getName(2, 3, 1, 0x0409)
        assert rec is not None, f"{filename} missing name ID 2 (Windows record)"
        actuals[filename] = rec.toUnicode()
        assert actuals[filename] == expected, (
            f"{filename} Subfamily {actuals[filename]!r}, expected {expected!r}"
        )
    assert len(set(actuals.values())) == 4


# --- M8-AC11: byte-determinism (run again, compare) ---


@live_required
@got_df_required
def test_got_font_two_runs_byte_identical(tmp_path: Path) -> None:
    """Re-run ``build_form_fonts`` against the same inputs; bytes match the on-disk run."""
    from engrish.config import FONTS_DIR
    from engrish.font import build_form_fonts

    out_dir = tmp_path / "fonts"
    build_form_fonts(
        form="got",
        locales=["got"],
        df_path=GOT_DF,
        out_dir=out_dir,
        fonts_dir=FONTS_DIR,
        log_path=None,
    )
    for name in ("Regular", "Bold", "Italic", "BoldItalic"):
        a = (GOT_FONTS / f"{name}.ttf").read_bytes()
        b = (out_dir / f"{name}.ttf").read_bytes()
        assert a == b, f"{name}.ttf differs between on-disk run and tmp_path rerun"


# --- M8-AC8: subset counter checked at build_form_fonts boundary ---


@got_df_required
def test_build_form_fonts_calls_subset_for_style_pair_exactly_twice(tmp_path: Path) -> None:
    """The high-level build invokes subset_for_style_pair exactly 2 times per form
    (one for Regular+Bold, one for Italic+BoldItalic)."""
    from engrish.config import FONTS_DIR
    from engrish.font import build_form_fonts, get_subset_call_count, reset_subset_counter

    reset_subset_counter()
    build_form_fonts(
        form="got",
        locales=["got"],
        df_path=GOT_DF,
        out_dir=tmp_path / "fonts",
        fonts_dir=FONTS_DIR,
        log_path=None,
    )
    assert get_subset_call_count() == 2, (
        f"expected exactly 2 subset_for_style_pair calls per form, got {get_subset_call_count()}"
    )


# --- M8-AC13: per-primitive unit tests for the M8 rewrite-era helpers ---


def test_primitives_all_present_in_engrish_font_module() -> None:
    """Smoke check that the M8 primitives the chunked rewrite contracted are
    importable from ``engrish.font``."""
    from engrish import font as f

    for name in (
        # M8-A
        "scan_codepoints", "source_font_cmap", "union_source_coverage",
        "coverage_check", "WarningChannel", "BuildError",
        # M8-B
        "subset_for_style_pair", "instantiate_variable", "pairwise_merge",
        "instantiate_and_merge_static",
        "reset_subset_counter", "get_subset_call_count",
        # M8-C
        "set_subfamily", "propagate_hhea",
        # M8-D
        "assert_under_format_limits", "save_font_deterministic",
        # M8-E
        "build_form_fonts", "resolve_source_fonts", "stem_to_path",
    ):
        assert hasattr(f, name), f"engrish.font missing primitive: {name}"


def test_stem_to_path_resolves_variable_first_then_static_then_italic(tmp_path: Path) -> None:
    """Resolve preference: variable [wght] > static Regular > static Italic."""
    from engrish.font import stem_to_path

    stem = "FakeFont"
    # All three present → variable wins.
    (tmp_path / f"{stem}[wght].ttf").write_bytes(b"v")
    (tmp_path / f"{stem}-Regular.ttf").write_bytes(b"r")
    (tmp_path / f"{stem}-Italic.ttf").write_bytes(b"i")
    assert stem_to_path(stem, tmp_path).name == f"{stem}[wght].ttf"

    # Drop variable → static Regular wins.
    (tmp_path / f"{stem}[wght].ttf").unlink()
    assert stem_to_path(stem, tmp_path).name == f"{stem}-Regular.ttf"

    # Drop Regular → italic-only fallback.
    (tmp_path / f"{stem}-Regular.ttf").unlink()
    assert stem_to_path(stem, tmp_path).name == f"{stem}-Italic.ttf"

    # All gone → None.
    (tmp_path / f"{stem}-Italic.ttf").unlink()
    assert stem_to_path(stem, tmp_path) is None


def test_stem_to_path_falls_back_to_otf_for_cjk_fonts(tmp_path: Path) -> None:
    """M13 fix (2026-05-09) — stem_to_path must resolve .otf fonts and other
    non-canonical filename variants by delegating to ``font_io.find_font_file``
    when the 3 hardcoded .ttf candidates miss.

    Pre-fix the function only checked ``<stem>[wght].ttf``,
    ``<stem>-Regular.ttf``, ``<stem>-Italic.ttf`` — silently returning None
    for CJK ``.otf`` fonts (NotoSansJP-Regular.otf, NotoSansSC-Regular.otf,
    etc.) and color-emoji variants (NotoColorEmoji-noflags.ttf), causing
    ja/zh/en form-fonts to ship without CJK + emoji glyph coverage.
    """
    from engrish.font import stem_to_path

    # CJK .otf — pre-fix returned None; post-fix must resolve.
    (tmp_path / "NotoSansJP-Regular.otf").write_bytes(b"j")
    assert stem_to_path("NotoSansJP", tmp_path).name == "NotoSansJP-Regular.otf"

    # Color emoji with -noflags suffix.
    (tmp_path / "NotoColorEmoji-noflags.ttf").write_bytes(b"e")
    assert stem_to_path("NotoColorEmoji", tmp_path).name == "NotoColorEmoji-noflags.ttf"


def test_resolve_source_fonts_excludes_color_bitmap_fonts() -> None:
    """M13 fix (2026-05-09) — color-bitmap fonts (NotoColorEmoji) MUST be
    excluded from the subset+merge pipeline.

    fontTools.merge.Merger picks the first source as the base. If the base
    is a CBDT/CBLC color-bitmap font (no glyf table), every subsequent TT
    outline source fails to merge because the merger can't combine bitmap
    and outline tables. Pre-fix (D40-only), NotoColorEmoji-noflags.ttf was
    alphabetically first in resolve_source_fonts output for en/ja/zh, so
    every other source was silently dropped → 600-glyph output with no CJK.
    """
    from engrish.config import FONTS_DIR
    from engrish.font import resolve_source_fonts, _FONT_STEMS_EXCLUDED_FROM_MERGE

    assert "NotoColorEmoji" in _FONT_STEMS_EXCLUDED_FROM_MERGE

    for locales in (["en"], ["ja"], ["zh"], ["zh", "ja"]):
        paths = resolve_source_fonts(locales, FONTS_DIR)
        names = [p.name for p in paths]
        for excluded in _FONT_STEMS_EXCLUDED_FROM_MERGE:
            for n in names:
                assert excluded not in n, (
                    f"{locales}: resolve_source_fonts should exclude {excluded!r} "
                    f"but returned {n!r}"
                )


def test_pairwise_merge_preserves_cff_otf_sources_after_conversion() -> None:
    """M13 fix (2026-05-09) — pairwise_merge of TT+CFF sources MUST retain
    glyphs from the CFF (.otf) source.

    Pre-fix, ``fontTools.merge.Merger`` raised ``AttributeError: 'NotImplementedType'
    object has no attribute 'cff'`` on TT+CFF inputs; ``pairwise_merge`` caught
    the exception and silently dropped the CFF source, causing CJK fonts
    (NotoSansJP/SC/TC/KR-Regular.otf) to vanish from merged outputs.

    Post-fix, ``_convert_cff_to_tt`` runs before merge to convert CFF outlines
    to glyf via cu2qu; the merge then succeeds and the merged font carries
    glyphs from both sources.
    """
    from engrish.config import FONTS_DIR
    from engrish.font import _subset_one, pairwise_merge

    ns_path = FONTS_DIR / "NotoSans[wght].ttf"
    jp_path = FONTS_DIR / "NotoSansJP-Regular.otf"
    if not (ns_path.exists() and jp_path.exists()):
        pytest.skip(f"need both {ns_path.name} and {jp_path.name} for this test")

    src_ns = _subset_one(str(ns_path), {0x41, 0x42, 0x43})  # ABC (Latin)
    src_jp = _subset_one(str(jp_path), {0x4E2D, 0x6587, 0x65E5, 0x672C})  # 中文日本 (CJK)
    merged = pairwise_merge([src_ns, src_jp])
    cmap = merged.getBestCmap()
    for cp in (0x41, 0x4E2D, 0x6587, 0x65E5, 0x672C):
        assert cp in cmap, f"merged cmap missing {hex(cp)}; CFF source dropped silently"


def test_stem_to_path_and_find_font_file_agree_on_engrish_json_stems() -> None:
    """M13-AC9 follow-up — the two stem-resolvers (font.stem_to_path and
    font_io.find_font_file) must agree on every stem referenced in
    engrish.json. Pre-fix they diverged for .otf-only stems.
    """
    from engrish.config import _ENGRISH_CFG, FONTS_DIR, SEED_FONTS
    from engrish.font import stem_to_path
    from engrish.font_io import find_font_file

    needed = set(SEED_FONTS)
    for cfg in _ENGRISH_CFG.values():
        needed.update(cfg.get("fonts", []))

    diffs: list[str] = []
    for stem in sorted(needed):
        a = stem_to_path(stem, FONTS_DIR)
        b = find_font_file(stem, FONTS_DIR)
        if a != b:
            diffs.append(f"  {stem}: stem_to_path={a}, find_font_file={b}")
    assert not diffs, (
        f"{len(diffs)} stems where stem_to_path and find_font_file disagree:\n"
        + "\n".join(diffs)
    )


def test_resolve_source_fonts_for_got_returns_real_paths() -> None:
    from engrish.config import FONTS_DIR
    from engrish.font import resolve_source_fonts

    paths = resolve_source_fonts(["got"], FONTS_DIR)
    assert paths, "expected at least one source font for got"
    for p in paths:
        assert p.exists(), f"resolved path does not exist: {p}"
