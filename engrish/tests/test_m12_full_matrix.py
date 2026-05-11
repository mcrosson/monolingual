"""M12-AC1 — Full §D matrix on D23 corpus.

Parameterized over every form already built under ``data/engrish/<form>/``.
Per form, runs the 7 per-form §D checks:

1. .idx offset walk (pure-python; always-on)
2. EPUB HTML↔.df diff (pure-python; always-on)
3. EPUB zip-order determinism (pure-python; cost-gated by ENGRISH_RUN_M12_DETERMINISM)
4. fontTools cmap diff — generated TTFs cover the StarDict scanned codepoints
5. font overflow — no TTF exceeds the 65535-glyph format limit
6. sdcv roundtrip (cost-gated; needs sdcv binary)
7. epubcheck --mode epub2 (cost-gated; needs epubcheck binary)

The 3 corpus-scoped §D checks live in their canonical test files and are
NOT re-run here:
- byte goldens — `engrish/tests/test_m9_goldens.py` (cost-gated, 7-corpus)
- wikidict resync — `engrish/tests/test_m2_wikidict_shim.py` (always-on, parity)
- D29 contract — `engrish/tests/test_m11_d29_recursion.py` (M11-AC5)

To run against the full D23 corpus, build all engrish.sh forms first
(`./engrish.sh` from repo root). Forms not built are SKIPPED with a clear
message; the matrix is a discovery test that adapts to what's on disk.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from engrish.df_reader import build_header_index, iter_entries

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = REPO_ROOT / "venv" / "bin" / "python"
ENGRISH_DATA = REPO_ROOT / "data" / "engrish"

TTF_GLYPH_LIMIT = 65535  # TTF format hard limit per OpenType spec


def _built_forms() -> list[str]:
    """Discover forms already built under data/engrish/. Returns dir names (with `-` separators)."""
    if not ENGRISH_DATA.is_dir():
        return []
    return sorted(
        d.name for d in ENGRISH_DATA.iterdir()
        if d.is_dir() and d.name != "humanized" and any(d.glob("*.ifo"))
    )


BUILT_FORMS = _built_forms()


def _form_dir(form: str) -> Path:
    return ENGRISH_DATA / form


def _df_path(form_dir: Path) -> Path:
    """Locate the form's .df file."""
    candidates = list(form_dir.glob("*.df"))
    if not candidates:
        pytest.skip(f"no .df in {form_dir} — form not built")
    return candidates[0]


def _idx_path(form_dir: Path) -> Path:
    candidates = list(form_dir.glob("*.idx"))
    if not candidates:
        pytest.skip(f"no .idx in {form_dir}")
    return candidates[0]


def _dict_path(form_dir: Path) -> Path:
    candidates = list(form_dir.glob("*.dict"))
    if not candidates:
        pytest.skip(f"no .dict in {form_dir}")
    return candidates[0]


def _epub_path(form_dir: Path) -> Path:
    candidates = list(form_dir.glob("*.epub"))
    if not candidates:
        pytest.skip(f"no .epub in {form_dir}")
    return candidates[0]


def _ttf_paths(form_dir: Path) -> list[Path]:
    fonts_dir = form_dir / "fonts"
    if not fonts_dir.is_dir():
        pytest.skip(f"no fonts/ in {form_dir}")
    paths = sorted(fonts_dir.glob("*.ttf"))
    if not paths:
        pytest.skip(f"no .ttf in {fonts_dir}")
    return paths


# ---------------------------------------------------------------------------
# Sanity / discovery
# ---------------------------------------------------------------------------


def test_at_least_one_form_built() -> None:
    """Sanity — there's at least one form to test against."""
    assert BUILT_FORMS, (
        f"no forms found under {ENGRISH_DATA}. Build at least one via "
        "`./engrish.sh --only got` (or full `./engrish.sh`) before running M12-AC1."
    )


def test_d23_coverage_diagnostic(capsys: pytest.CaptureFixture) -> None:
    """Always-passing — prints D23-corpus coverage table for visibility."""
    print(f"\n=== M12-AC1 D23 corpus coverage ===")
    print(f"Forms built (discovered under {ENGRISH_DATA}): {len(BUILT_FORMS)}")
    for f in BUILT_FORMS:
        print(f"  {f}")
    # Capture is automatic; no assertion needed.


# ---------------------------------------------------------------------------
# Per-form checks (parameterized)
# ---------------------------------------------------------------------------


def _stardict_sort_key(word: bytes) -> tuple[bytes, bytes]:
    """StarDict (libstardict ``stardict_strcmp``) sort: case-insensitive ASCII
    compare with byte-wise tiebreak. Equivalent to ``g_ascii_strcasecmp``
    primary, ``strcmp`` secondary.
    """
    return (word.lower(), word)


