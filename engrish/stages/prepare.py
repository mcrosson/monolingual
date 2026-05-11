"""``engrish prepare`` — download the EN Wiktionary dump.

Thin orchestration wrapper around ``wikidict.download.main("en")``. Every
engrish locale sources from the same EN dump (D22), so ``prepare`` downloads
once and all subsequent stages share the artifact via
``engrish.paths.parse_source_dir()``.

Idempotent: if ``data/en/pages-<YYYYMMDD>.xml.bz2`` already exists, the
download is a no-op (per D29 presence-on-disk = valid).
"""

from __future__ import annotations

import logging

from engrish.paths import parse_source_dir

log = logging.getLogger(__name__)


def _xml_bz2_present() -> bool:
    """Return True if any ``pages-*.xml.bz2`` exists in the parse source dir."""
    return bool(list(parse_source_dir().glob("pages-*.xml.bz2")))


def run(locale: str) -> int:
    """Entry: download the EN dump if absent. ``locale`` is unused (logged for traceability).

    Returns 0 on success. Raises if ``wikidict.download.main`` fails.
    """
    if _xml_bz2_present():
        log.info("[%s] prepare: pages-*.xml.bz2 already present in %s; skipping download", locale, parse_source_dir())
        return 0

    from wikidict import download

    log.info("[%s] prepare: downloading EN Wiktionary dump into %s ...", locale, parse_source_dir())
    download.main("en")

    if not _xml_bz2_present():
        raise RuntimeError(
            f"prepare completed but no pages-*.xml.bz2 appeared in {parse_source_dir()}; "
            "wikidict.download.main('en') may have failed silently."
        )
    return 0
