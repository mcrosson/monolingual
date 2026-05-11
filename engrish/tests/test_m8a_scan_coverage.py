"""M8-A regression tests: codepoint scan + source discovery + 3-tier coverage check.

Live data: ``data/engrish/got/got-en-20260401.df`` (the 9,602-entry got merged .df
from M6) + ``fonts/Noto/NotoSansGothic-Regular.ttf`` (Gothic-script source).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engrish.font import (
    ALL_STYLES,
    BuildError,
    CoverageReport,
    ScannedCodepoints,
    STYLE_BOLD,
    STYLE_BOLD_ITALIC,
    STYLE_ITALIC,
    STYLE_REGULAR,
    WarningChannel,
    _classify_text_runs,
    coverage_check,
    scan_codepoints,
    source_font_cmap,
    union_source_coverage,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOT_DF = REPO_ROOT / "data" / "engrish" / "got" / "got-en-20260401.df"
NOTO_GOTHIC = REPO_ROOT / "fonts" / "Noto" / "NotoSansGothic-Regular.ttf"
NOTO_AVESTAN = REPO_ROOT / "fonts" / "Noto" / "NotoSansAvestan-Regular.ttf"


got_df_required = pytest.mark.skipif(
    not GOT_DF.exists(),
    reason="got merged .df absent; run `engrish generate --form got` first",
)
noto_gothic_required = pytest.mark.skipif(
    not NOTO_GOTHIC.exists(),
    reason="NotoSansGothic-Regular.ttf absent from fonts/Noto/",
)
noto_avestan_required = pytest.mark.skipif(
    not NOTO_AVESTAN.exists(),
    reason="NotoSansAvestan-Regular.ttf absent from fonts/Noto/",
)


# --- _classify_text_runs ---


def test_classify_text_runs_regular_only() -> None:
    runs = _classify_text_runs("hello world")
    assert runs == [(STYLE_REGULAR, "hello world")]


def test_classify_text_runs_bold_span() -> None:
    runs = _classify_text_runs("a<b>X</b>z")
    assert runs == [(STYLE_REGULAR, "a"), (STYLE_BOLD, "X"), (STYLE_REGULAR, "z")]


def test_classify_text_runs_italic_span() -> None:
    runs = _classify_text_runs("<i>foo</i>")
    assert runs == [(STYLE_ITALIC, "foo")]


def test_classify_text_runs_nested_bold_italic() -> None:
    runs = _classify_text_runs("<b><i>BI</i></b>")
    assert runs == [(STYLE_BOLD_ITALIC, "BI")]


def test_classify_text_runs_strong_em_aliases_bold_italic() -> None:
    runs = _classify_text_runs("<strong>S</strong> <em>E</em>")
    styles = [r[0] for r in runs if r[1].strip()]
    assert STYLE_BOLD in styles
    assert STYLE_ITALIC in styles


# --- scan_codepoints — live got ---


@got_df_required
def test_scan_codepoints_finds_gothic_codepoints_in_got_df() -> None:
    """Real got .df should produce a non-empty regular-style codepoint set
    that includes Gothic-script characters (U+10330..U+1034F)."""
    scanned = scan_codepoints(GOT_DF)
    assert isinstance(scanned, ScannedCodepoints)
    regular = scanned.per_style[STYLE_REGULAR]
    # Regular always populated (headword chars).
    assert regular, "expected non-empty regular codepoint set"
    # At least some Gothic-script chars present (U+10330-1034F).
    gothic = [cp for cp in regular if 0x10330 <= cp <= 0x1034F]
    assert gothic, f"no Gothic-script codepoints found in regular set; sample: {sorted(regular)[:10]}"


@got_df_required
def test_scan_codepoints_first_ref_populated() -> None:
    """Every codepoint in any style should map to a FirstRef with a real headword."""
    scanned = scan_codepoints(GOT_DF)
    sample_cp = next(iter(scanned.per_style[STYLE_REGULAR]))
    ref = scanned.first_ref[sample_cp]
    assert ref.headword
    # locale should be "Gothic" for got entries (FORM_NAMES["got"] = "Gothic").
    assert ref.locale == "Gothic", f"unexpected locale label: {ref.locale!r}"


@got_df_required
def test_scan_codepoints_italic_style_populated_for_etymology_text() -> None:
    """got entries' etymology paragraphs use <i>...</i>; italic codepoints should be non-empty."""
    scanned = scan_codepoints(GOT_DF)
    italic = scanned.per_style[STYLE_ITALIC]
    assert italic, "expected italic codepoints from <i>...</i> etymology spans in got"


