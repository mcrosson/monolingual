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
    "grc": (["grc"], None),
    "ru": (["ru"], None),
    "fr": (["fr"], None),
    "en": (["en"], None),
    "el": (["el"], None),
    "cu": (["cu"], None),
    "ang+enm+en": (["ang", "enm", "en"], True),
    "ru+grc": (["ru", "grc"], False),
    "grc+el": (["grc", "el"], True),
    "ru+cu": (["ru", "cu"], True),
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
        assert "OEBPS/fonts/Gentium-Regular.woff" in names
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
        assert "fr" in cfg
        assert cfg["fr"]["wiktionary_section"] == "french"
        assert cfg["fr"]["display_name"] == "French"

        # Idempotent — adding again should skip
        result = add_lang.run(["fr"], all_langs=False)
        assert result == 0

        # Unknown code should fail
        result = add_lang.run(["zzz_invalid"], all_langs=False)
        assert result == 1
    finally:
        add_lang._ENGRISH_JSON = original


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
_NATIVE_MODULE_LOCALES = ["el", "ru", "de", "fr", "es", "it"]

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
