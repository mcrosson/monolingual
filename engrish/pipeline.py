"""Wikidict pipeline wrapper and cache management."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .paths import output_dir, parse_source_dir, render_source_dir

log = logging.getLogger(__name__)


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
            render.main(locale)

        out = output_dir(locale)
        if out.exists() and any(out.glob("dict-*.df")):
            log.info("[%s] Already converted — skipping", locale)
        else:
            log.info("=== [%s] convert ===", locale)
            wikidict_convert.main(locale)
