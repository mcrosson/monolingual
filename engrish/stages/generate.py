"""``engrish generate`` — merge per-locale ``.df`` files + produce StarDict.

Owns the M6 top-level flow:

1. For each locale in the form, call ``engrish.stages.df_stage.ensure_locale_df``
   (which itself recurses through render / parse / prepare per D29).
2. Merge the per-locale ``.df`` files into a single form-level ``.df`` via
   ``engrish.merge.merge_dfs``.
3. Convert the merged ``.df`` to StarDict via ``engrish.stardict_writer.convert_df_to_stardict``.

Form parsing: forms are ``+``-separated locale lists, e.g. ``en+ang+enm``.
The form directory lives at ``engrish.paths.engrish_form_dir(form)``
(= ``data/engrish/<form-with-dashes>/``). Final filenames use
``engrish.paths.dict_base_name(form, date)``.

Per M6-AC2: produces ``<F>.df``, ``<F>.ifo``, ``<F>.idx``, ``<F>.dict``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engrish.merge import merge_dfs
from engrish.paths import dict_base_name, engrish_form_dir, get_snapshot_date
from engrish.pipeline import require_artifact
from engrish.stages.df_stage import ensure_locale_df
from engrish.stardict_writer import convert_df_to_stardict

log = logging.getLogger(__name__)


def _parse_form(form: str) -> list[str]:
    """Split ``"en+ang+enm"`` → ``["en", "ang", "enm"]``. Raise on empty codes."""
    codes = [c.strip() for c in form.split("+") if c.strip()]
    if not codes:
        raise ValueError(f"empty form string: {form!r}")
    return codes


def _merged_df_path(form: str, date: str) -> Path:
    form_dir = engrish_form_dir(form)
    return form_dir / f"{dict_base_name(form, date)}.df"


def run(form: str) -> int:
    """Entry: produce the four StarDict files for ``form``.

    ``form`` is ``+``-separated locale codes (I19 surface).
    """
    locales = _parse_form(form)

    # Step 1: ensure per-locale .df files.
    per_locale_df: list[Path] = []
    for loc in locales:
        per_locale_df.append(ensure_locale_df(loc))

    # Step 2: determine snapshot date from the render JSONs.
    date = get_snapshot_date(locales)
    form_dir = engrish_form_dir(form)
    form_dir.mkdir(parents=True, exist_ok=True)

    # Step 3: k-way merge per-locale → merged .df.
    merged_df = _merged_df_path(form, date)

    def _do_merge() -> None:
        log.info("[%s] generate: merging %d per-locale .df → %s", form, len(per_locale_df), merged_df)
        merge_dfs(per_locale_df, merged_df, form_locales=locales)

    require_artifact(merged_df, _do_merge)

    # Step 4: StarDict assembly via PyGlossary.
    dict_name = dict_base_name(form, date)
    ifo_path = form_dir / f"{dict_name}.ifo"

    def _do_stardict() -> None:
        log.info("[%s] generate: converting %s → StarDict at %s", form, merged_df, form_dir)
        convert_df_to_stardict(
            df_src=merged_df,
            out_folder=form_dir,
            title=f"Engrish: {form}",
            date=date,
            dict_name=dict_name,
        )

    require_artifact(ifo_path, _do_stardict)

    log.info("[%s] generate: complete (%s)", form, form_dir)
    return 0
