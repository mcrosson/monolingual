"""generate: build StarDict dictionaries and sampler EPUBs."""

from __future__ import annotations

import logging
import shutil

from .config import FORM_NAMES
from .epub import generate_epub
from .merge import collect_locale_res, merge_dfs, write_merged_df
from .paths import (
    dict_base_name,
    engrish_form_dir,
    get_snapshot_date,
    stardict_zip_path,
)
from .pipeline import run_wikidict
from .stardict import (
    convert_df_to_stardict,
    extract_stardict_zip,
    ifo_fields,
    patch_ifo,
)

log = logging.getLogger(__name__)


def copy_single_locale_output(locale: str, form: str, form_dir, date: str) -> None:
    """Extract StarDict ZIPs (etym + noetym) for a single locale into form_dir."""
    for noetym in (False, True):
        name = dict_base_name(form, date, noetym=noetym)
        folder = form_dir / name
        extract_stardict_zip(stardict_zip_path(locale, noetym=noetym), folder, name)
        patch_ifo(folder, name, ifo_fields(form, date, name))


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
            merged = merge_dfs(locales, noetym=noetym)
            if not merged:
                log.warning("No entries produced for noetym=%s — skipping", noetym)
                continue

            write_merged_df(merged, merged_df)
            title = f"Engrish: {form}" + (" (no etym)" if noetym else "")
            convert_df_to_stardict(merged_df, out_folder, title, date, name)
            patch_ifo(out_folder, name, ifo_fields(form, date, name))

            locale_res: dict[str, bytes] = {}
            for locale in locales:
                locale_res.update(collect_locale_res(locale, noetym=noetym))
            if locale_res:
                res_dir = out_folder / "res"
                res_dir.mkdir(exist_ok=True)
                for fname, data in locale_res.items():
                    (res_dir / fname).write_bytes(data)
                log.info("Merged %d res/ files into %s/res/", len(locale_res), out_folder.name)
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


def process_form(form: str, locales: list[str]) -> None:
    """Run the full pipeline and generate output for a single form."""
    form_dir = engrish_form_dir(form)

    for locale in locales:
        run_wikidict(locale)

    form_dir.mkdir(parents=True, exist_ok=True)
    for child in form_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    date = get_snapshot_date(locales)
    log.info("Snapshot date: %s", date)

    if len(locales) == 1:
        log.info("Copying StarDict output for single locale: %s", locales[0])
        copy_single_locale_output(locales[0], form, form_dir, date)
    else:
        log.info("Building merged StarDict for locales: %s", locales)
        build_merged_output(locales, form, form_dir, date)

    epub_path = form_dir / f"test-{dict_base_name(form, date)}.epub"
    log.info("Generating sampler EPUB: %s", epub_path)
    try:
        generate_epub(locales, epub_path)
        log.info("EPUB:   %s", epub_path)
    except FileNotFoundError as exc:
        log.error("EPUB generation failed: %s", exc)

    log.info("Done. Output: %s", form_dir)
