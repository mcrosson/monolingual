"""generate: build StarDict dictionaries and sampler EPUBs."""

from __future__ import annotations

import gc
import itertools
import logging
import shutil

from .merge import clear_df_cache, collect_locale_res, iter_merged_dfs, write_merged_df
from .paths import (
    dict_base_name,
    engrish_form_dir,
    get_snapshot_date,
)
from .stardict import (
    convert_df_to_stardict,
    ifo_fields,
    patch_ifo,
)

log = logging.getLogger(__name__)


def build_merged_output(locales: list[str], form: str, form_dir, date: str) -> None:
    """Merge .df files and generate StarDict for both etym and noetym variants."""
    tmp_base = form_dir / "_tmp"
    tmp_base.mkdir(parents=True, exist_ok=True)

    try:
        for noetym in (False, True):
            name = dict_base_name(form, date, noetym=noetym)
            merged_df = tmp_base / f"{name}.df"
            out_folder = form_dir / name

            log.info("Merging .df files (noetym=%s) → %s", noetym, merged_df)
            entries = iter_merged_dfs(locales, noetym=noetym)
            # Peek at the first entry to check for empty output before writing
            first = next(entries, None)
            if first is None:
                log.warning("No entries produced for noetym=%s — skipping", noetym)
                continue

            write_merged_df(merged_df, itertools.chain([first], entries))

            title = f"Engrish: {form}" + (" (no etym)" if noetym else "")
            convert_df_to_stardict(merged_df, out_folder, title, date, name)
            patch_ifo(out_folder, name, ifo_fields(form, date, name))

            # PyGlossary writes un-prefixed resource files to out_folder/res/ during
            # conversion. Clear them so only correctly locale-prefixed files remain.
            pyglossary_res = out_folder / "res"
            if pyglossary_res.exists():
                shutil.rmtree(pyglossary_res)

            locale_res: dict[str, bytes] = {}
            for locale in locales:
                locale_res.update(collect_locale_res(locale, noetym=noetym))
            if locale_res:
                res_dir = out_folder / "res"
                res_dir.mkdir(exist_ok=True)
                for fname, data in locale_res.items():
                    (res_dir / fname).write_bytes(data)
                log.info("Merged %d res/ files into %s/res/", len(locale_res), out_folder.name)
            del locale_res
            gc.collect()
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


def process_form(form: str, locales: list[str]) -> None:
    """Generate engrish output for a single form.

    Assumes the wikidict pipeline has already been run for all locales
    (call run_wikidict before this).
    """
    form_dir = engrish_form_dir(form)

    form_dir.mkdir(parents=True, exist_ok=True)
    existing = list(form_dir.iterdir())
    if existing:
        log.warning("Wiping existing output: %s", form_dir)
        for child in existing:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    date = get_snapshot_date(locales)
    log.info("Snapshot date: %s", date)

    log.info("Building StarDict for locales: %s", locales)
    build_merged_output(locales, form, form_dir, date)

    # Clear .df cache to free memory after form is complete
    clear_df_cache()
    gc.collect()

    log.info("Done. Output: %s", form_dir)
