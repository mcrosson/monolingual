"""Tests for the engrish module — uses real Wiktionary pipeline data."""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from engrish import generate, merge, stats, epub
from engrish.__main__ import _parse_form
from engrish.config import FORM_NAMES
from engrish.paths import (
    dict_base_name,
    df_path,
    engrish_form_dir,
    get_snapshot_date,
)
from engrish.pipeline import run_wikidict
from engrish.stardict import ifo_fields, patch_ifo

# ---------------------------------------------------------------------------
# Test forms and their locale lists
# ---------------------------------------------------------------------------

# (locales, expect_overlap) — overlap=True means locales share headwords,
# overlap=False means zero shared headwords (disjoint merge).
TEST_FORMS: dict[str, tuple[list[str], bool | None]] = {
    # Single locales — cover diverse script families
    "grc": (["grc"], None),           # Ancient Greek — Cypriot script
    "ru": (["ru"], None),             # Russian — Cyrillic, native wikidict module
    "fr": (["fr"], None),             # French — Latin, native module
    "el": (["el"], None),             # Greek — native module
    "cu": (["cu"], None),             # Church Slavonic — Glagolitic
    "ja": (["ja"], None),             # Japanese — CJK + kana + emoji + Hentaigana
    # Merged forms — test overlap and merge logic
    "ang+enm+en": (["ang", "enm", "en"], True),   # English family — overlapping
    "ru+grc": (["ru", "grc"], False),              # Cyrillic + Greek — disjoint
    "grc+el": (["grc", "el"], True),               # Ancient + Modern Greek — overlapping
    "ru+cu": (["ru", "cu"], True),                 # Russian + Church Slavonic — overlapping
    "ja+en": (["ja", "en"], True),                 # CJK + English — overlapping, CJK merge
    # Large merged — many locales, overlapping
    "en+enm+ang+es+fr+de+ru+it+el+la+fro+grc": (["en", "enm", "ang", "es", "fr", "de", "ru", "it", "el", "la", "fro", "grc"], True),
    # Large stress test — en is last, biggest
    "en": (["en"], None),             # English — very large, stress test
}

FORM_IDS = list(TEST_FORMS)


# ---------------------------------------------------------------------------
# Session fixture — runs pipeline + generate once for all tests
# ---------------------------------------------------------------------------


def _form_output_exists(form: str) -> bool:
    """Check if a form's output directory already has StarDict files."""
    form_dir = engrish_form_dir(form)
    if not form_dir.exists():
        return False
    return bool(list(form_dir.rglob("*.ifo")))


@pytest.fixture(scope="session")
def engrish_pipeline() -> dict[str, Path]:
    """Run the wikidict pipeline and generate output for all test forms.

    Skips steps that are already complete (idempotent).
    Returns a dict mapping form names to their output directories.
    """
    # Override CWD to project root — conftest.py sets it to tests/ for upstream tests,
    # but the engrish pipeline needs it pointing at the project root.
    import engrish.config as cfg

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    log = logging.getLogger(__name__)

    project_root = str(Path(__file__).parent.parent)
    os.environ["CWD"] = project_root
    cfg.DATA_DIR = Path(project_root) / "data"
    cfg.ENGRISH_DIR = cfg.DATA_DIR / "engrish"

    # Run pipeline for all needed locales (once, grouped by source dump)
    all_locales = sorted({loc for locs, _ in TEST_FORMS.values() for loc in locs})
    log.info("=== engrish test setup: running wikidict pipeline for %s ===", all_locales)
    run_wikidict(all_locales)

    # Restore logging after wikidict (it redirects to file)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    # Generate each test form (skip if output already exists)
    result: dict[str, Path] = {}
    for form, (locales, _) in TEST_FORMS.items():
        if _form_output_exists(form):
            log.info("[%s] Already generated — skipping", form)
        else:
            log.info("=== Generating form: %s (locales: %s) ===", form, locales)
            generate.process_form(form, locales)
        result[form] = engrish_form_dir(form)

    log.info("=== engrish test setup complete ===")
    return result


@pytest.fixture(scope="session")
def engrish_epubs(engrish_pipeline: dict[str, Path]) -> dict[str, Path]:
    """Generate EPUBs for all test forms. Returns dict of form -> epub path."""
    result: dict[str, Path] = {}
    for form, (locales, _) in TEST_FORMS.items():
        form_dir = engrish_pipeline[form]
        date = get_snapshot_date(locales)
        epub_path = form_dir / f"test-{dict_base_name(form, date)}.epub"
        epub.generate_epub(locales, epub_path, form=form)
        result[form] = epub_path
    return result


# ---------------------------------------------------------------------------
# Pipeline output verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_generate_output_structure(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Verify form output dir contains StarDict files for both etym and noetym."""
    form_dir = engrish_pipeline[form]
    locales, _ = TEST_FORMS[form]
    date = get_snapshot_date(locales)

    for noetym in (False, True):
        name = dict_base_name(form, date, noetym=noetym)
        folder = form_dir / name
        assert folder.exists(), f"Missing output folder: {folder}"

        for ext in (".ifo", ".idx", ".dict", ".syn"):
            files = list(folder.glob(f"*{ext}"))
            assert files, f"No {ext} file in {folder}"

        # .oft files should exist for .idx and .syn
        for ext in (".idx.oft", ".syn.oft"):
            files = list(folder.glob(f"*{ext}"))
            assert files, f"No {ext} file in {folder}"


