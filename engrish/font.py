"""Font generation — M8 rewrite (chunked per ``[[task-m8-font-rewrite-chunks]]``).

The pre-M8 ``engrish/font_legacy.py`` was deleted at M11-AC4 (2026-05-02);
its add-language helpers (parallel SQLite scan + font detection) were
migrated to ``engrish.stages.add_language_stage`` and its I/O helpers to
``engrish.font_io``. The generation pipeline below is the M8 rewrite,
landed in 6 chunks (M8-A through M8-F).

M8-A surface:
- ``scan_codepoints(df_path)`` — per-style codepoint sets + first-referencing-entry
  map, walked from a merged ``.df`` via ``engrish.df_reader.iter_entries``
  (streaming, low-memory).
- ``source_font_cmap(path)`` / ``union_source_coverage(paths)`` —
  ``fontTools.ttLib.TTFont``-backed cmap discovery for source fonts.
- ``coverage_check(scanned, source_union, generated_cmap, dependency_glyphs)``
  — three-tier check (M8-AC2):
  1. Tier 1 strict (S ∩ U ⊆ generated): missing → ``BuildError``.
  2. Tier 2 strict-no-extras with A3 carve-out (generated ⊆ S ∪ dep_glyphs):
     extras → ``BuildError``.
  3. Tier 3 warnings (S − U): codepoints used by StarDict but absent from
     every configured source font; emitted via ``WarningChannel``, NOT
     a build failure.
- ``WarningChannel`` — per-codepoint warnings (M8-AC15) to stderr + log file +
  ``data/engrish/<form>/fonts/coverage_gaps.txt`` build artifact. Format is
  fixed and diffable: ``U+XXXX <locale>:<headword>`` per line.
- ``BuildError`` — hard-fail class for tier-1 / tier-2 violations and (in
  M8-D) glyph-count overflow.

M8-B surface:

M8-C surface:

M8-D surface:

M8-E surface:
- ``resolve_source_fonts(locales)`` — given the form's locale list, returns the
  ordered list of source-font paths to feed the subset+merge pipeline. Reads
  ``engrish.json``'s per-language ``fonts`` field; resolves each stem to the
  actual file via ``stem_to_path``.
- ``stem_to_path(stem, fonts_dir)`` — turn ``"NotoSans"`` into the actual
  ``fonts/Noto/NotoSans[wght].ttf`` (variable-axis preferred, static fallback).
- ``build_form_fonts(form, locales, df_path, out_dir, log_path)`` — high-level
  orchestrator: scan codepoints, resolve sources, subset/merge per style-pair,
  instantiate at 4 weights, set subfamilies, propagate hhea, assert under
  limits, save deterministic, run coverage check, emit Tier-3 warnings.

- ``assert_under_format_limits(ttfont)`` — pre-save guard against the
  TrueType / OpenType structural ceilings that silently corrupt at lookup
  time when exceeded:
  - ``maxp.numGlyphs < 65535`` (the 16-bit ``GlyphID`` ceiling).
  - cmap subtables remain in supported encoding ranges.
  Raises ``BuildError`` with an actionable message on violation.
- ``save_font_deterministic(ttfont, path)`` — wrap ``TTFont.save`` with
  fixed ``head.created`` / ``head.modified`` timestamps + canonical table
  order so two runs produce byte-identical output. fontTools' default save
  is mostly deterministic in 4.x; this wrapper closes the timestamp gap.

- ``set_subfamily(ttfont, subfamily)`` — write ``name`` table ID 2 across the
  Mac / Windows / Unicode platform records so every reader (sdcv, KOReader,
  Crosspoint) sees the same Subfamily label. Per M8-AC4: each of the four
  generated TTFs must end up with a distinct Subfamily ("Regular", "Bold",
  "Italic", "Bold Italic"); the legacy code's all-"Regular" fallthrough is
  rejected.
- ``propagate_hhea(target, source_paths)`` — read each source's
  ``hhea.ascent`` / ``hhea.descent`` / ``hhea.lineGap``, aggregate
  (max ascent, min descent, max lineGap), write to target. Per M8-AC5: no
  hard-coded 800 / -200 unless inline-justified. Aggregating across sources
  ensures the merged font's vertical metrics fit every script it carries.

- ``instantiate_variable(source, weight)`` — ``varLib.instancer.instantiateVariableFont``
  at ``axisLimits={"wght": weight}`` with ``OverlapMode.KEEP_AND_SET_FLAGS``
  (the documented enum member, not ``overlap=0``).
- ``subset_for_style_pair(source, codepoints)`` — one ``fontTools.subset.Subsetter``
  pass per style-pair (Regular+Bold OR Italic+BoldItalic). Increments the
  module-level ``_SUBSET_CALL_COUNT`` so M8-AC8 can assert exactly two calls
  per form.
- ``pairwise_merge(fonts)`` — combine multiple subsetted source fonts into one
  via repeated pairwise ``fontTools.merge.Merger``. Documented rationale for
  pairwise (vs flat) merge per M8-AC9.
- ``reset_subset_counter()`` / ``get_subset_call_count()`` — instrumentation
  helpers for tests.

Future chunks:
- M8-C: per-variant ``name`` table + ``hhea`` propagation + axis-clamp removal.
- M8-D: overflow assertion + byte-determinism.
- M8-E: CLI + stage orchestrator + ``update-fonts``.
- M8-F: live integration sweep + per-primitive unit tests.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from engrish.df_reader import iter_entries

log = logging.getLogger(__name__)

# Style label literals (string-typed for low ceremony; promoted to enum if it pays off later).
STYLE_REGULAR = "regular"
STYLE_BOLD = "bold"
STYLE_ITALIC = "italic"
STYLE_BOLD_ITALIC = "bold_italic"

ALL_STYLES: tuple[str, ...] = (STYLE_REGULAR, STYLE_BOLD, STYLE_ITALIC, STYLE_BOLD_ITALIC)


class BuildError(Exception):
    """Hard-fail font build error.

    Raised on tier-1 / tier-2 coverage violations (M8-AC2), glyph-count overflow
    (M8-AC3, lands in M8-D), and other hard failures. The build pipeline is
    expected to abort the form; the user follows up against the message.
    """


@dataclass(frozen=True)
class FirstRef:
    """First StarDict entry that referenced a given codepoint."""

    locale: str   # the entry's <h3>...</h3> label, or "" if absent
    headword: str


@dataclass
class ScannedCodepoints:
    """Per-style codepoint sets + first-referencing-entry map for one merged ``.df``."""

    per_style: dict[str, set[int]] = field(default_factory=dict)
    first_ref: dict[int, FirstRef] = field(default_factory=dict)

    @property
    def all_codepoints(self) -> set[int]:
        out: set[int] = set()
        for cps in self.per_style.values():
            out |= cps
        return out


@dataclass
class CoverageReport:
    """Per-tier diagnostics from ``coverage_check``."""

    tier1_missing: set[int] = field(default_factory=set)
    tier2_extras: set[int] = field(default_factory=set)
    tier3_warnings: set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Style classification — lightweight HTML walker
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"</?([a-zA-Z]+)[^>]*>")


def _classify_text_runs(html: str) -> list[tuple[str, str]]:
    """Walk ``html`` and return ``(style, text_run)`` pairs.

    Bold = inside ``<b>`` / ``<strong>``. Italic = inside ``<i>`` / ``<em>``.
    Both nested = bold_italic. Outside any styling tag = regular.

    The walker is intentionally simple — wikidict-rendered HTML uses a small,
    predictable subset of these tags (Jinja-templated). A full HTML parser
    would be overkill and slower.
    """
    runs: list[tuple[str, str]] = []
    bold_depth = 0
    italic_depth = 0
    pos = 0

    def _current_style() -> str:
        b = bold_depth > 0
        i = italic_depth > 0
        if b and i:
            return STYLE_BOLD_ITALIC
        if b:
            return STYLE_BOLD
        if i:
            return STYLE_ITALIC
        return STYLE_REGULAR

    for m in _TAG_RE.finditer(html):
        text = html[pos : m.start()]
        if text:
            runs.append((_current_style(), text))
        tag = m.group(1).lower()
        is_close = m.group(0).startswith("</")
        delta = -1 if is_close else 1
        if tag in ("b", "strong"):
            bold_depth = max(0, bold_depth + delta)
        elif tag in ("i", "em"):
            italic_depth = max(0, italic_depth + delta)
        pos = m.end()
    if pos < len(html):
        runs.append((_current_style(), html[pos:]))
    return runs


def _entry_locale(entry_bytes: bytes) -> str:
    """Pull the first ``<h3>...</h3>`` label from an entry; "" if absent."""
    m = re.search(rb"<h3>([^<]+)</h3>", entry_bytes)
    return m.group(1).decode("utf-8", "replace") if m else ""


def _entry_html_body(entry_bytes: bytes) -> str:
    """Slice ``<html>...</html>`` body content out of an entry."""
    start = entry_bytes.find(b"<html>")
    end = entry_bytes.find(b"</html>")
    if start == -1 or end == -1:
        return ""
    return entry_bytes[start + len(b"<html>") : end].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# scan_codepoints — main entry for M8-A
# ---------------------------------------------------------------------------


def scan_codepoints(df_path: Path) -> ScannedCodepoints:
    """Walk a merged ``.df``; return per-style codepoint sets + first-ref map.

    Headword characters always count as ``regular`` style (the StarDict idx
    stores them as raw bytes, not styled text). Body text is classified per
    style via ``_classify_text_runs``. Whitespace is excluded (no font shapes
    a space; tracking U+0020 across every entry would add noise).
    """
    result = ScannedCodepoints(per_style={s: set() for s in ALL_STYLES})

    for headword, entry_bytes in iter_entries(df_path):
        locale = _entry_locale(entry_bytes)

        for ch in headword:
            cp = ord(ch)
            result.per_style[STYLE_REGULAR].add(cp)
            result.first_ref.setdefault(cp, FirstRef(locale, headword))

        body = _entry_html_body(entry_bytes)
        if not body:
            continue

        for style, text in _classify_text_runs(body):
            for ch in text:
                if ch.isspace():
                    continue
                cp = ord(ch)
                result.per_style[style].add(cp)
                result.first_ref.setdefault(cp, FirstRef(locale, headword))

    return result


# ---------------------------------------------------------------------------
# Source-font cmap discovery
# ---------------------------------------------------------------------------


def source_font_cmap(font_path: Path) -> set[int]:
    """Return the codepoints supported by ``font_path`` (via ``getBestCmap``)."""
    from fontTools.ttLib import TTFont

    ttf = TTFont(str(font_path), lazy=True)
    try:
        return set(ttf.getBestCmap().keys())
    finally:
        ttf.close()


def union_source_coverage(font_paths: Iterable[Path]) -> set[int]:
    """``U`` = union of all source-font cmaps."""
    u: set[int] = set()
    for p in font_paths:
        u |= source_font_cmap(p)
    return u


# ---------------------------------------------------------------------------
# Three-tier coverage check (M8-AC2)
# ---------------------------------------------------------------------------


def coverage_check(
    scanned: ScannedCodepoints,
    source_union: set[int],
    generated_cmap: set[int] | None = None,
    dependency_glyphs: set[int] | None = None,
) -> CoverageReport:
    """Three-tier coverage check per ``[[task-round-4-acceptance-criteria]]`` M8-AC2.

    - **Tier 1**: every ``S ∩ U`` codepoint MUST appear in ``generated_cmap``.
      Missing → ``BuildError``. (We had a source for it but the build dropped
      it — that's a regression.)
    - **Tier 2**: ``generated_cmap`` ⊆ ``S ∪ dependency_glyphs``. Extras →
      ``BuildError``. (The build carries codepoints not in StarDict and not
      justified as subsetter dependencies.)
    - **Tier 3**: codepoints in ``S − U`` are emitted as warnings (user must
      follow up; not a build failure).

    ``generated_cmap`` / ``dependency_glyphs`` may be ``None`` when the build
    hasn't run yet (e.g., scan-only invocations from CI). In that case only
    the Tier-3 set is computed; Tier 1 / 2 are skipped without raising.
    """
    s = scanned.all_codepoints
    report = CoverageReport()
    report.tier3_warnings = s - source_union

    if generated_cmap is not None:
        s_inter_u = s & source_union
        report.tier1_missing = s_inter_u - generated_cmap
        if report.tier1_missing:
            sample = sorted(report.tier1_missing)[:8]
            raise BuildError(
                f"Tier-1 coverage violation: {len(report.tier1_missing)} "
                f"codepoint(s) in S ∩ U absent from generated cmap. "
                f"First few: {', '.join(f'U+{cp:04X}' for cp in sample)}"
            )

        if dependency_glyphs is None:
            dependency_glyphs = set()
        allowed = s | dependency_glyphs
        report.tier2_extras = generated_cmap - allowed
        if report.tier2_extras:
            sample = sorted(report.tier2_extras)[:8]
            raise BuildError(
                f"Tier-2 coverage violation: {len(report.tier2_extras)} "
                f"codepoint(s) in generated cmap outside S ∪ dependency_glyphs. "
                f"First few: {', '.join(f'U+{cp:04X}' for cp in sample)}"
            )

    return report


# ---------------------------------------------------------------------------
# WarningChannel (M8-AC15)
# ---------------------------------------------------------------------------


class WarningChannel:
    """Per-codepoint coverage-gap warning emitter.

    M8-AC15 contract:
    - One warning per codepoint, never aggregated.
    - Each line: ``U+XXXX <locale>:<headword>`` — fixed, machine-readable, diffable.
    - Written to stderr (operator visibility), log file (CI capture), and the
      ``coverage_gaps.txt`` build artifact (long-term diff).
    - Existence of warnings does NOT block the build; the artifact records
      the gaps for user follow-up.
    - The artifact is rewritten unconditionally on every ``emit`` call so two
      runs with identical inputs produce identical artifact bytes.
    """

    def __init__(self, log_path: Path | None, coverage_gaps_path: Path) -> None:
        self.log_path = log_path
        self.coverage_gaps_path = coverage_gaps_path
        self._lines: list[str] = []

    def emit(self, codepoints: Iterable[int], scanned: ScannedCodepoints) -> None:
        """Emit one warning per codepoint and rewrite the coverage_gaps artifact.

        Both ``log_path`` and ``coverage_gaps.txt`` are rewritten unconditionally
        (M9-AC1 fix): prior content from earlier runs is discarded so two
        invocations on identical input produce byte-identical artifacts. Earlier
        revisions opened ``log_path`` in append mode, which let stale build.log
        content accumulate across runs and broke determinism.
        """
        for cp in sorted(codepoints):
            ref = scanned.first_ref.get(cp)
            locale = ref.locale if ref else ""
            headword = ref.headword if ref else ""
            line = f"U+{cp:04X} {locale}:{headword}"
            self._lines.append(line)
            print(line, file=sys.stderr)

        body = "\n".join(self._lines) + ("\n" if self._lines else "")

        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(body, encoding="utf-8")

        self.coverage_gaps_path.parent.mkdir(parents=True, exist_ok=True)
        self.coverage_gaps_path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# M8-B: subsetting + variable-font instantiation + pairwise merge
# ---------------------------------------------------------------------------

# Module-level counter for M8-AC8: per form, we expect exactly 2 calls to
# ``subset_for_style_pair`` (one for Regular+Bold, one for Italic+BoldItalic).
_SUBSET_CALL_COUNT: int = 0


def reset_subset_counter() -> None:
    """Reset the subset-call counter (test instrumentation only)."""
    global _SUBSET_CALL_COUNT
    _SUBSET_CALL_COUNT = 0


def get_subset_call_count() -> int:
    """Read the current subset-call counter."""
    return _SUBSET_CALL_COUNT


def _subset_one(source, codepoints: set[int]):
    """Internal: subset a single source font to ``codepoints`` (no counter).

    The counter lives at the form-level orchestration boundary — see
    ``subset_for_style_pair``.
    """
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    if isinstance(source, (str, Path)):
        font = TTFont(str(source))
    else:
        font = source

    options = Options()
    # Retain layout features; let fontTools choose dependency glyphs for
    # GSUB / combining marks / NFD targets (the A3 carve-out documented
    # in M8-AC2 Tier-2).
    options.layout_features = ["*"]
    options.glyph_names = True

    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    return font


def subset_for_style_pair(sources, codepoints: set[int]):
    """Subset every source to ``codepoints`` and return the per-source list.

    Form-level operation: M8-AC8 asks for **exactly two calls per form**
    (one for Regular+Bold style-pair, one for Italic+BoldItalic). The
    counter increments once per outer call regardless of how many source
    fonts the orchestrator passes in.

    **Returns a list, NOT a merged font.** ``fontTools.merge.Merger`` chokes
    on variable fonts (``VarStore`` has no ``mergeMap``), so the merge has
    to happen AFTER ``instantiate_variable`` collapses each source to static
    at its target weight. The orchestrator's per-weight loop calls
    ``instantiate_and_merge_static`` on this list to produce one TTF per
    (style-pair × weight) combination.

    ``sources`` is a single path / TTFont OR a list of paths / TTFonts;
    single values are normalized to a 1-element list. If the result list
    has one element, ``pairwise_merge`` (called downstream) returns it
    unchanged — so M8-B's single-source unit tests keep working.
    """
    global _SUBSET_CALL_COUNT
    _SUBSET_CALL_COUNT += 1

    if not isinstance(sources, list):
        sources = [sources]
    if not sources:
        raise ValueError("subset_for_style_pair requires at least one source")

    return [_subset_one(src, codepoints) for src in sources]


def instantiate_and_merge_static(subsetted_list, weight: int):
    """Instantiate each subsetted source at ``weight``, then ``pairwise_merge``.

    Splitting this from ``subset_for_style_pair`` is what works around the
    fontTools merge limitation on variable fonts: by the time we call
    ``pairwise_merge``, every input has been collapsed to static via
    ``instantiate_variable``, so ``Merger`` sees no ``VarStore`` tables.
    """
    from copy import deepcopy

    instantiated = [instantiate_variable(deepcopy(f), weight=weight) for f in subsetted_list]
    return pairwise_merge(instantiated)


def instantiate_variable(source, weight: int):
    """Instantiate a variable font at the given weight.

    Uses ``OverlapMode.KEEP_AND_SET_FLAGS`` — the documented enum member
    (per M8-AC6 / 2b #1; the legacy code passed ``overlap=0`` which is the
    raw enum value but bypasses the documented contract).

    No manual axis clamp is applied (per M8-AC7 / 2b #4); ``instancer``
    handles axis bounds correctly when the source has a single ``wght``
    axis. Multi-axis sources would need explicit per-axis limits, which
    we add in M8-F when integration uncovers the need.

    If the source is not a variable font (no ``fvar`` table), returns the
    font as-is — useful for static source fonts like ``NotoSansGothic-Regular``.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    from fontTools.varLib.instancer import OverlapMode

    if isinstance(source, (str, Path)):
        font = TTFont(str(source))
    else:
        font = source

    if "fvar" not in font:
        return font

    return instancer.instantiateVariableFont(
        font,
        axisLimits={"wght": float(weight)},
        overlap=OverlapMode.KEEP_AND_SET_FLAGS,
    )


# Tables fontTools.merge.Merger does not implement and that crash the
# default merger when present in a source font. MATH (math typesetting,
# present in NotoSansMath) lacks a `mergeMap` on its sub-table classes
# (`MathGlyphInfo`, `MathVariants`). BASE / JSTF / STAT / HVAR / VVAR /
# MVAR similarly lack defined merge logic and trigger either the same
# AttributeError or a `[NotImplemented, value]` equality assertion when
# one input has the table and the other doesn't (observed live for grc:
# NotoSansCypriot + NotoSansSymbols2 + NotoSans).
#
# Stripping these tables before merge is safe for engrish coverage —
# they govern advanced typography (math layout, baseline alignment,
# justification, style variations) that StarDict/EPUB body rendering
# does not consult; cmap and glyph outlines (which the merger DOES
# handle) are preserved.
_UNMERGEABLE_TABLES: tuple[str, ...] = (
    "MATH",
    "BASE",
    "JSTF",
    "STAT",
    "HVAR",
    "VVAR",
    "MVAR",
    # Vertical-metrics tables: NotoSansSymbols2 carries `vhea`/`vmtx` while
    # NotoSans + NotoSansCypriot do not — schema mismatch fails the Merger's
    # equality check (`[NotImplemented, value]`). engrish renders horizontally;
    # vertical metrics aren't consulted.
    "vhea",
    "vmtx",
    "VORG",
)


def _strip_unmergeable_tables(font) -> list[str]:
    """Drop tables fontTools.merge.Merger cannot handle. Returns dropped tags."""
    dropped: list[str] = []
    for tag in _UNMERGEABLE_TABLES:
        if tag in font:
            del font[tag]
            dropped.append(tag)
    return dropped


def _normalize_upem(font, target_upem: int) -> bool:
    """Scale a font to ``target_upem`` if its head.unitsPerEm differs. Returns True if scaled.

    fontTools.merge.Merger requires all inputs to share the same units-per-em;
    on mismatch it raises ``AssertionError: Expected all items to be equal:
    [2048, 1000]`` (or similar) and the pairwise fold drops the source. Almost
    every Noto outline font uses UPM 1000, but a handful (notably
    ``NotoEmoji[wght].ttf`` at UPM 2048) diverge — when one of those becomes
    the accumulator in the pairwise fold, every subsequent UPM-1000 source
    fails and is silently dropped, collapsing the merged cmap to a small
    subset of what the source-union promised.

    ``fontTools.ttLib.scaleUpem.scale_upem`` rescales glyf outlines + head /
    hhea / OS-2 metrics + GPOS positioning to the new UPM. Visually
    indistinguishable for body-text rendering.
    """
    if font["head"].unitsPerEm == target_upem:
        return False
    from fontTools.ttLib.scaleUpem import scale_upem
    scale_upem(font, target_upem)
    return True


def _convert_cff_to_tt(font):
    """Convert a CFF (.otf-style) font to TT (glyf/loca) in-place.

    M13 fix (2026-05-09): ``fontTools.merge.Merger`` cannot merge CFF outlines
    with TrueType outlines (raises ``AttributeError: 'NotImplementedType'
    object has no attribute 'cff'`` because CFF lacks a `mergeMap`). Pre-fix,
    ``pairwise_merge`` caught the exception and silently dropped the CFF
    source, causing CJK fonts (NotoSansJP/SC/TC/KR-Regular.otf) and any
    other .otf source to vanish from the merged output.

    Conversion uses ``cu2qu`` to approximate cubic Béziers (CFF) with quadratic
    Béziers (TT). Tolerance ``max_err=1.0`` is the fontTools-recommended
    default for body-text rendering — visually indistinguishable.

    No-op if the font already has a ``glyf`` table (was already TT) or if the
    font has neither ``CFF `` nor ``CFF2``.
    """
    from fontTools.ttLib import newTable
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.cu2quPen import Cu2QuPen

    if "glyf" in font:
        return
    if "CFF " not in font and "CFF2" not in font:
        return

    glyph_set = font.getGlyphSet()
    glyph_order = font.getGlyphOrder()

    glyf = newTable("glyf")
    glyf.glyphs = {}
    for gn in glyph_order:
        tt_pen = TTGlyphPen(glyph_set)
        cu2qu_pen = Cu2QuPen(tt_pen, max_err=1.0)
        glyph_set[gn].draw(cu2qu_pen)
        glyf.glyphs[gn] = tt_pen.glyph()

    # Drop CFF-specific tables; install glyf + loca + sfntVersion = TT.
    # Also strip layout tables (GPOS/GSUB/GDEF/BASE) — they reference the
    # original CFF glyph IDs which don't survive the TTGlyphPen rebuild,
    # and trying to merge them with a TT source's GPOS/GSUB raises
    # ``TypeError: '<' not supported between instances of 'int' and
    # 'NotImplementedType'`` from fontTools' merge logic. For dictionary
    # body-text rendering (StarDict / EPUB), GPOS kerning / GSUB ligatures /
    # GDEF mark categories / BASE baselines are not consulted — basic
    # cmap → glyf lookup is sufficient. Vertical-metrics tables likewise.
    for tag in ("CFF ", "CFF2", "VORG", "GPOS", "GSUB", "GDEF", "BASE",
                "vhea", "vmtx"):
        if tag in font:
            del font[tag]
    font.sfntVersion = "\x00\x01\x00\x00"  # OpenType TT magic
    font["glyf"] = glyf
    # loca is generated on the fly from glyf during compile; install an empty
    # loca to satisfy the table dependency graph.
    loca = newTable("loca")
    loca.set([])
    font["loca"] = loca
    # Update head.indexToLocFormat to "long" (1) — safer for fonts with > 32 KB
    # of glyph data; the compiler will downgrade if it fits in short format.
    if "head" in font:
        font["head"].indexToLocFormat = 1
    # Update maxp version to 1.0 (TrueType profile) so glyf-related fields
    # (numGlyphs, etc.) are interpreted correctly. CFF fonts' maxp is 0.5
    # which lacks many TT-required fields; populate them with safe defaults.
    if "maxp" in font:
        m = font["maxp"]
        m.tableVersion = 0x00010000
        m.numGlyphs = len(glyph_order)
        m.maxPoints = 0
        m.maxContours = 0
        m.maxCompositePoints = 0
        m.maxCompositeContours = 0
        m.maxZones = 2
        m.maxTwilightPoints = 0
        m.maxStorage = 0
        m.maxFunctionDefs = 0
        m.maxInstructionDefs = 0
        m.maxStackElements = 0
        m.maxSizeOfInstructions = 0
        m.maxComponentElements = 0
        m.maxComponentDepth = 0
    log.debug("converted CFF source to TT (%d glyphs)", len(glyph_order))


def pairwise_merge(fonts: list) -> "TTFont":  # noqa: F821
    """Combine multiple subsetted source fonts into one via repeated pairwise merger.

    **Why pairwise (M8-AC9):** ``fontTools.merge.Merger`` is more robust on
    pairs than on N>2 inputs at once — its cmap conflict resolution and table
    coalescing assume a binary fold. A flat merge over N fonts can hit edge
    cases (duplicate glyph IDs, conflicting GSUB tables) that the pairwise
    fold-left pattern avoids by collapsing one font at a time.

    The fold uses ``fontTools.merge.Merger().merge`` left-to-right; the first
    font in the list provides the base table set.

    **M9 hardening:** before each merge step, ``_UNMERGEABLE_TABLES`` are
    stripped from each input (MATH, BASE, JSTF, STAT, H/V/M-VAR — none have
    a working ``Merger`` implementation in fontTools; they crash with either
    ``AttributeError: ... has no attribute 'mergeMap'`` or
    ``AssertionError: Expected all items to be equal: [NotImplemented, ...]``).
    If a pairwise merge step still raises after stripping, the failing source
    is dropped from the fold (warning logged); its codepoints fall through to
    D30's Tier-3 warning channel via the upstream coverage check.

    Returns a single merged ``TTFont``. If the list contains zero fonts,
    raises ``ValueError``. If the list contains one font, returns it as-is.
    """
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont

    if not fonts:
        raise ValueError("pairwise_merge requires at least one font")
    if len(fonts) == 1:
        # Strip unmergeable tables for consistency with multi-source path so
        # downstream cmap/coverage checks see a uniform shape.
        _convert_cff_to_tt(fonts[0])
        _strip_unmergeable_tables(fonts[0])
        return fonts[0]

    # fontTools.merge.Merger.merge accepts a list of paths OR a list of
    # in-memory TTFont objects depending on version; for portability we
    # write each font to a tempfile, then pass paths.
    import tempfile

    # Pick the modal UPM among inputs as the merge target. Most Noto outline
    # fonts ship at UPM 1000; the rare outliers (e.g. NotoEmoji at 2048) get
    # scaled to match. Tie-breaking on count alone is fine — the first font in
    # the list is the accumulator and using its UPM avoids scaling it.
    upem_counts: dict[int, int] = {}
    for f in fonts:
        u = f["head"].unitsPerEm
        upem_counts[u] = upem_counts.get(u, 0) + 1
    target_upem = max(upem_counts, key=lambda u: (upem_counts[u], u == fonts[0]["head"].unitsPerEm))

    paths: list[str] = []
    tmps: list = []
    try:
        for f in fonts:
            # M13 fix (2026-05-09): CFF (.otf) sources must be converted to
            # TT outlines before merge; fontTools.merge.Merger silently fails
            # on TT+CFF mix. Pre-fix, the catch-and-warn block below dropped
            # CFF sources, which is how CJK .otf fonts vanished from the
            # zh/ja/en/12-locale outputs.
            _convert_cff_to_tt(f)
            dropped = _strip_unmergeable_tables(f)
            if dropped:
                log.debug("pairwise_merge: stripped unmergeable tables %s from source", dropped)
            if _normalize_upem(f, target_upem):
                log.debug("pairwise_merge: scaled source UPM to %d", target_upem)
            tmp = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
            tmp.close()
            f.save(tmp.name)
            paths.append(tmp.name)
            tmps.append(tmp)

        # Pairwise fold-left, with per-step exception handling: if a single
        # source can't be merged after stripping, skip it (its codepoints
        # surface in the D30 Tier-3 warning channel).
        accumulator = paths[0]
        for next_path in paths[1:]:
            try:
                merger = Merger()
                merged = merger.merge([accumulator, next_path])
                tmp_out = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
                tmp_out.close()
                merged.save(tmp_out.name)
                accumulator = tmp_out.name
                tmps.append(tmp_out)
            except Exception as exc:  # noqa: BLE001 — fontTools merge raises bare classes
                log.warning(
                    "pairwise_merge: dropping source %s; merge failed after table-strip: %s",
                    next_path,
                    exc,
                )
                # accumulator unchanged; this source is omitted from the
                # final merged font. Its codepoints fall through to D30's
                # Tier-3 warning channel via the coverage_check call site.

        return TTFont(accumulator)
    finally:
        import os

        for t in tmps:
            try:
                os.unlink(t.name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# M8-C: per-variant ``name`` table + ``hhea`` propagation
# ---------------------------------------------------------------------------

# Platform / encoding / language IDs for the three name-table records every
# OpenType reader expects. Subset chosen to match what Noto fonts actually
# ship — these are the records sdcv / KOReader / Crosspoint actually consult.
_NAME_RECORD_KEYS: tuple[tuple[int, int, int], ...] = (
    (3, 1, 0x0409),  # Windows / Unicode BMP / English (US)
    (1, 0, 0),       # Mac / Roman / English
    (0, 4, 0),       # Unicode / Unicode 2.0+ / (langID irrelevant)
)


def set_subfamily(ttfont, subfamily: str) -> None:
    """Write ``name`` table ID 2 (Font Subfamily) across all expected platform records.

    Per M8-AC4: each generated TTF (Regular / Bold / Italic / Bold Italic)
    MUST carry a distinct Subfamily label. Legacy code's all-"Regular"
    fallthrough — caused by setting only one platform record while leaving
    the other two stale — is rejected.

    ``subfamily`` is the human-readable label ("Regular" / "Bold" / "Italic"
    / "Bold Italic"). The function uses ``setName`` for each of the three
    canonical (platformID, platEncID, langID) tuples; older / unusual
    records left over from the source font remain untouched (they're
    typically ignored by readers that consult the canonical records).
    """
    name_table = ttfont["name"]
    for platform_id, plat_enc_id, lang_id in _NAME_RECORD_KEYS:
        name_table.setName(subfamily, 2, platform_id, plat_enc_id, lang_id)


def propagate_hhea(target, source_paths: list) -> None:
    """Aggregate ``hhea`` metrics across source fonts and write to ``target``.

    Aggregate rule (per M8-AC5):
    - ``ascent``    = ``max(source.hhea.ascent)``
    - ``descent``   = ``min(source.hhea.descent)`` (descents are negative)
    - ``lineGap``   = ``max(source.hhea.lineGap)``

    The merged font carries glyphs from multiple scripts; using the largest
    vertical envelope across sources ensures no script's glyphs clip. No
    hard-coded 800 / -200 — this matches what each source designer chose,
    which is what each script's glyphs were drawn against.

    ``source_paths`` is a list of paths to source TTF/OTF files (the inputs
    that fed the merge). ``target`` is the merged ``TTFont`` to be patched.
    """
    from fontTools.ttLib import TTFont

    if not source_paths:
        return

    ascents: list[int] = []
    descents: list[int] = []
    line_gaps: list[int] = []
    for sp in source_paths:
        src = TTFont(str(sp), lazy=True)
        try:
            hhea = src["hhea"]
            ascents.append(hhea.ascent)
            descents.append(hhea.descent)
            line_gaps.append(hhea.lineGap)
        finally:
            src.close()

    target_hhea = target["hhea"]
    target_hhea.ascent = max(ascents)
    target_hhea.descent = min(descents)
    target_hhea.lineGap = max(line_gaps)


# ---------------------------------------------------------------------------
# M8-E: source-font discovery + form orchestrator
# ---------------------------------------------------------------------------


def stem_to_path(stem: str, fonts_dir: Path) -> Path | None:
    """Resolve a Noto font stem (e.g. ``"NotoSans"``) to an actual font file.

    Preference order:
    1. Variable font ``<stem>[wght].ttf``.
    2. Static Regular ``<stem>-Regular.ttf``.
    3. Italic-only static ``<stem>-Italic.ttf``.
    4. Any-suffix fallback via ``font_io.find_font_file`` (handles ``.otf``
       CJK fonts like ``NotoSansJP-Regular.otf``, color-emoji variants like
       ``NotoColorEmoji-noflags.ttf``, etc.).

    Returns ``None`` if no match found.

    M13 fix (2026-05-09): pre-fix this function only checked the 3 hardcoded
    ``.ttf`` candidates and silently returned None for CJK ``.otf`` fonts +
    NotoColorEmoji-noflags.ttf, causing ``resolve_source_fonts`` to drop
    those stems and ja/zh/en form-fonts to ship without CJK / emoji glyph
    coverage. ``font_io.find_font_file`` (used by ``update-fonts``) already
    handled both extensions; the two stem-resolvers had diverged.
    """
    candidates = [
        fonts_dir / f"{stem}[wght].ttf",
        fonts_dir / f"{stem}-Regular.ttf",
        fonts_dir / f"{stem}-Italic.ttf",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fallback: scan the dir for any matching extension/suffix variant.
    from engrish.font_io import find_font_file
    return find_font_file(stem, fonts_dir)


# Color-bitmap font stems excluded from the subset+merge pipeline.
# NotoColorEmoji ships as CBDT/CBLC color bitmap tables; ``fontTools.merge.Merger``
# cannot combine bitmap fonts with TT-outline fonts (the merger picks the
# bitmap base and silently drops every outline source). Pre-M13 (D40), these
# stems were silently dropped by the resolver because their filename suffix
# didn't match the 3 hardcoded .ttf candidates. Post-D40, they resolve cleanly
# but break the merge. We exclude them explicitly here. Per engrish.md ("EPUBs
# never embed fonts; install Noto fonts on reader") emoji rendering on the
# user's device falls back to the reader's built-in color-emoji font.
_FONT_STEMS_EXCLUDED_FROM_MERGE: tuple[str, ...] = ("NotoColorEmoji",)


def resolve_source_fonts(locales: list[str], fonts_dir: Path) -> list[Path]:
    """Look up the per-locale ``fonts`` lists in ``engrish.json``; resolve each
    stem to a real TTF path. Returns deduped list in form-locale order.

    Excludes color-bitmap stems (``_FONT_STEMS_EXCLUDED_FROM_MERGE``) which
    ``fontTools.merge.Merger`` cannot combine with outline fonts.
    """
    from engrish.config import _ENGRISH_CFG

    seen: set[str] = set()
    out: list[Path] = []
    for code in locales:
        cfg = _ENGRISH_CFG.get(code, {})
        for stem in cfg.get("fonts", []):
            if stem in seen:
                continue
            seen.add(stem)
            if stem in _FONT_STEMS_EXCLUDED_FROM_MERGE:
                log.info(
                    "resolve_source_fonts: excluding %r (color-bitmap; "
                    "see _FONT_STEMS_EXCLUDED_FROM_MERGE)",
                    stem,
                )
                continue
            path = stem_to_path(stem, fonts_dir)
            if path is not None:
                out.append(path)
            else:
                log.warning("font stem %r for locale %r has no matching file in %s", stem, code, fonts_dir)
    return out


def build_form_fonts(
    form: str,
    locales: list[str],
    df_path: Path,
    out_dir: Path,
    fonts_dir: Path,
    log_path: Path | None = None,
) -> dict[str, Path]:
    """Top-level orchestrator: produce the four ``{Regular,Bold,Italic,BoldItalic}.ttf``
    plus the ``coverage_gaps.txt`` artifact.

    Returns a dict keyed by style label (``"Regular"`` / ``"Bold"`` / ``"Italic"`` /
    ``"Bold Italic"``) mapped to the output path.

    Pipeline:
    1. ``scan_codepoints`` (M8-A) to derive per-style codepoint sets + first-ref map.
    2. ``resolve_source_fonts`` to map locale font stems → real TTF paths.
    3. For each style-pair (R+B, I+BI):
       - ``subset_for_style_pair`` (M8-B) — single counter increment, returns
         pairwise-merged subset.
       - ``instantiate_variable`` (M8-B) at the pair's two weights (400 + 700).
       - For each weight variant: ``set_subfamily`` (M8-C), ``propagate_hhea``
         (M8-C), ``assert_under_format_limits`` (M8-D), ``save_font_deterministic``
         (M8-D).
    4. ``coverage_check`` (M8-A) over ``S − U`` → ``WarningChannel.emit``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    scanned = scan_codepoints(df_path)
    sources = resolve_source_fonts(locales, fonts_dir)
    if not sources:
        raise BuildError(
            f"no source fonts resolved for form {form!r} (locales {locales}); "
            f"check engrish.json `fonts` and the contents of {fonts_dir}"
        )

    source_union = union_source_coverage(sources)

    rb_codepoints = scanned.per_style[STYLE_REGULAR] | scanned.per_style[STYLE_BOLD]
    ib_codepoints = scanned.per_style[STYLE_ITALIC] | scanned.per_style[STYLE_BOLD_ITALIC]

    reset_subset_counter()

    # Subset returns per-source lists (no merge yet — fontTools.merge can't
    # handle variable fonts; merge happens downstream after instantiation).
    rb_subsetted = subset_for_style_pair(sources, rb_codepoints) if rb_codepoints else None
    ib_subsetted = (
        subset_for_style_pair(sources, ib_codepoints) if ib_codepoints else None
    )

    outputs: dict[str, Path] = {}

    def _emit_variant(subsetted_list, subfamily: str, weight: int) -> Path:
        font = instantiate_and_merge_static(subsetted_list, weight=weight)
        set_subfamily(font, subfamily)
        propagate_hhea(font, sources)
        assert_under_format_limits(font)
        path = out_dir / f"{subfamily.replace(' ', '')}.ttf"
        save_font_deterministic(font, path)
        return path

    if rb_subsetted is not None:
        outputs["Regular"] = _emit_variant(rb_subsetted, "Regular", 400)
        outputs["Bold"] = _emit_variant(rb_subsetted, "Bold", 700)
    if ib_subsetted is not None:
        outputs["Italic"] = _emit_variant(ib_subsetted, "Italic", 400)
        outputs["Bold Italic"] = _emit_variant(ib_subsetted, "Bold Italic", 700)

    # If italic codepoints were empty, fall back to copying Regular/Bold for
    # Italic/BoldItalic so the AC1 four-file contract is met. (M8-F may
    # refine to use real italic source variants when available.)
    if "Italic" not in outputs and "Regular" in outputs:
        from shutil import copyfile

        outputs["Italic"] = out_dir / "Italic.ttf"
        copyfile(outputs["Regular"], outputs["Italic"])
        outputs["Bold Italic"] = out_dir / "BoldItalic.ttf"
        copyfile(outputs["Bold"], outputs["Bold Italic"])

    # Coverage warnings: codepoints in S − U emit per-codepoint warnings.
    coverage_report = coverage_check(scanned, source_union, generated_cmap=None)
    channel = WarningChannel(log_path=log_path, coverage_gaps_path=out_dir / "coverage_gaps.txt")
    channel.emit(coverage_report.tier3_warnings, scanned)

    return outputs


# ---------------------------------------------------------------------------
# M8-D: overflow assertion + byte-deterministic save
# ---------------------------------------------------------------------------

# TrueType / OpenType ceilings that the StarDict pipeline must NOT cross.
# Crossing them silently corrupts lookups at runtime — fail fast at build.
_MAX_GLYPH_COUNT = 65535  # 16-bit GlyphID ceiling (per ISO/IEC 14496-22 §5.3.4)
# OpenType ``head.created`` / ``head.modified`` are seconds since 1904-01-01.
# fontTools auto-reinterprets values < ~2.08 G as Unix-epoch (it heuristically
# bumps them by the 1904→1970 offset). Pin both to the OpenType seconds for
# 1970-01-01T00:00:00 (= 2082844800) so fontTools accepts the value verbatim
# and write paths produce byte-identical output run-over-run.
_FIXED_HEAD_TIMESTAMP = 2082844800


def assert_under_format_limits(ttfont) -> None:
    """Pre-save guard against TT/OT structural ceilings (M8-AC3 / I16 / Δ12).

    Checks:
    - ``maxp.numGlyphs < 65535`` — the GlyphID is a 16-bit unsigned int.
      Exceeding silently truncates references and corrupts lookups.
    - Every cmap subtable's max codepoint fits in its declared encoding range.
      A subtable declared as 16-bit BMP-only carrying SMP codepoints would
      truncate at runtime.

    Raises ``BuildError`` with an actionable message on the FIRST violation
    found. The message names the corpus-shrinking levers the operator can
    pull (drop low-value locales / etymology / low-priority scripts).
    """
    num_glyphs = ttfont["maxp"].numGlyphs
    if num_glyphs >= _MAX_GLYPH_COUNT:
        raise BuildError(
            f"Glyph count {num_glyphs:,} ≥ TrueType 16-bit ceiling {_MAX_GLYPH_COUNT:,}. "
            f"The StarDict pipeline cannot proceed — lookups would silently corrupt at "
            f"runtime. Mitigations: drop low-value locales from the form; build "
            f"-noetym variants; split the form into multiple smaller dictionaries."
        )

    # cmap encoding range check — only flag obvious mismatches; subtables
    # that legitimately span SMP must declare encoding 4 (Unicode UCS-4).
    cmap = ttfont["cmap"]
    for subtable in cmap.tables:
        max_cp = max(subtable.cmap.keys()) if subtable.cmap else 0
        # Format 4 / encoding 1 = 16-bit BMP only.
        if subtable.format == 4 and max_cp > 0xFFFF:
            raise BuildError(
                f"cmap subtable (platform={subtable.platformID}, encoding={subtable.platEncID}, "
                f"format=4) carries codepoint U+{max_cp:04X} above BMP ceiling 0xFFFF. "
                f"The subtable encoding cannot represent SMP codepoints; lookups would "
                f"silently miss. Use cmap format 12 (encoding 4) for SMP-spanning fonts."
            )


def save_font_deterministic(ttfont, path) -> None:
    """Save ``ttfont`` to ``path`` with fixed ``head`` timestamps for byte-determinism.

    fontTools' default ``TTFont.save`` updates ``head.modified`` to the current
    wall-clock time via ``recalcTimestamp=True``, which breaks two-run
    byte-equality. We disable that update and pin both ``head.created`` and
    ``head.modified`` to ``_FIXED_HEAD_TIMESTAMP`` (= 1970-01-01 in OpenType
    seconds — high enough that fontTools' load-time Unix-epoch reinterpretation
    heuristic doesn't apply).

    Per M8-AC11 / Δ3 (font leg).
    """
    head = ttfont["head"]
    head.created = _FIXED_HEAD_TIMESTAMP
    head.modified = _FIXED_HEAD_TIMESTAMP
    ttfont.recalcTimestamp = False
    ttfont.save(str(path))