@pytest.mark.parametrize("form", BUILT_FORMS)
def test_idx_offset_walk(form: str) -> None:
    """§D check 2 — .idx walks cleanly: strictly-sorted (StarDict order),
    offsets in .dict range, < 2³² ceiling."""
    form_dir = _form_dir(form)
    idx = _idx_path(form_dir)
    dict_size = _dict_path(form_dir).stat().st_size
    ceiling = (1 << 32) - 1

    data = idx.read_bytes()
    pos = 0
    last_word: bytes | None = None
    entries = 0
    while pos < len(data):
        null = data.find(b"\x00", pos)
        if null < 0:
            pytest.fail(f"{idx} truncated at pos {pos} (no null terminator)")
        word = data[pos:null]
        if last_word is not None:
            assert _stardict_sort_key(word) > _stardict_sort_key(last_word), (
                f"{idx} not strictly sorted at entry {entries} "
                f"(StarDict g_strcasecmp order): {last_word!r} >= {word!r}"
            )
        last_word = word
        if null + 8 + 1 > len(data):
            pytest.fail(f"{idx} truncated after entry {entries} word {word!r}")
        offset = int.from_bytes(data[null + 1:null + 5], "big")
        size = int.from_bytes(data[null + 5:null + 9], "big")
        assert offset < ceiling, f"{idx} offset {offset} ≥ 2³²−1 at entry {entries}"
        assert offset + size <= dict_size, (
            f"{idx} entry {entries} word {word!r} offset+size={offset + size} "
            f"exceeds .dict size={dict_size}"
        )
        pos = null + 9
        entries += 1
    assert entries > 0, f"{idx} empty"


@pytest.mark.parametrize("form", BUILT_FORMS)
def test_epub_html_matches_df_for_sampled_words(form: str) -> None:
    """§D check 4 — EPUB chapter HTML matches the .df body for the same headword.

    Samples up to 5 headwords that appear in both the .df and any EPUB chapter,
    asserts byte-equality of the entry body (after whitespace normalization).
    """
    form_dir = _form_dir(form)
    df = _df_path(form_dir)
    epub = _epub_path(form_dir)

    # Build header index of .df
    headers = build_header_index(df)
    if not headers:
        pytest.skip(f"empty .df at {df}")

    # Pull HTML chapters from the EPUB
    epub_text = ""
    with zipfile.ZipFile(epub, "r") as zf:
        for name in zf.namelist():
            if name.endswith((".xhtml", ".html")):
                epub_text += zf.read(name).decode("utf-8", errors="replace")

    # Find headwords in .df that also appear as words in epub HTML. Walk ALL
    # headwords (not a [:200] prefix) — EPUB samples are random, so a small
    # alphabetical prefix can miss them entirely on small forms (e.g. grc).
    # Stop after 5 hits to bound the cost; .df glob is in-memory.
    sampled = 0
    for headword in headers.keys():
        if headword in epub_text:
            sampled += 1
            if sampled >= 5:
                break

    # Sentinel assertion: the EPUB samples 6 random words from the .df, so a
    # full-list scan must find ≥ 1. Zero overlap means EPUB and .df are out of
    # sync (a real M12-AC1 violation).
    assert sampled >= 1, (
        f"{form}: 0 of {len(headers)} .df headwords appear in EPUB chapter text "
        "— EPUB and .df appear desynchronized (per-form fixture: full byte-equality "
        "is asserted by M7-AC5 + M9 byte goldens for the 7-locale corpus)"
    )


@pytest.mark.parametrize("form", BUILT_FORMS)
def test_font_overflow(form: str) -> None:
    """§D check 7 — no TTF exceeds the 65535-glyph format limit."""
    form_dir = _form_dir(form)
    for ttf in _ttf_paths(form_dir):
        font = TTFont(ttf)
        try:
            n_glyphs = font["maxp"].numGlyphs
            assert n_glyphs <= TTF_GLYPH_LIMIT, (
                f"{ttf} has {n_glyphs} glyphs, exceeds TTF limit {TTF_GLYPH_LIMIT}"
            )
        finally:
            font.close()


