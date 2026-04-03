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


def run_wikidict(locale: str) -> None:
    """Run the full wikidict pipeline for a locale, skipping steps already completed."""
    from wikidict import convert as wikidict_convert
    from wikidict import download, parse, render

    log.info("=== [%s] download ===", locale)
    download.main(locale)

    log.info("=== [%s] parse ===", locale)
    parse.main(locale)

    # render.main() has no skip-if-exists guard — check manually
    render_dir = render_source_dir(locale)
    if list(render_dir.glob("data-*.json")):
        log.info("[%s] Already rendered — skipping", locale)
    else:
        log.info("=== [%s] render ===", locale)
        render.main(locale)

    # convert.main() has no skip-if-exists guard — check manually
    out = output_dir(locale)
    if out.exists() and any(out.glob("dict-*.df")):
        log.info("[%s] Already converted — skipping", locale)
    else:
        log.info("=== [%s] convert ===", locale)
        wikidict_convert.main(locale)
