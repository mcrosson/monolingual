"""``engrish add-language`` — add languages to engrish.json from the EN dump.

D29 chain: add-language ← parse (SQLite) ← prepare (XML).

This is the final M11-AC4 migration of the legacy add-language path. Replaces:
- ``engrish/add_language.py`` (the orchestrator + import to broken
  ``engrish.font.collect_headword_chars_batch``)
- ``engrish/stats.py::load_language_codes`` (inlined here)
- ``engrish/font_legacy.py::collect_headword_chars_batch`` + ``detect_fonts``
  + ``_scan_chunk`` + ``_best_stem_for_char`` + ``_greedy_set_cover``
  (inlined here; I/O helpers moved to ``engrish.font_io``)
- ``engrish/pipeline_legacy.py::ensure_wikidict_parsed`` (replaced with the
  same D29 require_artifact pattern used in ``engrish.stages.render._run_parse``)

Per M3-AC2 (deferred from M11-AC3 → folded into M11-AC4): full integration
testing requires an initialized Wiktionary SQLite dump fixture. Test landed
at ``engrish/tests/test_m11_add_language.py`` (cost-gated by
``ENGRISH_RUN_M11_INTEGRATION=1``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from engrish.config import ENGRISH_JSON_PATH, FONTS_DIR, SEED_FONTS
from engrish.font_io import (
    cmap_for_stem,
    discover_available_stems,
    download_font,
    existing_stems,
    find_font_file,
)
from engrish.paths import get_sqlite_path, parse_source_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D29 upstream-artifact recursion
# ---------------------------------------------------------------------------


def _xml_bz2_present() -> bool:
    return bool(list(parse_source_dir().glob("pages-*.xml.bz2")))


def _sqlite_present() -> bool:
    return bool(list(parse_source_dir().glob("pages-*.sqlite")))


def _ensure_wikidict_parsed() -> Path:
    """D29-recursive: ensure pages-*.sqlite exists. Recurse into prepare if XML missing."""
    if _sqlite_present():
        return get_sqlite_path()

    if not _xml_bz2_present():
        from engrish.stages.prepare import run as prepare_run
        log.info("add-language: XML absent; recursing into prepare")
        prepare_run("en")

    from wikidict import parse as wdparse
    log.info("add-language: SQLite absent; running wikidict.parse for en")
    wdparse.main("en")

    if not _sqlite_present():
        raise RuntimeError(
            f"wikidict.parse('en') completed but no pages-*.sqlite appeared in "
            f"{parse_source_dir()}"
        )
    return get_sqlite_path()


# ---------------------------------------------------------------------------
# Language-code lookup (was engrish.stats.load_language_codes)
# ---------------------------------------------------------------------------


def load_language_codes(db_path: Path) -> dict[str, str]:
    """Extract language name → ISO code mapping from Module:languages data."""
    name_to_code: dict[str, str] = {}
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT body FROM pages "
            "WHERE title LIKE 'Module:languages/data%' "
            "OR title LIKE 'Module:etymology languages/data%'"
        )
        for (body,) in cur:
            if body is None:
                continue
            for m in re.finditer(r'm\["([^"]+)"\]\s*=\s*\{\s*"([^"]+)"', body):
                code, name = m.group(1), m.group(2)
                name_to_code[name] = code
    finally:
        con.close()
    return name_to_code


# ---------------------------------------------------------------------------
# Parallel headword-character scan (was font_legacy._scan_chunk + collect_headword_chars_batch)
# ---------------------------------------------------------------------------


def _scan_chunk(
    db_path: str,
    targets_lower: list[str],
    row_lo: int,
    row_hi: int,
) -> dict[str, set[str]]:
    """Worker: scan a rowid range for headword chars across all target languages.

    Each worker opens its own SQLite connection so there is no GIL contention.
    Module-level for ProcessPoolExecutor pickling. Returns ``{section_lower: chars}``.
    """
    l2 = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.MULTILINE)
    target_set = set(targets_lower)
    result: dict[str, set[str]] = {t: set() for t in targets_lower}
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT title, body FROM pages "
            "WHERE namespace_id = 0 AND rowid >= ? AND rowid < ?",
            (row_lo, row_hi),
        )
        for title, body in cur:
            if body is None:
                continue
            body_lower = body.lower()
            if not any(t in body_lower for t in target_set):
                continue
            for m in l2.finditer(body):
                heading = m.group(1).lower()
                if heading in target_set:
                    for ch in title:
                        if ord(ch) > 127:
                            result[heading].add(ch)
    finally:
        con.close()
    return result


def collect_headword_chars_batch(
    locale_codes: list[str],
    db_path: Path,
    *,
    wiktionary_sections: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """Collect headword chars for one or more languages in a single parallel scan.

    Partitions the dump by rowid range across ~half cpu_count() workers
    (I/O-bound; diminishing returns past 4–6 workers). Returns
    ``{code: set_of_non_ascii_chars}``.
    """
    sections: dict[str, str] = {}
    for code in locale_codes:
        if wiktionary_sections and code in wiktionary_sections:
            sections[code] = wiktionary_sections[code]
        else:
            raise ValueError(
                f"Locale '{code}' has no wiktionary_section. "
                "Pass via wiktionary_sections={code: section} (add-language is "
                "the canonical caller; the section name comes from "
                "load_language_codes() inverted)."
            )

    targets_lower = [s.lower() for s in sections.values()]
    db_str = str(db_path)

    con = sqlite3.connect(db_str)
    try:
        cur = con.cursor()
        cur.execute("SELECT MIN(rowid), MAX(rowid) FROM pages WHERE namespace_id = 0")
        row = cur.fetchone()
        if row is None or row[0] is None:
            return {code: set() for code in locale_codes}
        lo, hi = row
    finally:
        con.close()

    n_workers = max(2, (os.cpu_count() or 4) // 2)
    chunk_size = (hi - lo + 2) // n_workers
    chunks = []
    for i in range(n_workers):
        c_lo = lo + i * chunk_size
        c_hi = min(lo + (i + 1) * chunk_size, hi + 1)
        if c_lo < c_hi:
            chunks.append((db_str, targets_lower, c_lo, c_hi))

    merged: dict[str, set[str]] = {t: set() for t in targets_lower}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        try:
            for partial in pool.map(_scan_chunk, *zip(*chunks)):
                for t in targets_lower:
                    merged[t].update(partial[t])
        except Exception as exc:
            log.error("Parallel dump scan failed: %s", exc)
            raise

    return {code: merged[sections[code].lower()] for code in locale_codes}


# ---------------------------------------------------------------------------
# Font detection (was font_legacy.detect_fonts + _greedy_set_cover)
# ---------------------------------------------------------------------------


def _greedy_set_cover(
    remaining: set[int],
    candidates: dict[str, set[int]],
) -> list[tuple[str, set[int]]]:
    """Greedy set cover: pick stems from candidates that cover remaining codepoints.

    Returns [(stem, covered_cps), ...] in selection order. Modifies neither
    remaining nor candidates.
    """
    pool = dict(candidates)
    left = set(remaining)
    selected: list[tuple[str, set[int]]] = []
    while left:
        best_stem: str | None = None
        best_count = 0
        best_covered: set[int] = set()
        for stem, cmap_cps in pool.items():
            covered = left & cmap_cps
            if len(covered) > best_count:
                best_stem = stem
                best_count = len(covered)
                best_covered = covered
        if not best_stem:
            break
        pool.pop(best_stem)
        left -= best_covered
        selected.append((best_stem, best_covered))
    return selected


def detect_fonts(
    locale_code: str,
    db_path: Path,
    chars: set[str] | None = None,
    *,
    wiktionary_section: str | None = None,
) -> list[str]:
    """Detect font stems needed for a language.

    Returns exactly the validated set of stems that cover the headword chars.
    """
    if chars is None:
        if not wiktionary_section:
            raise ValueError("either chars or wiktionary_section must be provided")
        chars = collect_headword_chars_batch(
            [locale_code], db_path, wiktionary_sections={locale_code: wiktionary_section}
        )[locale_code]
    if not chars:
        return []

    log.info("Found %d unique non-ASCII headword characters for %s", len(chars), locale_code)

    # Stage 0: ensure seed fonts on disk (download if missing)
    if SEED_FONTS:
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        for stem in SEED_FONTS:
            if not find_font_file(stem, FONTS_DIR):
                log.info("Downloading seed font %s...", stem)
                if not download_font(stem, FONTS_DIR):
                    raise RuntimeError(
                        f"Seed font '{stem}' could not be downloaded. "
                        "Check the seed_fonts list in engrish.json."
                    )

    # Discover full set of available font stems (local + remote)
    remote_available, cjk_stems = discover_available_stems()
    available = existing_stems(FONTS_DIR) | remote_available

    # Stage 1: build candidate stems from character name prefixes; download
    # those that exist remotely but not on disk
    candidates: set[str] = set()
    for ch in chars:
        name = unicodedata.name(ch, None)
        if not name:
            continue
        words = name.split()
        for n in range(1, min(5, len(words) + 1)):
            suffix = "".join(w.title() for w in words[:n])
            for prefix in ("NotoSans", "NotoSerif"):
                candidates.add(prefix + suffix)
                candidates.add(prefix + suffix + "s")

    to_download = (candidates & available) - existing_stems(FONTS_DIR)
    if to_download:
        log.info("Downloading %d name-matched font(s)...", len(to_download))
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        for stem in sorted(to_download):
            download_font(stem, FONTS_DIR)

    # Stage 2: cmap-verified greedy set cover in 4 rounds
    all_cps = {ord(ch) for ch in chars}
    confirmed = candidates & available
    stem_cmaps: dict[str, set[int]] = {}
    result: set[str] = set()
    remaining_cps = set(all_cps)

    def _load_stems(stems: set[str]) -> None:
        for stem in stems:
            if stem not in stem_cmaps:
                cm = cmap_for_stem(stem, FONTS_DIR)
                if cm:
                    stem_cmaps[stem] = cm

    def _run_cover() -> None:
        nonlocal remaining_cps
        selected = _greedy_set_cover(remaining_cps, stem_cmaps)
        for stem, covered in selected:
            result.add(stem)
            remaining_cps -= covered

    if SEED_FONTS:
        _load_stems(set(SEED_FONTS))
        _run_cover()

    if remaining_cps:
        _load_stems(confirmed)
        _run_cover()

    if remaining_cps:
        _load_stems(existing_stems(FONTS_DIR))
        _run_cover()

    if remaining_cps and cjk_stems:
        cjk_to_download = cjk_stems - existing_stems(FONTS_DIR)
        if cjk_to_download:
            log.info("Downloading %d CJK font(s)...", len(cjk_to_download))
            FONTS_DIR.mkdir(parents=True, exist_ok=True)
            for stem in sorted(cjk_to_download):
                download_font(stem, FONTS_DIR)
        _load_stems(cjk_stems)
        _run_cover()

    # Warn about uncovered characters
    unmatched_chars = {ch for ch in chars if ord(ch) in remaining_cps}
    if unmatched_chars:
        char_list = "\n".join(
            f"  U+{ord(ch):04X}  {unicodedata.name(ch, '?')}"
            for ch in sorted(unmatched_chars)
        )
        log.warning(
            "%d headword character(s) could not be matched to any available "
            "NotoSans font:\n%s",
            len(unmatched_chars),
            char_list,
        )
        log.warning(
            "To add coverage: download the appropriate .ttf/.otf font, place "
            "it in %s, and add the font stem to the 'fonts' list in the "
            "config entry for this language.",
            FONTS_DIR,
        )

    return sorted(result)


# ---------------------------------------------------------------------------
# Config I/O + CLI entry
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    if ENGRISH_JSON_PATH.exists():
        return json.loads(ENGRISH_JSON_PATH.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg: dict) -> None:
    ENGRISH_JSON_PATH.write_text(
        json.dumps(cfg, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(langs: list[str] | None, *, all_langs: bool = False) -> int:
    """Resolve language codes from the dump and add them to engrish.json."""
    db_path = _ensure_wikidict_parsed()
    name_to_code = load_language_codes(db_path)
    code_to_name = {code: name for name, code in name_to_code.items()}

    cfg = _load_config()
    languages = cfg.setdefault("languages", {})

    if all_langs:
        langs = sorted(code_to_name.keys())
        log.info("Adding all %d languages", len(langs))

    if langs is None:
        langs = []

    errors: list[str] = []
    to_add: list[tuple[str, str]] = []
    for code in langs:
        if code in languages:
            log.info("Skipping '%s' — already configured", code)
            continue
        if code not in code_to_name:
            errors.append(
                f"Unknown language code '{code}'. Run 'language-stats' to see available codes."
            )
            continue
        to_add.append((code, code_to_name[code]))

    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if not to_add:
        log.info("Nothing to add")
        return 0

    codes_to_add = [code for code, _ in to_add]
    ws_map = {code: name.lower() for code, name in to_add}
    log.info("Scanning dump for %d language(s)...", len(codes_to_add))
    all_chars = collect_headword_chars_batch(
        codes_to_add, db_path, wiktionary_sections=ws_map
    )

    for code, name in to_add:
        log.info("Detecting fonts for %s (%s)...", code, name)
        fonts = detect_fonts(
            code, db_path, chars=all_chars[code], wiktionary_section=name.lower()
        )
        languages[code] = {
            "wiktionary_section": name.lower(),
            "display_name": name,
            "fonts": fonts,
        }
        log.info("Added: %s (%s) — fonts: %s", code, name, ", ".join(fonts))

    _save_config(cfg)
    log.info("Updated %s — %d language(s) added", ENGRISH_JSON_PATH, len(to_add))

    return 0