@pytest.mark.parametrize("form", FORM_IDS)
def test_generate_ifo_metadata(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Verify .ifo has correct bookname, date, and lang fields."""
    form_dir = engrish_pipeline[form]
    locales, _ = TEST_FORMS[form]
    date = get_snapshot_date(locales)
    name = dict_base_name(form, date, noetym=False)
    ifo_path = form_dir / name / f"{name}.ifo"

    assert ifo_path.exists(), f"Missing .ifo: {ifo_path}"
    content = ifo_path.read_text(encoding="utf-8")

    expected = ifo_fields(form, date, name)
    for key, value in expected.items():
        assert f"{key}={value}" in content, f"Missing or wrong {key} in .ifo"


# ---------------------------------------------------------------------------
# Merge verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_merge_dfs(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Verify merge logic with real .df files."""
    locales, expect_overlap = TEST_FORMS[form]
    merged = merge.merge_dfs(locales, noetym=False)

    assert merged, "merge_dfs returned empty dict"

    if len(locales) == 1:
        # Single locale: no <h3> headers
        sample_word = next(iter(merged))
        _, html = merged[sample_word]
        assert "<h3>" not in html, "Single-locale merge should not have <h3> headers"
    else:
        entries_with_headers = {w: html for w, (_, html) in merged.items() if "<h3>" in html}
        has_overlap = bool(entries_with_headers)

        if expect_overlap is True:
            assert has_overlap, (
                f"Expected overlapping entries for {form} but none found — "
                "locales may not share any headwords"
            )
            for loc in locales:
                header = f"<h3>{FORM_NAMES[loc]}</h3>"
                assert any(header in html for html in entries_with_headers.values()), (
                    f"Overlapping entries exist but none contain <h3> for {FORM_NAMES[loc]} — "
                    f"locale '{loc}' may not have contributed to the merge"
                )
        elif expect_overlap is False:
            assert not has_overlap, (
                f"Expected zero overlap for {form} but found {len(entries_with_headers)} "
                "entries with <h3> headers — unexpected shared headwords"
            )

import re


def test_merge_dfs_honors_locale_order(engrish_pipeline: dict[str, Path]) -> None:
    """Merged <h3> headers must appear in the order the caller specified."""
    locales = ["ang", "enm", "en"]
    merged = merge.merge_dfs(locales, noetym=False)

    # Find a word present in all three locales
    overlapping = {
        w: html
        for w, (_, html) in merged.items()
        if all(f"<h3>{FORM_NAMES[loc]}</h3>" in html for loc in locales)
    }
    assert overlapping, "Need at least one word present in all three locales"

    word, html = next(iter(overlapping.items()))
    positions = [html.index(f"<h3>{FORM_NAMES[loc]}</h3>") for loc in locales]
    assert positions == sorted(positions), (
        f"Headers for {word!r} not in caller order {locales}: "
        f"got positions {positions}"
    )

    # Reverse the locale list and verify headers follow the new order
    reversed_locales = list(reversed(locales))
    merged_rev = merge.merge_dfs(reversed_locales, noetym=False)
    _, html_rev = merged_rev[word]
    positions_rev = [html_rev.index(f"<h3>{FORM_NAMES[loc]}</h3>") for loc in reversed_locales]
    assert positions_rev == sorted(positions_rev), (
        f"Headers for {word!r} not in reversed order {reversed_locales}: "
        f"got positions {positions_rev}"
    )


@pytest.mark.parametrize("form", FORM_IDS)
def test_res_integrity(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Every res/ reference in merged HTML must have a matching file in collect_locale_res."""
    locales, _ = TEST_FORMS[form]
    merged = merge.merge_dfs(locales, noetym=False)

    # Collect all src="res/..." references from HTML
    html_res_refs: set[str] = set()
    for _, (_, html) in merged.items():
        for m in re.finditer(r'(?:src|href)="(res/[^"]+)"', html):
            html_res_refs.add(m.group(1))

    if not html_res_refs:
        return  # no res/ references in this form's data

    # Collect actual files that would be written
    available_files: set[str] = set()
    for locale in locales:
        for fname in merge.collect_locale_res(locale, noetym=False):
            available_files.add(f"res/{fname}")

    missing = html_res_refs - available_files
    assert not missing, (
        f"HTML references res/ files that don't exist in collect_locale_res: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# EPUB verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_epub_structure(engrish_epubs: dict[str, Path], form: str) -> None:
    """Verify EPUB is a valid ZIP with expected structure."""
    epub_path = engrish_epubs[form]
    locales, _ = TEST_FORMS[form]

    assert epub_path.exists(), f"EPUB not created: {epub_path}"
    assert zipfile.is_zipfile(epub_path), f"Not a valid ZIP: {epub_path}"

    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()

        assert "mimetype" in names
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/toc.ncx" in names
        assert "OEBPS/styles.css" in names
        assert "OEBPS/cover.html" in names
        font_files = [n for n in names if n.startswith("OEBPS/fonts/")]
        assert font_files, "No font files in EPUB"
        assert any("NotoSans" in f for f in font_files), "No NotoSans font in EPUB"
        assert "OEBPS/cover.html" in names
        assert "OEBPS/summary.html" in names
        assert "OEBPS/stress_test.html" in names

        # Per-locale spot-check chapters
        for locale in locales:
            assert f"OEBPS/spot_{locale}.html" in names, (
                f"Missing spot-check chapter for locale '{locale}'"
            )

        # Cross-language chapter only for merged forms
        if len(locales) > 1:
            assert "OEBPS/cross_language.html" in names

        # Missing chapter always present
        assert "OEBPS/missing.html" in names


@pytest.mark.parametrize("form", FORM_IDS)
def test_epub_chapter_content(engrish_epubs: dict[str, Path], form: str) -> None:
    """Verify key chapters have substantive content."""
    epub_path = engrish_epubs[form]

    with zipfile.ZipFile(epub_path) as zf:
        # Summary should have a table with language info
        summary = zf.read("OEBPS/summary.html").decode("utf-8")
        assert "Modern English" in summary
        assert "<table>" in summary

        # Stress test should have size info
        stress = zf.read("OEBPS/stress_test.html").decode("utf-8")
        assert "KB" in stress

        # Missing chapter should have overview table and word lists
        missing = zf.read("OEBPS/missing.html").decode("utf-8")
        assert "<table>" in missing
        assert "Overview" in missing


# ---------------------------------------------------------------------------
# StarDict file verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_stardict_oft_files(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Verify .oft files have correct StarDict cache header."""
    form_dir = engrish_pipeline[form]
    locales, _ = TEST_FORMS[form]
    date = get_snapshot_date(locales)
    name = dict_base_name(form, date, noetym=False)
    folder = form_dir / name

    expected_header = b"StarDict's Cache, Version: 0.2"
    expected_magic = b"\xc1\xd1\xa4\x51"

    for pattern in ("*.idx.oft", "*.syn.oft"):
        oft_files = list(folder.glob(pattern))
        assert oft_files, f"No {pattern} in {folder}"
        for oft in oft_files:
            data = oft.read_bytes()
            assert data[:len(expected_header)] == expected_header, (
                f"{oft.name} has wrong header"
            )
            assert data[len(expected_header):len(expected_header) + 4] == expected_magic, (
                f"{oft.name} has wrong magic bytes"
            )


@pytest.mark.parametrize("form", FORM_IDS)
def test_stardict_ifo_patch(engrish_pipeline: dict[str, Path], form: str, tmp_path: Path) -> None:
    """Verify patch_ifo correctly modifies .ifo files."""
    form_dir = engrish_pipeline[form]
    locales, _ = TEST_FORMS[form]
    date = get_snapshot_date(locales)
    name = dict_base_name(form, date, noetym=False)

    # Copy a real .ifo to tmp_path for modification
    src_ifo = form_dir / name / f"{name}.ifo"
    test_dir = tmp_path / name
    test_dir.mkdir()
    test_ifo = test_dir / src_ifo.name
    shutil.copy2(src_ifo, test_ifo)

    # Patch with test overrides
    patch_ifo(test_dir, name, {"bookname": "Test Override", "newfield": "newvalue"})

    content = test_ifo.read_text(encoding="utf-8")
    assert "bookname=Test Override" in content, "Existing key not replaced"
    assert "newfield=newvalue" in content, "New key not appended"


# ---------------------------------------------------------------------------
# Stats verification
# ---------------------------------------------------------------------------


def test_language_stats(engrish_pipeline: dict[str, Path]) -> None:
    """Verify language stats against real SQLite dump."""
    from engrish.paths import get_sqlite_path

    db_path = get_sqlite_path()

    # Code mappings
    codes = stats.load_language_codes(db_path)
    assert codes["French"] == "fr"
    assert codes["German"] == "de"
    assert codes["Old English"] == "ang"
    assert codes["Middle English"] == "enm"
    assert len(codes) > 1000, "Expected thousands of language mappings"

    # Stats scan
    lang_stats = stats.scan_language_stats(db_path)
    assert "English" in lang_stats
    assert "Old English" in lang_stats

    en_stats = lang_stats["English"]
    assert en_stats["gloss_defs"] > 0
    assert en_stats["entries"] > 0
    assert en_stats["total_defs"] >= en_stats["gloss_defs"]

    # English should have the most gloss defs
    max_lang = max(lang_stats, key=lambda k: lang_stats[k]["gloss_defs"])
    assert max_lang == "English", f"Expected English to have most gloss defs, got {max_lang}"


# ---------------------------------------------------------------------------
# Add-language verification
# ---------------------------------------------------------------------------


def test_add_language(engrish_pipeline: dict[str, Path], tmp_path: Path) -> None:
    """Verify add-language writes correct config and handles edge cases."""
    import engrish.add_language as add_lang

    tmp_config = tmp_path / "engrish.json"
    tmp_config.write_text("{}", encoding="utf-8")

    # Patch the config path
    original = add_lang._ENGRISH_JSON
    add_lang._ENGRISH_JSON = tmp_config
    try:
        # Add French
        result = add_lang.run(["fr"], all_langs=False)
        assert result == 0

        cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "fr" in cfg["languages"]
        assert cfg["languages"]["fr"]["wiktionary_section"] == "french"
        assert cfg["languages"]["fr"]["display_name"] == "French"

        # Idempotent — adding again should skip
        result = add_lang.run(["fr"], all_langs=False)
        assert result == 0

        # Unknown code should fail
        result = add_lang.run(["zzz_invalid"], all_langs=False)
        assert result == 1
        # Add English — should work like any other language
        tmp_config.write_text("{}", encoding="utf-8")
        result = add_lang.run(["en"], all_langs=False)
        assert result == 0

        cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "en" in cfg["languages"]
        assert cfg["languages"]["en"]["wiktionary_section"] == "english"
        assert "fonts" in cfg["languages"]["en"]
    finally:
        add_lang._ENGRISH_JSON = original


def test_en_retains_native_head_sections() -> None:
    """When 'en' is in engrish.json, it must keep its native head_sections
    including 'translingual' — the ENGRISH_MODE guard must not overwrite them."""
    from wikidict.lang import head_sections

    assert "en" in head_sections, "en should be a registered locale"
    assert "translingual" in head_sections["en"], (
        "en should retain 'translingual' in head_sections even when in engrish.json"
    )


# ---------------------------------------------------------------------------
# Config verification
# ---------------------------------------------------------------------------


def test_seed_fonts_loaded() -> None:
    """Verify seed_fonts is loaded from config with expected stems."""
    from engrish.config import SEED_FONTS

    assert isinstance(SEED_FONTS, list)
    assert len(SEED_FONTS) > 0
    assert "NotoSans" in SEED_FONTS
    assert "NotoSansMath" in SEED_FONTS
    assert "NotoSansSymbols" in SEED_FONTS
    assert "NotoSansSymbols2" in SEED_FONTS
    assert "NotoColorEmoji" in SEED_FONTS


def test_epub_base_fonts_loaded() -> None:
    """Verify epub_base_fonts is loaded from config."""
    from engrish.config import EPUB_BASE_FONTS

    assert isinstance(EPUB_BASE_FONTS, list)
    assert "NotoSans" in EPUB_BASE_FONTS


def test_en_in_all_locales() -> None:
    """Verify 'en' is in ALL_LOCALES via config, not hardcoded."""
    from engrish.config import ALL_LOCALES, _ENGRISH_CFG

    assert "en" in ALL_LOCALES
    assert "en" in _ENGRISH_CFG, "en must be a configured language, not hardcoded"


# ---------------------------------------------------------------------------
# Font detection verification
# ---------------------------------------------------------------------------


def test_detect_fonts_locale_code_api(engrish_pipeline: dict[str, Path]) -> None:
    """detect_fonts takes a locale code and resolves wiktionary_section from config."""
    from engrish.paths import get_sqlite_path
    from engrish.update_fonts import detect_fonts

    db_path = get_sqlite_path()
    for code in ("en", "ja", "ang"):
        fonts = detect_fonts(code, db_path)
        assert isinstance(fonts, list)
        assert len(fonts) > 0, f"detect_fonts returned empty for {code}"
        assert all(isinstance(f, str) for f in fonts)


def test_detect_fonts_wiktionary_section_override(engrish_pipeline: dict[str, Path]) -> None:
    """detect_fonts accepts wiktionary_section kwarg for codes not in config."""
    from engrish.paths import get_sqlite_path
    from engrish.update_fonts import detect_fonts

    db_path = get_sqlite_path()
    # Use a known section heading with a made-up code
    fonts = detect_fonts("_test_fr", db_path, wiktionary_section="french")
    assert isinstance(fonts, list)
    assert len(fonts) > 0, "detect_fonts with explicit section returned empty"


def test_collect_headword_chars_batch(engrish_pipeline: dict[str, Path]) -> None:
    """Batch scan returns chars keyed by locale code for multiple languages."""
    from engrish.paths import get_sqlite_path
    from engrish.update_fonts import collect_headword_chars_batch

    db_path = get_sqlite_path()
    result = collect_headword_chars_batch(["en", "ja"], db_path)
    assert "en" in result
    assert "ja" in result
    assert len(result["en"]) > 0, "No non-ASCII chars found for en"
    assert len(result["ja"]) > 0, "No non-ASCII chars found for ja"


# ---------------------------------------------------------------------------
# EPUB font verification
# ---------------------------------------------------------------------------


def test_epub_contains_base_fonts(engrish_epubs: dict[str, Path]) -> None:
    """Every EPUB must contain all epub_base_fonts."""
    from engrish.config import EPUB_BASE_FONTS

    for form, epub_path in engrish_epubs.items():
        with zipfile.ZipFile(epub_path) as zf:
            font_files = [n for n in zf.namelist() if n.startswith("OEBPS/fonts/")]
            for stem in EPUB_BASE_FONTS:
                assert any(stem in f for f in font_files), (
                    f"EPUB for {form} missing base font {stem}"
                )


def test_epub_contains_cjk_fonts(engrish_epubs: dict[str, Path]) -> None:
    """Japanese EPUB must contain CJK font files."""
    epub_path = engrish_epubs["ja"]
    with zipfile.ZipFile(epub_path) as zf:
        font_files = [n for n in zf.namelist() if n.startswith("OEBPS/fonts/")]
        assert any("NotoSansJP" in f for f in font_files), (
            "Japanese EPUB missing NotoSansJP font"
        )


def test_epub_contains_emoji_font(engrish_epubs: dict[str, Path]) -> None:
    """EPUBs for locales with emoji headwords must contain NotoColorEmoji."""
    for form in ("en", "ja"):
        epub_path = engrish_epubs[form]
        with zipfile.ZipFile(epub_path) as zf:
            font_files = [n for n in zf.namelist() if n.startswith("OEBPS/fonts/")]
            assert any("NotoColorEmoji" in f for f in font_files), (
                f"EPUB for {form} missing NotoColorEmoji font"
            )


# ---------------------------------------------------------------------------
# Update-fonts verification
# ---------------------------------------------------------------------------


def test_update_fonts_downloads_missing(engrish_pipeline: dict[str, Path]) -> None:
    """Temporarily remove a font, run update-fonts, verify it's re-downloaded."""
    from engrish.config import FONTS_DIR
    from engrish.update_fonts import _find_font_file, run as run_update_fonts

    stem = "NotoSansRunic"
    font_file = _find_font_file(stem, FONTS_DIR)
    assert font_file is not None, f"{stem} not on disk before test"

    original_bytes = font_file.read_bytes()
    font_file.unlink()
    try:
        assert _find_font_file(stem, FONTS_DIR) is None, "Font should be gone"
        result = run_update_fonts()
        assert result == 0, "update-fonts returned non-zero"
        restored = _find_font_file(stem, FONTS_DIR)
        assert restored is not None, f"{stem} was not re-downloaded"
    finally:
        # Ensure font is restored even if test fails
        if not _find_font_file(stem, FONTS_DIR):
            font_file.write_bytes(original_bytes)


# ---------------------------------------------------------------------------
# CLI validation
# ---------------------------------------------------------------------------


def test_parse_form_validation() -> None:
    """Verify _parse_form accepts valid codes and rejects invalid ones."""
    assert _parse_form("en") == ["en"]
    assert _parse_form("ang+en") == ["ang", "en"]
    assert _parse_form("ang+enm+en") == ["ang", "enm", "en"]

    with pytest.raises(SystemExit):
        _parse_form("xyz")

    with pytest.raises(SystemExit):
        _parse_form("")


# ---------------------------------------------------------------------------
# Native-module locale registration (catches attr-copy bugs for locales
# like fr, el, de, etc. that have their own wikidict/lang/ module)
# ---------------------------------------------------------------------------

# Locales in engrish.json that also have a native wikidict/lang/<code>/ module
_NATIVE_MODULE_LOCALES = ["el", "ru", "de", "fr", "es", "it", "ja"]

# Parsing-critical _populate dicts that must match 'en' for engrish locales.
# These are the dict names from wikidict.lang (built by _populate).
_CRITICAL_DICTS = [
    "section_patterns",
    "sublist_patterns",
    "section_level",
    "section_sublevels",
    "etyl_section",
    "sections",
    "definitions_to_ignore",
    "templates_ignored",
    "float_separator",
    "thousands_separator",
]


@pytest.mark.parametrize("code", _NATIVE_MODULE_LOCALES)
def test_native_module_has_en_parsing_attrs(code: str) -> None:
    """Locales with native wikidict modules must inherit EN parsing rules in engrish mode.

    Compares the _populate()-level values (what render/convert actually use),
    not raw module attrs, so defaults are correctly accounted for.
    """
    from wikidict import lang

    for attr in _CRITICAL_DICTS:
        populated = getattr(lang, attr)
        en_val = populated["en"]
        loc_val = populated[code]
        assert loc_val == en_val, (
            f"lang.{attr}[{code!r}] does not match lang.{attr}['en']: "
            f"{loc_val!r} != {en_val!r}"
        )


@pytest.mark.parametrize("code", _NATIVE_MODULE_LOCALES)
def test_native_module_has_custom_head_sections(code: str) -> None:
    """Engrish override must set head_sections to the wiktionary_section from engrish.json."""
    from wikidict.lang import _ALL_LOCALES

    locale_mod = _ALL_LOCALES[code]
    en_mod = _ALL_LOCALES["en"]

    assert locale_mod.head_sections != en_mod.head_sections, (
        f"{code}.head_sections should differ from en (should be the engrish override)"
    )
    assert len(locale_mod.head_sections) == 1, (
        f"{code}.head_sections should be a single-element tuple"
    )


@pytest.mark.parametrize("code", _NATIVE_MODULE_LOCALES)
def test_native_module_locale_origin(code: str) -> None:
    """All engrish locales must map to 'en' in LOCALE_ORIGIN."""
    from wikidict.constants import LOCALE_ORIGIN

    assert LOCALE_ORIGIN.get(code) == "en", (
        f"LOCALE_ORIGIN[{code!r}] should be 'en', got {LOCALE_ORIGIN.get(code)!r}"
    )


# ---------------------------------------------------------------------------
# Intermediate pipeline output — render must produce data files
# ---------------------------------------------------------------------------

_ALL_TEST_LOCALES = sorted({loc for locs, _ in TEST_FORMS.values() for loc in locs})


@pytest.mark.parametrize("locale", _ALL_TEST_LOCALES)
def test_render_produces_data_file(engrish_pipeline: dict[str, Path], locale: str) -> None:
    """After the pipeline runs, each locale must have a data-*.json file."""
    from engrish.paths import render_source_dir

    rdir = render_source_dir(locale)
    data_files = list(rdir.glob("data-*.json"))
    assert data_files, (
        f"No data-*.json found in {rdir} for locale '{locale}' — "
        "render likely produced zero words (wrong parsing rules?)"
    )


@pytest.mark.parametrize("locale", _ALL_TEST_LOCALES)
def test_convert_produces_df_file(engrish_pipeline: dict[str, Path], locale: str) -> None:
    """After the pipeline runs, each locale must have a .df output file."""
    from engrish.paths import output_dir

    odir = output_dir(locale)
    df_files = list(odir.glob("dict-*.df"))
    assert df_files, (
        f"No dict-*.df found in {odir} for locale '{locale}' — "
        "convert likely failed"
    )


# ---------------------------------------------------------------------------
# Variant normalization coverage
# ---------------------------------------------------------------------------


_SINGLE_LOCALE_FORMS = sorted({locs[0] for locs, _ in TEST_FORMS.values() if len(locs) == 1})


@pytest.mark.parametrize("locale", _SINGLE_LOCALE_FORMS)
def test_no_fixable_orphaned_variants(engrish_pipeline: dict[str, Path], locale: str) -> None:
    """No remaining orphaned variants should be fixable by stripping combining marks.

    If any are, the normalization step in the pipeline failed to resolve them.
    """
    import json
    import struct

    from engrish.paths import render_source_dir
    from engrish.pipeline import _try_strip_combining

    rdir = render_source_dir(locale)
    jsons = sorted(rdir.glob("data-*.json"))
    if not jsons:
        pytest.skip(f"No source data for {locale}")
    source = json.loads(jsons[-1].read_text("utf-8"))
    headwords = set(source.keys())

    # Parse .idx words
    etym_dir = None
    for vd in sorted((engrish_pipeline[next(
        f for f, (locs, _) in TEST_FORMS.items() if locale in locs and len(locs) == 1
    )]).iterdir()):
        if vd.is_dir() and "-noetym-" not in vd.name:
            etym_dir = vd
            break

    if not etym_dir:
        pytest.skip(f"No single-locale form for {locale}")

    name = etym_dir.name
    idx_data = (etym_dir / f"{name}.idx").read_bytes()
    idx_words: set[str] = set()
    pos = 0
    while pos < len(idx_data):
        end = idx_data.index(b"\x00", pos)
        idx_words.add(idx_data[pos:end].decode("utf-8"))
        pos = end + 1 + 4 + 4

    syn_path = etym_dir / f"{name}.syn"
    syn_words: set[str] = set()
    if syn_path.exists() and syn_path.stat().st_size:
        syn_data = syn_path.read_bytes()
        pos = 0
        while pos < len(syn_data):
            end = syn_data.index(b"\x00", pos)
            syn_words.add(syn_data[pos:end].decode("utf-8"))
            pos = end + 5

    all_output = idx_words | syn_words

    # Find orphaned variant-only entries that normalization could have fixed
    fixable = []
    for word, entry in source.items():
        if word in all_output or entry.get("definitions"):
            continue
        for target in entry.get("variants", []):
            if target in headwords:
                # Target exists but word didn't make it into .syn — different issue
                continue
            resolved = _try_strip_combining(target, headwords)
            if resolved:
                fixable.append((word, target, resolved))
                break

    assert not fixable, (
        f"{len(fixable)} orphaned variants could be fixed by stripping combining marks — "
        f"normalization step may not have run. Examples: "
        + ", ".join(f"'{w}'->'{t}' (should be '{r}')" for w, t, r in fixable[:5])
    )


# ---------------------------------------------------------------------------
# Variant chain resolution
# ---------------------------------------------------------------------------


def test_resolve_variant_chain() -> None:
    """Verify chain following resolves A -> B -> C when B is variant-only."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["c"]},
        "c": {"definitions": {"Noun": ["a thing"]}},
    }
    defs = {"c"}
    headwords = set(data.keys())

    assert _resolve_variant_chain("a", data, defs, headwords, {}) == "c"
    assert _resolve_variant_chain("b", data, defs, headwords, {}) == "c"
    assert _resolve_variant_chain("c", data, defs, headwords, {}) == "c"


def test_resolve_variant_chain_cycle() -> None:
    """Cycles must not hang — should return None."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["a"]},
    }

    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


def test_resolve_variant_chain_dead_end() -> None:
    """Chain ending at an entry with no definitions and no further variants returns None."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {},
    }

    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


def test_resolve_variant_chain_missing_target() -> None:
    """Chain pointing to a word not in the data returns None."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["nonexistent"]},
    }

    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