# --- source_font_cmap + union_source_coverage ---


@noto_gothic_required
def test_source_font_cmap_returns_codepoints_for_noto_gothic() -> None:
    cmap = source_font_cmap(NOTO_GOTHIC)
    assert cmap, "NotoSansGothic-Regular cmap should be non-empty"
    # Gothic script range U+10330-1034F should be present.
    gothic = [cp for cp in cmap if 0x10330 <= cp <= 0x1034F]
    assert len(gothic) >= 20, f"NotoSansGothic should cover most Gothic codepoints; got {len(gothic)}"


@noto_gothic_required
@noto_avestan_required
def test_union_source_coverage_combines_two_fonts() -> None:
    cmap_gothic = source_font_cmap(NOTO_GOTHIC)
    cmap_avestan = source_font_cmap(NOTO_AVESTAN)
    union = union_source_coverage([NOTO_GOTHIC, NOTO_AVESTAN])
    assert union == cmap_gothic | cmap_avestan
    assert len(union) >= len(cmap_gothic)
    assert len(union) >= len(cmap_avestan)


# --- coverage_check ---


def _scanned_with(*per_style: tuple[str, set[int]]) -> ScannedCodepoints:
    s = ScannedCodepoints(per_style={st: set() for st in ALL_STYLES})
    for style, cps in per_style:
        s.per_style[style] = set(cps)
        for cp in cps:
            s.first_ref.setdefault(cp, FirstRef_for_test(style))
    return s


def FirstRef_for_test(style: str):
    from engrish.font import FirstRef
    return FirstRef(locale=style, headword=f"test_{style}")


def test_coverage_check_tier3_only_when_generated_is_none() -> None:
    """Skip Tier 1 / Tier 2; report only S − U."""
    scanned = _scanned_with((STYLE_REGULAR, {0x41, 0x42, 0x1F4A9}))
    source_union = {0x41, 0x42}
    report = coverage_check(scanned, source_union, generated_cmap=None)
    assert report.tier3_warnings == {0x1F4A9}
    assert report.tier1_missing == set()
    assert report.tier2_extras == set()


def test_coverage_check_tier1_raises_on_missing_intersection() -> None:
    """Codepoint in S ∩ U absent from generated_cmap → BuildError."""
    scanned = _scanned_with((STYLE_REGULAR, {0x41, 0x42}))
    source_union = {0x41, 0x42}
    generated_cmap = {0x41}  # missing 0x42
    with pytest.raises(BuildError, match="Tier-1"):
        coverage_check(scanned, source_union, generated_cmap=generated_cmap)


def test_coverage_check_tier2_raises_on_unjustified_extra() -> None:
    """Codepoint in generated_cmap not in S ∪ dependency_glyphs → BuildError."""
    scanned = _scanned_with((STYLE_REGULAR, {0x41}))
    source_union = {0x41, 0x42, 0x43}
    generated_cmap = {0x41, 0x42}  # 0x42 is extra (not in S, not in dep)
    with pytest.raises(BuildError, match="Tier-2"):
        coverage_check(
            scanned, source_union, generated_cmap=generated_cmap, dependency_glyphs=set()
        )


def test_coverage_check_tier2_passes_when_extras_in_dependency_glyphs() -> None:
    """A3 carve-out: dependency_glyphs whitelisted as legitimate extras."""
    scanned = _scanned_with((STYLE_REGULAR, {0x41}))
    source_union = {0x41, 0x42, 0x43}
    generated_cmap = {0x41, 0x42}
    dep = {0x42}  # subsetter retained 0x42 for GSUB
    report = coverage_check(
        scanned, source_union, generated_cmap=generated_cmap, dependency_glyphs=dep
    )
    assert report.tier1_missing == set()
    assert report.tier2_extras == set()


