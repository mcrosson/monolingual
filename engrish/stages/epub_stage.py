"""``engrish epub`` — produce the EPUB 2 sampler for a form.

D29 chain: epub ← merged .df ← per-locale .df ← render JSON ← parse ← prepare.
The producer for the EPUB itself is ``engrish.epub.write_epub`` consuming the
form's merged ``.df`` from M6's generate stage. ``require_artifact`` recurses
into ``engrish.stages.generate.run`` to produce the merged .df if absent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engrish.epub import write_epub
from engrish.paths import dict_base_name, engrish_form_dir, get_snapshot_date
from engrish.pipeline import require_artifact

log = logging.getLogger(__name__)


def _parse_form(form: str) -> list[str]:
    codes = [c.strip() for c in form.split("+") if c.strip()]
    if not codes:
        raise ValueError(f"empty form string: {form!r}")
    return codes


def _merged_df_path(form: str, date: str) -> Path:
    return engrish_form_dir(form) / f"{dict_base_name(form, date)}.df"


def _epub_path(form: str, date: str) -> Path:
    return engrish_form_dir(form) / f"{dict_base_name(form, date)}.epub"


def run(form: str) -> int:
    """Entry: produce ``<form>.epub`` for the given form."""
    locales = _parse_form(form)

    # Ensure the merged .df exists (recurses through generate → merge → df_stage → render).
    from engrish.stages.generate import run as generate_run

    def _ensure_df() -> None:
        generate_run(form)

    # Try snapshot date first; if absent, fall through to generate which will produce render JSON
    # and let us discover the snapshot.
    try:
        date = get_snapshot_date(locales)
    except RuntimeError:
        log.info("[%s] epub: no render JSON yet; recursing into generate", form)
        generate_run(form)
        date = get_snapshot_date(locales)

    merged_df = _merged_df_path(form, date)
    require_artifact(merged_df, _ensure_df)

    epub_path = _epub_path(form, date)

    def _do_epub() -> None:
        log.info("[%s] epub: writing %s", form, epub_path)
        write_epub(
            output_path=epub_path,
            form=form,
            snapshot_date=date,
            locales=locales,
            df_path=merged_df,
        )

    require_artifact(epub_path, _do_epub)
    log.info("[%s] epub: complete (%s)", form, epub_path)
    return 0