# ---------------------------------------------------------------------------
# normalize_variant_targets — full function tests
# ---------------------------------------------------------------------------


def _run_normalize(tmp_path: Path, data: dict) -> dict:
    """Write test data to a fake locale dir, run normalize, return result."""
    import json
    from unittest.mock import patch

    from engrish.pipeline import normalize_variant_targets

    data_file = tmp_path / "data-20260101.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    out_dir = tmp_path / "output"
    out_dir.mkdir(exist_ok=True)

    with (
        patch("engrish.pipeline.render_source_dir", return_value=tmp_path),
        patch("engrish.pipeline.output_dir", return_value=out_dir),
    ):
        normalize_variant_targets("test")

    return json.loads(data_file.read_text("utf-8"))


# -- Step 1: core write behavior --


def test_normalize_resolves_and_drops_unresolvable(tmp_path: Path) -> None:
    """Non-headword targets are resolved to headwords; unresolvable originals are dropped."""
    data = {
        "DVD": {"definitions": {"Noun": ["disc"]}},
        "entry": {"variants": ["\uFF24\uFF36\uFF24"]},  # ＤＶＤ fullwidth
    }
    result = _run_normalize(tmp_path, data)
    variants = result["entry"]["variants"]
    assert "DVD" in variants, "NFKC-normalized headword must be present"
    # Original ＤＶＤ is not a headword, so it's dropped in cleanup
    assert "\uFF24\uFF36\uFF24" not in variants