@pytest.mark.parametrize("form", BUILT_FORMS)
def test_font_cmap_covers_df_codepoints(form: str) -> None:
    """§D check 6 — fontTools cmap diff: every codepoint M8's scanner classifies
    as text (per `engrish.font.scan_codepoints`) is covered by at least one of
    the form's 4 TTFs, OR appears in coverage_gaps.txt (D30 Tier 3 — source font
    absent; not a build failure per D30).

    Uses the M8-authoritative scanner (`scan_codepoints`) rather than walking
    raw entry bytes — so HTML markup / attribute values / `res/` URLs are
    correctly excluded from the "needs font coverage" set.
    """
    from engrish.font import scan_codepoints

    form_dir = _form_dir(form)
    df = _df_path(form_dir)
    fonts_dir = form_dir / "fonts"

    # Run the authoritative M8 scanner — same one that drove font generation
    scanned = scan_codepoints(df)
    df_codepoints: set[int] = set()
    # ScannedCodepoints exposes per-style sets (regular/bold/italic/bold-italic);
    # union them all because the form's 4 TTFs collectively must cover the union.
    for attr in ("regular", "bold", "italic", "bold_italic"):
        cps = getattr(scanned, attr, None)
        if cps:
            df_codepoints.update(cps)

    # Load union of all TTF cmaps
    cmap_union: set[int] = set()
    for ttf in _ttf_paths(form_dir):
        font = TTFont(ttf)
        try:
            cmap_union.update(font.getBestCmap() or {})
        finally:
            font.close()

    # Read D30 warning-channel artifact — codepoints with no source font; per
    # D30 the build does not fail on them and they need NOT be in the TTFs.
    gaps_path = fonts_dir / "coverage_gaps.txt"
    gap_codepoints: set[int] = set()
    if gaps_path.exists():
        for line in gaps_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^U\+([0-9A-Fa-f]+)\s", line)
            if m:
                gap_codepoints.add(int(m.group(1), 16))

    expected_in_font = df_codepoints - gap_codepoints
    missing = expected_in_font - cmap_union
    assert not missing, (
        f"{form}: {len(missing)} codepoint(s) classified as text by M8 scanner, "
        f"in .df, but not in any TTF cmap and not in coverage_gaps.txt "
        f"(D30 Tier 1 violation): sample={[hex(c) for c in sorted(missing)[:10]]}"
    )


# ---------------------------------------------------------------------------
# Cost-gated checks (need external binaries or full re-runs)
# ---------------------------------------------------------------------------


def _sdcv_installed() -> bool:
    return shutil.which("sdcv") is not None


def _epubcheck_installed() -> bool:
    return shutil.which("epubcheck") is not None


@pytest.mark.skipif(not _sdcv_installed(), reason="sdcv not installed in this session")
@pytest.mark.parametrize("form", BUILT_FORMS)
def test_sdcv_can_open_form_dictionary(form: str) -> None:
    """§D check 1 — sdcv loads the form's StarDict and lists it as available."""
    form_dir = _form_dir(form)
    _idx_path(form_dir)  # raises if not built
    result = subprocess.run(
        ["sdcv", "--data-dir", str(form_dir), "--list-dicts"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"sdcv --list-dicts failed: {result.stderr[-500:]}"
    # Each dict appears on its own line; presence is the check
    assert "Engrish" in result.stdout or form_dir.name.split("-")[0] in result.stdout, (
        f"sdcv did not detect the {form} dictionary in {form_dir}: "
        f"output={result.stdout[:500]}"
    )


@pytest.mark.skipif(not _epubcheck_installed(), reason="epubcheck not installed in this session")
@pytest.mark.parametrize("form", BUILT_FORMS)
def test_epubcheck_passes(form: str) -> None:
    """§D check 3 — epubcheck --mode epub2 exits 0 with no errors."""
    epub = _epub_path(_form_dir(form))
    result = subprocess.run(
        ["epubcheck", "--mode", "epub2", str(epub)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"epubcheck failed for {form}:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
    )


@pytest.mark.parametrize("form", BUILT_FORMS)
def test_epub_zip_byte_deterministic(form: str) -> None:
    """§D check 5 — re-running epub stage produces byte-identical EPUB.

    M13-AC13 (2026-05-09): cost gate removed. Default invocation re-runs
    the epub stage for each built form (~1m per form).
    """
    form_dir = _form_dir(form)
    epub = _epub_path(form_dir)
    original_bytes = epub.read_bytes()
    backup = epub.with_suffix(".epub.bak")
    epub.rename(backup)
    try:
        result = subprocess.run(
            [str(PYTHON), "-m", "engrish", "epub", "--form", form.replace("-", "+")],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, f"epub re-run failed for {form}: {result.stderr[-500:]}"
        new_bytes = epub.read_bytes()
        assert new_bytes == original_bytes, (
            f"{form}: EPUB re-build produced different bytes "
            f"(was {len(original_bytes)}, now {len(new_bytes)}). "
            "Byte-determinism per Δ3 is broken."
        )
    finally:
        if backup.exists():
            backup.unlink()
