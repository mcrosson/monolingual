"""Wikidict pipeline wrapper and cache management."""

from __future__ import annotations

import json
import logging
import shutil
import unicodedata
from pathlib import Path

from .paths import output_dir, parse_source_dir, render_source_dir

log = logging.getLogger(__name__)

def _try_strip_combining(target: str, headwords: set[str]) -> str | None:
    """Try stripping combining characters from target to find a headword match.

    Decomposes to NFD, identifies every distinct combining character present,
    then tries removing progressively larger subsets — single marks first, then
    pairs, triples, etc. — until a candidate lands on a headword.  Trying
    smallest subsets first preserves as much of the original spelling as possible
    (e.g. keeping Greek accents while stripping only vowel-length annotations).
    """
    from itertools import combinations

    nfd = unicodedata.normalize("NFD", target)
    marks = sorted({c for c in nfd if unicodedata.combining(c)})
    if not marks:
        return None

    for r in range(1, len(marks) + 1):
        for subset in combinations(marks, r):
            drop = set(subset)
            candidate = unicodedata.normalize("NFC", "".join(c for c in nfd if c not in drop))
            if candidate != target and candidate in headwords:
                return candidate

    return None


def normalize_variant_targets(locale: str) -> None:
    """Rewrite variant targets in a locale's data-*.json so they match headwords.

    Wiktionary often uses annotation diacritics in lemma headwords (Russian
    stress marks, Greek vowel-length breves, Arabic tashkeel, Latin macrons)
    that don't appear in the actual entry keys.  For each unresolved variant
    target, we try stripping combining characters and check if the result
    matches an existing headword — the headword set is the oracle for what
    constitutes an annotation vs. a real accent.
    """
    render_dir = render_source_dir(locale)
    jsons = sorted(render_dir.glob("data-*.json"))
    if not jsons:
        return

    data_file = jsons[-1]
    data: dict = json.loads(data_file.read_text("utf-8"))
    headwords = set(data.keys())
    fixed = 0

    for word, entry in data.items():
        variants = entry.get("variants")
        if not variants:
            continue
        new_variants = []
        for target in variants:
            if target in headwords:
                new_variants.append(target)
                continue
            resolved = _try_strip_combining(target, headwords)
            if resolved:
                new_variants.append(resolved)
                fixed += 1
            else:
                new_variants.append(target)
        entry["variants"] = new_variants

    if fixed:
        log.info("[%s] Normalized %s variant targets", locale, f"{fixed:,}")
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        # Invalidate convert output so it re-runs with the updated data
        out = output_dir(locale)
        if out.exists():
            shutil.rmtree(out)
            log.info("[%s] Cleared stale convert output", locale)



def delete_cache(locales: list[str]) -> None:
    """Delete cached downloads, parse DB, render JSON, and convert output."""
    src = parse_source_dir()

    # Shared: downloads + parse DB
    for pattern in (
        "pages-*.xml.bz2",
        "pages-*.xml",
        "pages-*.sqlite",
        "pages-*.sqlite-shm",
        "pages-*.sqlite-wal",
    ):
        for f in src.glob(pattern):
            log.info("Removing %s", f)
            f.unlink(missing_ok=True)

    # Per-locale: render JSON + output dir
    seen: set[Path] = set()
    for locale in locales:
        rdir = render_source_dir(locale)
        if rdir in seen:
            continue
        seen.add(rdir)
        for f in rdir.glob("data-*.json"):
            log.info("Removing %s", f)
            f.unlink(missing_ok=True)
        odir = rdir / "output"
        if odir.exists():
            log.info("Removing %s", odir)
            shutil.rmtree(odir)


def run_wikidict(locales: list[str]) -> None:
    """Run the full wikidict pipeline for all locales.

    Groups locales by source dump so download+parse happens once per dump
    (parse.main deletes the XML after parsing, so all locales sharing a
    dump must be parsed before the XML is removed).  Then render+convert
    each locale individually.
    """
    from wikidict import convert as wikidict_convert
    from wikidict import download, parse, render, utils

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for loc in locales:
        if loc not in seen:
            seen.add(loc)
            unique.append(loc)

    # Group by source dump
    by_source: dict[str, list[str]] = {}
    for locale in unique:
        src, _ = utils.guess_locales(locale, use_log=False)
        by_source.setdefault(src, []).append(locale)

    # Phase 1: download + parse (grouped by source dump)
    # parse.main deletes the XML after parsing, so skip download entirely
    # if the .sqlite already exists — otherwise we waste ~3 min decompressing
    # a 12GB XML that won't be used.
    for src, group in by_source.items():
        src_dir = parse_source_dir()
        has_sqlite = bool(list(src_dir.glob("pages-*.sqlite")))

        if not has_sqlite:
            log.info("=== [%s] download ===", group[0])
            download.main(group[0])

            for locale in group:
                log.info("=== [%s] parse ===", locale)
                parse.main(locale)
        else:
            log.info("[%s] Already parsed — skipping download+parse", src)

    # Phase 2: render + convert (per locale)
    for locale in unique:
        render_dir = render_source_dir(locale)
        if list(render_dir.glob("data-*.json")):
            log.info("[%s] Already rendered — skipping", locale)
        else:
            log.info("=== [%s] render ===", locale)
            if render.main(locale):
                raise RuntimeError(f"Render failed for locale '{locale}'")

        normalize_variant_targets(locale)

        out = output_dir(locale)
        if out.exists() and any(out.glob("dict-*.df")):
            log.info("[%s] Already converted — skipping", locale)
        else:
            log.info("=== [%s] convert ===", locale)
            if wikidict_convert.main(locale):
                raise RuntimeError(f"Convert failed for locale '{locale}'")