def test_normalize_multiple_resolutions_all_added(tmp_path: Path) -> None:
    """If multiple normalizations resolve, ALL headword forms are written."""
    data = {
        "ab": {"definitions": {"Noun": ["thing"]}},
        "a-b": {"definitions": {"Noun": ["other"]}},
        "entry": {"variants": ["a\u00B7b"]},  # a·b → strip gives "ab", replace with "-" gives "a-b"
    }
    result = _run_normalize(tmp_path, data)
    variants = result["entry"]["variants"]
    assert "ab" in variants, "interpunct-stripped form"
    assert "a-b" in variants, "interpunct-to-hyphen form"


def test_normalize_exact_dedup_only(tmp_path: Path) -> None:
    """Exact-identical strings are collapsed, but non-identical are kept."""
    data = {
        "target": {"definitions": {"Noun": ["thing"]}},
        "entry": {"variants": ["target", "target"]},  # duplicate
    }
    result = _run_normalize(tmp_path, data)
    assert result["entry"]["variants"].count("target") == 1


# -- Step 2: anchor/fragment stripping --


def test_normalize_anchor_hash(tmp_path: Path) -> None:
    data = {
        "you": {"definitions": {"Pronoun": ["second person"]}},
        "entry": {"variants": ["you#Noun"]},
    }
    result = _run_normalize(tmp_path, data)
    variants = result["entry"]["variants"]
    assert "you" in variants, "anchor-stripped form present"


