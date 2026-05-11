"""``engrish render`` — produce ``data/<locale>/en/data-<locale>.json`` for a locale.

D29 chain: render ← parse (SQLite) ← prepare (XML). Each producer is invoked
only if its artifact is missing (presence-on-disk = valid). Shim-engaged
throughout so ``wikidict.parse``/``wikidict.render`` see the engrish-registered
locales.

``wikidict.render.main(locale)`` produces the JSON; it reads the shared
``data/en/pages-<snap>.sqlite`` and writes ``data/<lang_dst>/<lang_src>/data-<lang_dst>.json``.
For every engrish locale, ``lang_src = "en"`` and ``lang_dst = <locale>``,
so the output lives at ``data/<locale>/en/data-<locale>.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engrish.paths import parse_source_dir, render_source_dir

log = logging.getLogger(__name__)


def _existing_json(locale: str) -> Path | None:
    """Return the most recent ``data-<snapshot>.json`` for this locale, or None."""
    files = sorted(render_source_dir(locale).glob("data-*.json"))
    return files[-1] if files else None


def latest_json(locale: str) -> Path:
    """Return the most recent render JSON for this locale; raise if absent."""
    path = _existing_json(locale)
    if path is None:
        raise FileNotFoundError(
            f"no data-*.json in {render_source_dir(locale)} for locale {locale!r}"
        )
    return path


def _sqlite_present() -> bool:
    return bool(list(parse_source_dir().glob("pages-*.sqlite")))


def _run_parse(locale: str) -> None:
    """Producer for the shared SQLite DB. Recurses into prepare if XML is missing."""
    from engrish.stages.prepare import _xml_bz2_present, run as prepare_run
    from wikidict import parse as wdparse

    if not _xml_bz2_present():
        log.info("[%s] render: XML absent; recursing into prepare", locale)
        prepare_run(locale)

    log.info("[%s] render: parsing XML → SQLite (wikidict.parse.main('en'))", locale)
    rc = wdparse.main("en")
    if rc:
        raise RuntimeError(f"wikidict.parse.main('en') returned non-zero: {rc}")
    if not _sqlite_present():
        raise RuntimeError(
            f"wikidict.parse.main('en') completed but no pages-*.sqlite appeared in {parse_source_dir()}"
        )


def _run_render(locale: str) -> None:
    """Producer for the per-locale render JSON. Recurses into parse if SQLite is missing."""
    if not _sqlite_present():
        log.info("[%s] render: SQLite absent; recursing into parse", locale)
        _run_parse(locale)

    from wikidict import render as wdrender

    log.info("[%s] render: running wikidict.render.main(%r)", locale, locale)
    rc = wdrender.main(locale)
    if rc:
        raise RuntimeError(f"wikidict.render.main({locale!r}) returned non-zero: {rc}")


def run(locale: str) -> int:
    """Entry: produce ``data/<locale>/en/data-<snapshot>.json`` for the given locale.

    Returns 0 on success. Uses ``require_artifact`` via a sentinel path (matches
    the glob expectation of ``_existing_json``) so the producer is only invoked
    when no ``data-*.json`` exists. Chain (render → parse → prepare) is composed
    in ``_run_render``.
    """
    render_dir = render_source_dir(locale)
    render_dir.mkdir(parents=True, exist_ok=True)

    if _existing_json(locale) is None:
        _run_render(locale)
        if _existing_json(locale) is None:
            raise RuntimeError(
                f"wikidict.render.main({locale!r}) completed without producing "
                f"data-*.json in {render_dir}"
            )

    json_path = latest_json(locale)
    log.info("[%s] render: JSON present at %s", locale, json_path)
    return 0