def test_coverage_check_returns_clean_report_when_everything_aligns() -> None:
    """Happy path: tier-1 satisfied, tier-2 satisfied, tier-3 empty."""
    scanned = _scanned_with((STYLE_REGULAR, {0x41, 0x42}))
    source_union = {0x41, 0x42}
    generated_cmap = {0x41, 0x42}
    report = coverage_check(scanned, source_union, generated_cmap=generated_cmap)
    assert report.tier1_missing == set()
    assert report.tier2_extras == set()
    assert report.tier3_warnings == set()


# --- WarningChannel ---


def test_warning_channel_writes_one_line_per_codepoint(tmp_path: Path) -> None:
    """M8-AC15: one warning line per codepoint, ``U+XXXX <locale>:<headword>`` format."""
    scanned = _scanned_with((STYLE_REGULAR, {0x1F4A9, 0x2603}))
    log_path = tmp_path / "build.log"
    artifact = tmp_path / "coverage_gaps.txt"
    ch = WarningChannel(log_path=log_path, coverage_gaps_path=artifact)
    ch.emit({0x1F4A9, 0x2603}, scanned)

    # Artifact contains exactly two lines, sorted by codepoint.
    lines = artifact.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines == [
        "U+2603 regular:test_regular",
        "U+1F4A9 regular:test_regular",
    ]
    # Log file mirrors the artifact lines.
    log_lines = log_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert sorted(log_lines) == sorted(lines)


def test_warning_channel_artifact_empty_when_no_gaps(tmp_path: Path) -> None:
    """Even with zero codepoints, the artifact is written (diffable across runs)."""
    scanned = ScannedCodepoints(per_style={s: set() for s in ALL_STYLES})
    artifact = tmp_path / "coverage_gaps.txt"
    ch = WarningChannel(log_path=None, coverage_gaps_path=artifact)
    ch.emit([], scanned)
    assert artifact.exists()
    assert artifact.read_text(encoding="utf-8") == ""


def test_warning_channel_two_runs_byte_identical(tmp_path: Path) -> None:
    """Two emissions with identical inputs produce byte-identical artifacts."""
    scanned = _scanned_with((STYLE_REGULAR, {0x1F4A9}))
    a1 = tmp_path / "a.txt"
    a2 = tmp_path / "b.txt"
    WarningChannel(log_path=None, coverage_gaps_path=a1).emit({0x1F4A9}, scanned)
    WarningChannel(log_path=None, coverage_gaps_path=a2).emit({0x1F4A9}, scanned)
    assert a1.read_bytes() == a2.read_bytes()


# --- Live got × NotoSansGothic — Tier-3 happy path ---


@got_df_required
@noto_gothic_required
def test_got_against_noto_gothic_only_emits_tier3_warnings_for_non_covered_scripts() -> None:
    """Real got scan vs Noto Gothic only: Gothic-script + bundled-Latin covered;
    other scripts in got's etymology paragraphs (Cyrillic, Greek, Hebrew, etc.)
    fall into Tier-3 because NotoSansGothic doesn't ship them.

    NOTE: NotoSansGothic-Regular.ttf bundles basic Latin (U+0020..U+007E) as a
    fallback, so Latin characters in got entries are NOT in S − U. Use a
    Cyrillic codepoint (U+0430 'а') as a representative tier-3 hit instead.
    """
    scanned = scan_codepoints(GOT_DF)
    source_union = source_font_cmap(NOTO_GOTHIC)
    report = coverage_check(scanned, source_union, generated_cmap=None)
    assert report.tier3_warnings, "expected non-empty Tier-3 (non-Gothic scripts in etymology)"
    # got etymology paragraphs reference Proto-Germanic / OCS / Greek; Cyrillic
    # 'а' (U+0430) shows up in Cyrillic-script glosses absent from NotoSansGothic.
    cyrillic_a = 0x0430
    if cyrillic_a in scanned.all_codepoints:
        assert cyrillic_a in report.tier3_warnings, (
            f"U+0430 used by got but unexpectedly covered by NotoSansGothic"
        )