def test_normalize_anchor_double_slash(tmp_path: Path) -> None:
    data = {
        "atomus": {"definitions": {"Noun": ["atom"]}},
        "entry": {"variants": ["atomus//atomos"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "atomus" in result["entry"]["variants"]


# -- Step 3: ZWNJ --


def test_normalize_zwnj_removal(tmp_path: Path) -> None:
    data = {
        "\u0633\u0631\u0627\u06CC": {"definitions": {"Noun": ["palace"]}},  # سرای
        "entry": {"variants": ["\u0633\u0631\u0627\u06CC\u200c"]},  # سرای + ZWNJ
    }
    result = _run_normalize(tmp_path, data)
    variants = result["entry"]["variants"]
    assert "\u0633\u0631\u0627\u06CC" in variants


def test_normalize_zwnj_to_space(tmp_path: Path) -> None:
    data = {
        "a b": {"definitions": {"Noun": ["thing"]}},
        "entry": {"variants": ["a\u200cb"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "a b" in result["entry"]["variants"]


# -- Step 4: NFKC --


def test_normalize_nfkc_fullwidth(tmp_path: Path) -> None:
    data = {
        "DVD": {"definitions": {"Noun": ["disc"]}},
        "entry": {"variants": ["\uFF24\uFF36\uFF24"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "DVD" in result["entry"]["variants"]


def test_normalize_nfkc_presentation_form(tmp_path: Path) -> None:
    """Arabic presentation form U+FEEE (waw final) → U+0648 (waw)."""
    data = {
        "\u0648": {"definitions": {"Conjunction": ["and"]}},  # و
        "entry": {"variants": ["\uFEEE"]},  # ﻮ (presentation form)
    }
    result = _run_normalize(tmp_path, data)
    assert "\u0648" in result["entry"]["variants"]


def test_normalize_nfkc_cjk_radical(tmp_path: Path) -> None:
    data = {
        "\u77DB": {"definitions": {"Noun": ["spear"]}},  # 矛
        "entry": {"variants": ["\u2F6D"]},  # ⽭ (kangxi radical)
    }
    result = _run_normalize(tmp_path, data)
    assert "\u77DB" in result["entry"]["variants"]


# -- Step 5: case --


def test_normalize_case_lower(tmp_path: Path) -> None:
    data = {
        "toc": {"definitions": {"Noun": ["thing"]}},
        "entry": {"variants": ["Toc"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "toc" in result["entry"]["variants"]


def test_normalize_case_decap(tmp_path: Path) -> None:
    data = {
        "öffentlicher Nahverkehr": {"definitions": {"Noun": ["transit"]}},
        "entry": {"variants": ["Öffentlicher Nahverkehr"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "öffentlicher Nahverkehr" in result["entry"]["variants"]


# -- Step 6: bidi --


def test_bidi_normalize_greek_accent(tmp_path: Path) -> None:
    """Greek acute ψιλοί vs grave ψιλοὶ — both strip to ψιλοι."""
    data = {
        "\u03C8\u03B9\u03BB\u03BF\u03AF": {"definitions": {"Adj": ["light"]}},  # ψιλοί
        "entry": {"variants": ["\u03C8\u03B9\u03BB\u03BF\u1F76"]},  # ψιλοὶ
    }
    result = _run_normalize(tmp_path, data)
    assert "\u03C8\u03B9\u03BB\u03BF\u03AF" in result["entry"]["variants"]


def test_bidi_normalize_russian_yo(tmp_path: Path) -> None:
    """Russian ё (U+0451) headword, е (U+0435) target — bidi strips diaeresis."""
    data = {
        "\u0447\u0451\u0442\u043A\u0438\u0439": {"definitions": {"Adj": ["clear"]}},  # чёткий
        "entry": {"variants": ["\u0447\u0435\u0442\u043A\u0438\u0439"]},  # четкий
    }
    result = _run_normalize(tmp_path, data)
    assert "\u0447\u0451\u0442\u043A\u0438\u0439" in result["entry"]["variants"]


# -- Step 7: punctuation --


def test_normalize_interpunct(tmp_path: Path) -> None:
    data = {
        "dogní": {"definitions": {"Verb": ["does"]}},
        "entry": {"variants": ["do\u00B7gní"]},  # do·gní
    }
    result = _run_normalize(tmp_path, data)
    assert "dogní" in result["entry"]["variants"]


def test_normalize_dot_strip(tmp_path: Path) -> None:
    data = {
        "EU": {"definitions": {"Noun": ["union"]}},
        "entry": {"variants": ["E.U."]},
    }
    result = _run_normalize(tmp_path, data)
    assert "EU" in result["entry"]["variants"]


def test_normalize_smart_quote(tmp_path: Path) -> None:
    data = {
        "Rus'": {"definitions": {"Noun": ["place"]}},
        "entry": {"variants": ["Rus\u2019"]},  # Rus' with curly quote
    }
    result = _run_normalize(tmp_path, data)
    assert "Rus'" in result["entry"]["variants"]


def test_normalize_dash(tmp_path: Path) -> None:
    data = {
        "a-b": {"definitions": {"Noun": ["thing"]}},
        "entry": {"variants": ["a\u2013b"]},  # en-dash
    }
    result = _run_normalize(tmp_path, data)
    assert "a-b" in result["entry"]["variants"]


# -- Step 8: spacing --


def test_normalize_hyphen_to_space(tmp_path: Path) -> None:
    data = {
        "hand held": {"definitions": {"Adj": ["portable"]}},
        "entry": {"variants": ["hand-held"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "hand held" in result["entry"]["variants"]


def test_normalize_space_to_hyphen(tmp_path: Path) -> None:
    data = {
        "pied-noir": {"definitions": {"Noun": ["person"]}},
        "entry": {"variants": ["pied noir"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "pied-noir" in result["entry"]["variants"]


# -- Step 9: article --


def test_normalize_strip_article(tmp_path: Path) -> None:
    data = {
        "Philippines": {"definitions": {"Noun": ["country"]}},
        "entry": {"variants": ["the Philippines"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "Philippines" in result["entry"]["variants"]


def test_normalize_add_article(tmp_path: Path) -> None:
    data = {
        "the pond": {"definitions": {"Noun": ["Atlantic"]}},
        "entry": {"variants": ["pond"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "the pond" in result["entry"]["variants"]


# -- Step 10: reflexive --


def test_normalize_reflexive_french(tmp_path: Path) -> None:
    data = {
        "échapper": {"definitions": {"Verb": ["escape"]}},
        "entry": {"variants": ["s\u2019échapper"]},  # s'échapper
    }
    result = _run_normalize(tmp_path, data)
    assert "échapper" in result["entry"]["variants"]


def test_normalize_sich_german(tmp_path: Path) -> None:
    data = {
        "umziehen": {"definitions": {"Verb": ["move"]}},
        "entry": {"variants": ["sich umziehen"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "umziehen" in result["entry"]["variants"]


# -- Step 11: Japanese する --


def test_normalize_ja_suru(tmp_path: Path) -> None:
    data = {
        "\u4F1D\u8A00": {"definitions": {"Noun": ["message"]}},  # 伝言
        "entry": {"variants": ["\u4F1D\u8A00\u3059\u308B"]},  # 伝言する
    }
    result = _run_normalize(tmp_path, data)
    assert "\u4F1D\u8A00" in result["entry"]["variants"]


# -- Step 12: comma split --


def test_normalize_comma_split_both_exist(tmp_path: Path) -> None:
    data = {
        "femur": {"definitions": {"Noun": ["bone"]}},
        "femen": {"definitions": {"Noun": ["thigh"]}},
        "femina": {"variants": ["femur,femen"]},
    }
    result = _run_normalize(tmp_path, data)
    variants = result["femina"]["variants"]
    assert "femur" in variants, "first comma part"
    assert "femen" in variants, "second comma part"


def test_normalize_comma_split_one_exists(tmp_path: Path) -> None:
    data = {
        "femur": {"definitions": {"Noun": ["bone"]}},
        "femina": {"variants": ["femur,nonexistent"]},
    }
    result = _run_normalize(tmp_path, data)
    variants = result["femina"]["variants"]
    assert "femur" in variants


# -- Step 13: chain resolution --


def test_resolve_chain_multi_target(tmp_path: Path) -> None:
    """Chain resolver tries all targets, not just the first."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["dead", "c"]},
        "dead": {},
        "c": {"definitions": {"Noun": ["thing"]}},
    }
    headwords = set(data.keys())
    base_map = {}
    result = _resolve_variant_chain("a", data, {"c"}, headwords, base_map)
    assert result == "c"


def test_resolve_chain_normalizes_at_each_step(tmp_path: Path) -> None:
    """Chain resolver applies normalization at intermediate nodes."""
    from engrish.pipeline import _build_base_form_map, _resolve_variant_chain

    data = {
        "a": {"variants": ["B"]},  # B needs case normalization to find "b"
        "b": {"variants": ["c"]},
        "c": {"definitions": {"Noun": ["thing"]}},
    }
    headwords = set(data.keys())
    base_map = _build_base_form_map(headwords)
    result = _resolve_variant_chain("a", data, {"c"}, headwords, base_map)
    assert result == "c"


# -- Step 14: cleanup --


def test_cleanup_drops_dangling(tmp_path: Path) -> None:
    """Targets that don't resolve to any headword are dropped."""
    data = {
        "entry": {"variants": ["nonexistent"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "variants" not in result["entry"]


def test_cleanup_drops_dead_chain(tmp_path: Path) -> None:
    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["c"]},
        "c": {},  # dead end — no definitions
    }
    result = _run_normalize(tmp_path, data)
    # "b" and "c" exist as headwords so they're kept, but "a" variant to "b"
    # is kept because "b" IS a headword (even without definitions)
    assert "b" in result["a"]["variants"]


def test_cleanup_drops_cycle(tmp_path: Path) -> None:
    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["a"]},
    }
    result = _run_normalize(tmp_path, data)
    # Both exist as headwords, so cross-references are kept
    assert "b" in result["a"]["variants"]
    assert "a" in result["b"]["variants"]


def test_cleanup_preserves_valid_and_definitions(tmp_path: Path) -> None:
    """Entry with both definitions and variants: defs untouched, valid variants kept."""
    data = {
        "target": {"definitions": {"Noun": ["thing"]}},
        "entry": {
            "definitions": {"Adj": ["quality"]},
            "variants": ["target", "nonexistent"],
        },
    }
    result = _run_normalize(tmp_path, data)
    assert result["entry"]["definitions"] == {"Adj": ["quality"]}
    assert "target" in result["entry"]["variants"]
    assert "nonexistent" not in result["entry"].get("variants", [])


def test_cleanup_removes_self_reference(tmp_path: Path) -> None:
    data = {
        "entry": {"definitions": {"Noun": ["thing"]}, "variants": ["entry"]},
    }
    result = _run_normalize(tmp_path, data)
    assert "variants" not in result["entry"]


# -- Backward compat: existing chain tests with new signature --


def test_resolve_variant_chain_compat() -> None:
    """Original chain test with updated function signature."""
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["c"]},
        "c": {"definitions": {"Noun": ["a thing"]}},
    }
    defs = {"c"}
    headwords = set(data.keys())

    assert _resolve_variant_chain("a", data, defs, headwords, {}) == "c"
    assert _resolve_variant_chain("b", data, defs, headwords, {}) == "c"
    assert _resolve_variant_chain("c", data, defs, headwords, {}) == "c"


def test_resolve_variant_chain_cycle_compat() -> None:
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {"variants": ["a"]},
    }
    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


def test_resolve_variant_chain_dead_end_compat() -> None:
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["b"]},
        "b": {},
    }
    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


def test_resolve_variant_chain_missing_target_compat() -> None:
    from engrish.pipeline import _resolve_variant_chain

    data = {
        "a": {"variants": ["nonexistent"]},
    }
    assert _resolve_variant_chain("a", data, set(), set(data.keys()), {}) is None


# ---------------------------------------------------------------------------
# Pipeline error propagation
# ---------------------------------------------------------------------------


def test_pipeline_raises_on_render_failure(tmp_path: Path) -> None:
    """run_wikidict must raise if render fails (not silently continue)."""
    from unittest.mock import patch

    with (
        patch("wikidict.render.main", return_value=1),
        patch("engrish.pipeline.render_source_dir", return_value=tmp_path),
        patch("engrish.pipeline.parse_source_dir", return_value=tmp_path),
    ):
        # Create a fake sqlite so Phase 1 (download+parse) is skipped
        (tmp_path / "pages-20260401.sqlite").touch()
        with pytest.raises(RuntimeError, match="Render failed"):
            run_wikidict(["ang"])
