"""Tests for the engrish module — uses real Wiktionary pipeline data."""

from __future__ import annotations

import gzip
import json
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

TEST_FORMS = {
    "en": ["en"],
    "ang+enm": ["ang", "enm"],
    "ang+en": ["ang", "en"],
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

    project_root = str(Path(__file__).parent.parent)
    os.environ["CWD"] = project_root
    cfg.DATA_DIR = Path(project_root) / "data"
    cfg.ENGRISH_DIR = cfg.DATA_DIR / "engrish"

    # Run pipeline for all needed locales
    for locale in ("en", "ang", "enm"):
        run_wikidict(locale)

    # Generate each test form (skip if output already exists)
    result: dict[str, Path] = {}
    for form, locales in TEST_FORMS.items():
        if not _form_output_exists(form):
            generate.process_form(form, locales)
        result[form] = engrish_form_dir(form)

    return result


@pytest.fixture(scope="session")
def engrish_epubs(engrish_pipeline: dict[str, Path]) -> dict[str, Path]:
    """Generate EPUBs for all test forms. Returns dict of form -> epub path."""
    result: dict[str, Path] = {}
    for form, locales in TEST_FORMS.items():
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
    locales = TEST_FORMS[form]
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
    locales = TEST_FORMS[form]
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
    locales = TEST_FORMS[form]
    merged = merge.merge_dfs(locales, noetym=False)

    assert merged, "merge_dfs returned empty dict"
    assert "gold" in merged, "'gold' should be in every locale"

    _, html = merged["gold"]
    if len(locales) > 1:
        # Multi-locale: should have <h3> headers for each locale
        for loc in locales:
            assert f"<h3>{FORM_NAMES[loc]}</h3>" in html, (
                f"Missing <h3> header for {FORM_NAMES[loc]} in merged 'gold'"
            )
    else:
        # Single locale: no <h3> headers
        assert "<h3>" not in html, "Single-locale merge should not have <h3> headers"


# ---------------------------------------------------------------------------
# EPUB verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_epub_structure(engrish_epubs: dict[str, Path], form: str) -> None:
    """Verify EPUB is a valid ZIP with expected structure."""
    epub_path = engrish_epubs[form]
    locales = TEST_FORMS[form]

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
        assert "OEBPS/fonts/Charis-Regular.woff" in names
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


# ---------------------------------------------------------------------------
# StarDict file verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORM_IDS)
def test_stardict_oft_files(engrish_pipeline: dict[str, Path], form: str) -> None:
    """Verify .oft files have correct StarDict cache header."""
    form_dir = engrish_pipeline[form]
    locales = TEST_FORMS[form]
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
    locales = TEST_FORMS[form]
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
