"""Path helpers for locating pipeline data."""

from __future__ import annotations

from pathlib import Path

from . import config


def parse_source_dir() -> Path:
    """Shared parse DB directory (all locales use the EN Wiktionary)."""
    return config.DATA_DIR / "en"


def render_source_dir(locale: str) -> Path:
    """Render JSON directory for a locale."""
    from wikidict import utils

    lang_src, lang_dst = utils.guess_locales(locale, use_log=False)
    return config.DATA_DIR / lang_dst / lang_src


def output_dir(locale: str) -> Path:
    return render_source_dir(locale) / "output"


def df_path(locale: str, noetym: bool = False) -> Path:
    from wikidict import utils

    lang_src, lang_dst = utils.guess_locales(locale, use_log=False)
    suffix = "-noetym" if noetym else ""
    return output_dir(locale) / f"dict-{lang_src}-{lang_dst}{suffix}.df"


def stardict_zip_path(locale: str, noetym: bool = False) -> Path:
    from wikidict import utils

    lang_src, lang_dst = utils.guess_locales(locale, use_log=False)
    suffix = "-noetym" if noetym else ""
    return output_dir(locale) / f"dict-{lang_src}-{lang_dst}{suffix}.zip"


def engrish_form_dir(form: str) -> Path:
    return config.ENGRISH_DIR / form.replace("+", "-")


def dict_base_name(form: str, date: str, noetym: bool = False) -> str:
    """Return the StarDict folder/file base name for a given form and snapshot date.

    The '-en' suffix denotes the EN Wiktionary source dump (all engrish
    dictionaries extract from en.wiktionary), not the Modern English locale.
    """
    suffix = "-noetym" if noetym else ""
    return f"{form.replace('+', '_')}-en{suffix}-{date}"


def get_snapshot_date(locales: list[str]) -> str:
    """Return the 8-digit snapshot date (YYYYMMDD) from a locale's render JSON filename."""
    for locale in locales:
        for f in render_source_dir(locale).glob("data-*.json"):
            return f.stem[5:]  # strips "data-"
    raise RuntimeError(
        f"Cannot determine snapshot date: no data-*.json found for locales {locales}. "
        "Run the pipeline first."
    )


def get_sqlite_path() -> Path:
    """Find the most recent SQLite database for the EN Wiktionary dump."""
    src = parse_source_dir()
    dbs = sorted(src.glob("pages-*.sqlite"))
    if not dbs:
        raise RuntimeError(f"No SQLite database found in {src}")
    return dbs[-1]
