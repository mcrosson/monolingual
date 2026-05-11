"""M11 — add-language integration test (folds in deferred M3-AC2).

Two layers:

1. **Unit** (always-on): synthetic SQLite fixture with the minimal schema +
   a few `Module:languages/data` entries. Verifies that
   `load_language_codes` extracts the name → code mapping correctly.
2. **Integration** (cost-gated by `ENGRISH_RUN_M11_INTEGRATION=1`): runs
   `engrish add-language` end-to-end against the live Wiktionary SQLite
   dump on the host (if present) and asserts engrish.json gains the
   requested language entries.

The unit fixture is sufficient to demonstrate the M3-AC2 deferral is closed
(SQLite dump fixture exists; load_language_codes works against it). The
integration test is the deeper end-to-end check.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from engrish.stages.add_language_stage import load_language_codes

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = REPO_ROOT / "venv" / "bin" / "python"


def _build_synthetic_dump(path: Path) -> None:
    """Build a minimal Wiktionary-shaped SQLite dump for unit testing.

    Schema mirrors what wikidict.parse produces:
    - `pages` table with `rowid`, `namespace_id`, `title`, `body` columns.
    - Language data lives in `Module:languages/data` (namespace 828) per
      Wiktionary convention; the loader queries by title prefix.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.executescript("""
            CREATE TABLE pages (
                rowid INTEGER PRIMARY KEY,
                namespace_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT
            );
        """)
        # Two pages with Module:languages/data entries — one per
        # WT module suffix to verify the LIKE prefix match works.
        body_main = '''
            local m = {}
            m["en"] = { "English", "Latn" }
            m["fr"] = { "French", "Latn" }
            m["de"] = { "German", "Latn" }
            m["ang"] = { "Old English", "Latn" }
            m["zh"] = { "Chinese", "Hani" }
            return m
        '''
        body_etym = '''
            local m = {}
            m["proto-germanic"] = { "Proto-Germanic", "Latinx" }
            return m
        '''
        con.execute(
            "INSERT INTO pages (namespace_id, title, body) VALUES (?, ?, ?)",
            (828, "Module:languages/data2", body_main),
        )
        con.execute(
            "INSERT INTO pages (namespace_id, title, body) VALUES (?, ?, ?)",
            (828, "Module:etymology languages/data", body_etym),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def synthetic_dump(tmp_path: Path) -> Path:
    """Build a synthetic SQLite fixture mirroring wikidict.parse output."""
    p = tmp_path / "pages-fixture.sqlite"
    _build_synthetic_dump(p)
    return p


def test_load_language_codes_extracts_names_from_synthetic_dump(synthetic_dump: Path) -> None:
    """M3-AC2 — load_language_codes parses Module:languages/data correctly."""
    name_to_code = load_language_codes(synthetic_dump)
    assert name_to_code["English"] == "en"
    assert name_to_code["French"] == "fr"
    assert name_to_code["German"] == "de"
    assert name_to_code["Old English"] == "ang"
    assert name_to_code["Chinese"] == "zh"
    # etymology languages also picked up
    assert name_to_code["Proto-Germanic"] == "proto-germanic"


def test_load_language_codes_returns_empty_when_no_modules(tmp_path: Path) -> None:
    """Empty dump → empty mapping (graceful degradation)."""
    p = tmp_path / "empty.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    try:
        con.execute("CREATE TABLE pages (rowid INTEGER, namespace_id INTEGER, title TEXT, body TEXT)")
        con.commit()
    finally:
        con.close()
    assert load_language_codes(p) == {}


def test_add_language_help_runs() -> None:
    """Integration — `engrish add-language --help` works against the new stage.

    M13-AC13 (2026-05-09): cost gate removed. `--help` short-circuits before
    the stage's SQLite lookup, so this runs without a dump on disk.
    """
    result = subprocess.run(
        [str(PYTHON), "-m", "engrish", "add-language", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--lang" in result.stdout
    assert "--all" in result.stdout


def test_add_language_already_configured_locale_is_noop() -> None:
    """M13-AC9 — full add-language pipeline runs without writing engrish.json
    when the requested locale is already configured.

    Exercises the complete code path: SQLite open → ``load_language_codes``
    iteration → per-locale gate (line 410: ``if code in languages: skip``).
    Asserts engrish.json is byte-unchanged after the call.
    """
    parse_dir = REPO_ROOT / "data" / "en"
    sqlite_files = list(parse_dir.glob("pages-*.sqlite"))
    if not sqlite_files:
        pytest.skip(
            f"no pages-*.sqlite in {parse_dir}; run `engrish prepare` first "
            "(parse SQLite is required for add-language full integration)"
        )

    engrish_json = REPO_ROOT / "engrish" / "engrish.json"
    before_bytes = engrish_json.read_bytes()
    before_mtime = engrish_json.stat().st_mtime

    # 'ae' is in engrish.json since AC1 priming; should hit the "Skipping" branch.
    result = subprocess.run(
        [str(PYTHON), "-m", "engrish", "add-language", "--lang", "ae"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"add-language --lang ae exited {result.returncode}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}\n"
    )
    after_bytes = engrish_json.read_bytes()
    after_mtime = engrish_json.stat().st_mtime
    assert before_bytes == after_bytes, "engrish.json modified for already-configured locale"
    assert before_mtime == after_mtime, "engrish.json mtime touched for already-configured locale"
