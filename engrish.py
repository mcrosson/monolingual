#!/usr/bin/env python3
"""engrish.py — Build English-family dictionaries (Modern, Middle, Old English).

Usage:
    python engrish.py --engrish-form FORM [--no-cache]

Forms:
    en            Modern English only
    enm           Middle English only
    ang           Old English only
    enm+en        Middle + Modern English (merged)
    ang+en        Old + Modern English (merged)
    ang+enm+en    Old + Middle + Modern English (merged)
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import random
import shutil
import struct
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCALES_FOR_FORM: dict[str, list[str]] = {
    "en": ["en"],
    "enm": ["enm"],
    "ang": ["ang"],
    "enm+en": ["en", "enm"],
    "ang+en": ["en", "ang"],
    "ang+enm+en": ["en", "enm", "ang"],
}

FORM_NAMES = {
    "en": "Modern English",
    "enm": "Middle English",
    "ang": "Old English",
}

# Display order: modern → middle → old
DISPLAY_ORDER = ["en", "enm", "ang"]

# Words with verified substantive definitions in all three English periods.
# Used for the "Universal" epub chapter.
UNIVERSAL_WORDS = ["amen", "bolster", "brand", "colt", "gold", "word"]

DATA_DIR = Path(os.getenv("CWD", "")) / "data"
ENGRISH_DIR = DATA_DIR / "engrish"
FONTS_DIR = Path(__file__).parent / "fonts" / "Charis-7.000" / "web"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def parse_source_dir() -> Path:
    """Shared parse DB directory (all three locales use the EN Wiktionary)."""
    return DATA_DIR / "en"


def render_source_dir(locale: str) -> Path:
    """Render JSON directory for a locale."""
    from wikidict import utils
    lang_src, lang_dst = utils.guess_locales(locale, use_log=False)
    return DATA_DIR / lang_dst / lang_src


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
    return ENGRISH_DIR / form.replace("+", "-")


def dict_base_name(form: str, date: str, noetym: bool = False) -> str:
    """Return the StarDict folder/file base name for a given form and snapshot date.

    Examples:
        en,        20260301 → en-en-20260301
        enm,       20260301 → enm-en-20260301
        ang+enm+en,20260301 → ang_enm_en-en-20260301
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


# ---------------------------------------------------------------------------
# Cache deletion
# ---------------------------------------------------------------------------

def delete_cache(locales: list[str]) -> None:
    """Delete cached downloads, parse DB, render JSON, and convert output."""
    src = parse_source_dir()

    # Shared: downloads + parse DB
    for pattern in ("pages-*.xml.bz2", "pages-*.xml", "pages-*.sqlite",
                    "pages-*.sqlite-shm", "pages-*.sqlite-wal"):
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


# ---------------------------------------------------------------------------
# wikidict pipeline
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# .df file parser
# ---------------------------------------------------------------------------

def parse_df(path: Path) -> dict[str, tuple[list[str], str]]:
    """Parse a DictFile (.df) into {word: (synonyms, html)}.

    .df format:
        @ headword
        :pronunciation  (optional, ignored)
        & synonym       (0 or more)
        <html>...</html>
        (blank line)
    """
    entries: dict[str, tuple[list[str], str]] = {}
    current_word: str | None = None
    current_syns: list[str] = []
    current_html_lines: list[str] = []

    def flush() -> None:
        if current_word is not None:
            entries[current_word] = (current_syns, "".join(current_html_lines).strip())

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("@ "):
                flush()
                current_word = line[2:].rstrip()
                current_syns = []
                current_html_lines = []
            elif line.startswith("& ") and current_word is not None:
                current_syns.append(line[2:].rstrip())
            elif line.startswith(":") and current_word is not None:
                pass  # pronunciation line — skip
            elif current_word is not None:
                current_html_lines.append(line)

    flush()
    return entries


# ---------------------------------------------------------------------------
# res/ URL rewriting
# ---------------------------------------------------------------------------

class _ResUrlRewriter(HTMLParser):
    """Rebuild HTML, rewriting src/href attributes that start with 'res/' to
    include a locale prefix (e.g. res/foo.jpg → res/en_foo.jpg).

    Subdirectory separators within the res path are flattened to underscores
    (e.g. res/sub/foo.jpg → res/en_sub_foo.jpg) so the merged res/ folder
    stays flat and filenames remain unique across locales.
    """

    def __init__(self, locale: str) -> None:
        super().__init__(convert_charrefs=False)
        self._locale = locale
        self._parts: list[str] = []

    def _rewrite_res(self, value: str) -> str:
        rel = value[4:]  # strip leading "res/"
        flat = rel.replace("/", "_")
        return f"res/{self._locale}_{flat}"

    def _build_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        out = ""
        for name, value in attrs:
            if value is None:
                out += f" {name}"
            else:
                if name in ("src", "href") and value.startswith("res/"):
                    value = self._rewrite_res(value)
                out += f' {name}="{value.replace(chr(34), "&quot;")}"'
        return out

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._parts.append(f"<{tag}{self._build_attrs(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._parts.append(f"<{tag}{self._build_attrs(attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._parts.append(f"<!--{data}-->")

    def result(self) -> str:
        return "".join(self._parts)


def _prefix_res_urls(html: str, locale: str) -> str:
    """Return html with all res/ paths prefixed with the locale code."""
    rewriter = _ResUrlRewriter(locale)
    rewriter.feed(html)
    return rewriter.result()


def _collect_locale_res(locale: str, noetym: bool) -> dict[str, bytes]:
    """Return {prefixed_filename: bytes} for every file inside res/ in the locale's StarDict ZIP.

    Files are keyed as '{locale}_{flat_path}' where subdirectory separators
    are replaced with underscores, matching what _prefix_res_urls produces.
    The source ZIP is never modified.
    """
    zip_path = stardict_zip_path(locale, noetym=noetym)
    result: dict[str, bytes] = {}
    if not zip_path.exists():
        return result
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename
            idx = name.find("res/")
            if idx < 0 or name.endswith("/"):
                continue
            rel = name[idx + 4:]  # path relative to res/
            if not rel:
                continue
            flat = rel.replace("/", "_")
            prefixed = f"{locale}_{flat}"
            with zf.open(member) as fh:
                result[prefixed] = fh.read()
    return result


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_dfs(
    locales: list[str], noetym: bool = False
) -> dict[str, tuple[list[str], str]]:
    """Merge .df files from multiple locales, ordered by DISPLAY_ORDER.

    For words present in multiple locales, wrap each locale's HTML in a
    <h3>Language Period</h3> heading.  For words in only one locale, emit
    the HTML as-is.
    """
    # Collect per-locale entries
    per_locale: dict[str, dict[str, tuple[list[str], str]]] = {}
    active = [loc for loc in DISPLAY_ORDER if loc in locales]

    for locale in active:
        path = df_path(locale, noetym=noetym)
        if not path.exists():
            log.warning("Missing .df file: %s — skipping", path)
            continue
        per_locale[locale] = parse_df(path)

    # Gather all words across all locales (preserve sort order)
    all_words: set[str] = set()
    for entries in per_locale.values():
        all_words.update(entries.keys())

    merged: dict[str, tuple[list[str], str]] = {}
    for word in sorted(all_words):
        present = [(loc, per_locale[loc][word]) for loc in active if word in per_locale.get(loc, {})]
        if len(present) == 1:
            loc, (syns, html) = present[0]
            merged[word] = (syns, _prefix_res_urls(html, loc))
        else:
            # Multiple locales — add language-period headings, no synonyms at top level
            combined = ""
            for loc, (_, html) in present:
                combined += f"<h3>{FORM_NAMES[loc]}</h3>{_prefix_res_urls(html, loc)}"
            merged[word] = ([], combined)

    return merged


def write_merged_df(
    merged: dict[str, tuple[list[str], str]], dest: Path
) -> None:
    """Write a merged DictFile (.df)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for word, (syns, html) in merged.items():
            fh.write(f"@ {word}\n")
            for syn in syns:
                fh.write(f"& {syn}\n")
            fh.write(f"{html}\n\n")


# ---------------------------------------------------------------------------
# StarDict extraction helpers
# ---------------------------------------------------------------------------

_OFT_HEADER = b"StarDict's Cache, Version: 0.2"
_OFT_MAGIC = b"\xc1\xd1\xa4\x51"  # fixed 4-byte magic observed in all StarDict .oft files
_OFT_STRIDE = 32  # record one offset per this many entries


def generate_oft(source_file: Path, bytes_after_null: int) -> None:
    """Generate a StarDict offset-cache (.oft) file for an .idx or .syn file.

    source_file     — the .idx or .syn file to index
    bytes_after_null — fixed bytes per entry after the null terminator:
                       8 for .idx (4-byte offset + 4-byte size, both BE)
                       4 for .syn (4-byte word-index, BE)

    The .oft is written alongside source_file with a .oft suffix appended
    (e.g. dict-data.idx → dict-data.idx.oft).
    """
    data = source_file.read_bytes()
    offsets: list[int] = []
    pos = 0
    count = 0
    while pos < len(data):
        null = data.index(b"\x00", pos)
        if count % _OFT_STRIDE == 0:
            offsets.append(pos)
        pos = null + 1 + bytes_after_null
        count += 1

    offsets.append(len(data))  # sentinel: idx file size, required by KOReader

    oft_path = source_file.with_suffix(source_file.suffix + ".oft")
    with oft_path.open("wb") as fh:
        fh.write(_OFT_HEADER)
        fh.write(_OFT_MAGIC)
        fh.write(struct.pack(f"<{len(offsets)}I", *offsets))
    log.info("Generated %s (%d entries, stride %d)", oft_path.name, len(offsets), _OFT_STRIDE)


def generate_oft_files(folder: Path) -> None:
    """Generate .idx.oft and .syn.oft for all StarDict files in folder."""
    for idx_file in folder.glob("*.idx"):
        generate_oft(idx_file, bytes_after_null=8)
    for syn_file in folder.glob("*.syn"):
        generate_oft(syn_file, bytes_after_null=4)


def patch_ifo(folder: Path, dict_name: str, fields: dict[str, str]) -> None:
    """Update or insert key=value fields in the .ifo file inside folder."""
    ifo_files = list(folder.glob("*.ifo"))
    if not ifo_files:
        log.warning("No .ifo found in %s — skipping patch", folder)
        return
    ifo_path = ifo_files[0]
    lines = ifo_path.read_text(encoding="utf-8").splitlines()
    updated: dict[str, bool] = {k: False for k in fields}
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key and key in fields:
            new_lines.append(f"{key}={fields[key]}")
            updated[key] = True
        else:
            new_lines.append(line)
    # Append any fields that weren't already present
    for key, was_updated in updated.items():
        if not was_updated:
            new_lines.append(f"{key}={fields[key]}")
    ifo_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def rename_stardict_files(folder: Path, dict_name: str) -> None:
    """Rename dict-data.* files in folder to {dict_name}.*"""
    for f in sorted(folder.glob("dict-data*")):
        if f.is_file():
            new_name = dict_name + f.name[len("dict-data"):]
            f.rename(folder / new_name)


def decompress_dict_dz(folder: Path) -> None:
    """For each .dict.dz in folder, write a decompressed .dict alongside it."""
    for dz_file in folder.glob("*.dict.dz"):
        dict_file = dz_file.with_suffix("")  # removes .dz → .dict
        if not dict_file.exists():
            with gzip.open(dz_file, "rb") as src, dict_file.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            log.info("Decompressed %s → %s", dz_file.name, dict_file.name)


def extract_stardict_zip(zip_path: Path, dest_folder: Path, dict_name: str) -> None:
    """Extract a StarDict zip into dest_folder, rename files to dict_name, then post-process."""
    if not zip_path.exists():
        log.warning("StarDict zip not found: %s — skipping", zip_path)
        return
    dest_folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            parts = Path(member.filename).parts
            if not parts or parts[-1] == "":
                continue  # directory entry
            if len(parts) >= 2 and parts[-2] == "res":
                rel = Path("res") / parts[-1]
            else:
                rel = Path(parts[-1])
            target = dest_folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    rename_stardict_files(dest_folder, dict_name)
    decompress_dict_dz(dest_folder)
    generate_oft_files(dest_folder)
    log.info("Extracted %s → %s", zip_path.name, dest_folder)


# ---------------------------------------------------------------------------
# StarDict generation from merged .df via pyglossary
# ---------------------------------------------------------------------------

def convert_df_to_stardict(df_src: Path, out_folder: Path, title: str, date: str, dict_name: str) -> None:
    """Convert a .df file to a StarDict dictionary in out_folder, named dict_name."""
    import gc

    from pyglossary.glossary_v2 import ConvertArgs, Glossary

    out_folder.mkdir(parents=True, exist_ok=True)

    original_gc_collect = gc.collect
    gc.collect = lambda *_: None  # type: ignore[assignment]

    try:
        os.environ["NO_SQLITE"] = "1"
        Glossary.init()
        glos = Glossary()
        glos.config = {"auto_sqlite": False, "cleanup": False}

        writer_cls = glos.plugins["Stardict"].writerClass

        def get_bookname(cls) -> str:  # type: ignore[no-untyped-def]
            return title

        writer_cls.getBookname = get_bookname

        glos.setInfo("title", title)
        glos.setInfo("description", "Generated by engrish.py from Wiktionary data")
        glos.setInfo("date", f"{date[:4]}-{date[4:6]}-{date[6:8]}")
        glos.setInfo("website", "https://kemonine.info")

        glos.convert(
            ConvertArgs(
                inputFilename=str(df_src),
                outputFilename=str(out_folder / "dict-data.ifo"),
                writeOptions={"dictzip": True, "sametypesequence": "h"},
            )
        )
    finally:
        gc.collect = original_gc_collect  # type: ignore[assignment]

    rename_stardict_files(out_folder, dict_name)
    decompress_dict_dz(out_folder)
    generate_oft_files(out_folder)
    log.info("StarDict generated in %s", out_folder)


# ---------------------------------------------------------------------------
# Single-locale output
# ---------------------------------------------------------------------------

def ifo_fields(form: str, date: str, dict_name: str) -> dict[str, str]:
    """Return the .ifo fields to set/override for a given form."""
    return {
        "bookname": f"Engrish: {form}",
        "website": "https://kemonine.info",
        "description": "Generated by engrish.py from Wiktionary data",
        "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        "lang": f"{form}-en",
    }


def copy_single_locale_output(locale: str, form: str, form_dir: Path, date: str) -> None:
    """Extract StarDict ZIPs (etym + noetym) for a single locale into form_dir."""
    for noetym in (False, True):
        name = dict_base_name(form, date, noetym=noetym)
        folder = form_dir / name
        extract_stardict_zip(stardict_zip_path(locale, noetym=noetym), folder, name)
        patch_ifo(folder, name, ifo_fields(form, date, name))


# ---------------------------------------------------------------------------
# Multi-locale merged output
# ---------------------------------------------------------------------------

def build_merged_output(locales: list[str], form: str, form_dir: Path, date: str) -> None:
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

            # Collect res/ files from all source locale ZIPs, renamed with locale prefix
            locale_res: dict[str, bytes] = {}
            for locale in locales:
                locale_res.update(_collect_locale_res(locale, noetym=noetym))
            if locale_res:
                res_dir = out_folder / "res"
                res_dir.mkdir(exist_ok=True)
                for fname, data in locale_res.items():
                    (res_dir / fname).write_bytes(data)
                log.info("Merged %d res/ files into %s/res/", len(locale_res), out_folder.name)
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


# ---------------------------------------------------------------------------
# EPUB generation
# ---------------------------------------------------------------------------

_CHARIS_CSS = """\
@font-face {
  font-family: 'Charis';
  font-weight: 400;
  font-style: normal;
  src: url('fonts/Charis-Regular.woff') format('woff');
}
body {
  font-family: 'Charis', serif;
}
h1 {
  font-weight: normal;
}
"""

_CONTAINER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_OPF_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Engrish Word Examples</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">engrish-test-dict</dc:identifier>
  </metadata>
  <manifest>
    <item id="cover" href="cover.html" media-type="application/xhtml+xml"/>
    <item id="stylesheet" href="styles.css" media-type="text/css"/>
    <item id="font-regular" href="fonts/Charis-Regular.woff" media-type="font/woff"/>
{manifest_items}
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover"/>
{spine_items}
  </spine>
</package>
"""

_NCX_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="engrish-test-dict"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>Engrish Word Examples</text></docTitle>
  <navMap>
    <navPoint id="np-cover" playOrder="0">
      <navLabel><text>Cover</text></navLabel>
      <content src="cover.html"/>
    </navPoint>
{nav_points}
  </navMap>
</ncx>
"""

_COVER_HTML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><title>Engrish Word Examples</title><link rel="stylesheet" type="text/css" href="styles.css"/></head>
<body>
<div style="text-align:center; margin-top:40%;">
  <h1>Engrish Word Examples</h1>
  <p>A sampler of Modern, Middle, and Old English</p>
</div>
</body>
</html>
"""

_CHAPTER_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><title>{title}</title><link rel="stylesheet" type="text/css" href="styles.css"/></head>
<body>
<h1>{title}</h1>
{entries}
</body>
</html>
"""


def _pick_sample_words(
    locale: str, n: int, exclude: set[str], other_locale_words: set[str]
) -> list[tuple[str, str]]:
    """Return up to n (word, html) pairs from locale's .df.

    Excludes words in `exclude` (already used) and `other_locale_words`
    (present in any other active locale's dictionary), so each chapter only
    contains words unique to that period.
    """
    path = df_path(locale, noetym=False)
    if not path.exists():
        log.warning("No .df for %s, skipping epub chapter", locale)
        return []

    entries = parse_df(path)
    candidates = [
        (w, html)
        for w, (_, html) in entries.items()
        if w not in exclude and w not in other_locale_words
    ]
    if len(candidates) <= n:
        return candidates
    return random.sample(candidates, n)


def _build_universal_chapter(locales: list[str]) -> str:
    """Build HTML content for the Universal chapter using only the active locales.

    Raises RuntimeError if any UNIVERSAL_WORDS entry is missing from an active locale.
    """
    active = [loc for loc in DISPLAY_ORDER if loc in locales]

    data: dict[str, dict[str, tuple[list[str], str]]] = {}
    for locale in active:
        path = df_path(locale)
        if not path.exists():
            raise RuntimeError(f"Missing .df file for locale '{locale}': {path}")
        data[locale] = parse_df(path)

    entry_html = ""
    for word in UNIVERSAL_WORDS:
        for locale in active:
            if word not in data[locale]:
                raise RuntimeError(
                    f"Universal word '{word}' not found in {FORM_NAMES[locale]} dictionary."
                )
        entry_html += f"<h2>{word}</h2>\n"
        for locale in active:
            _, html = data[locale][word]
            entry_html += f"<h3>{FORM_NAMES[locale]}</h3>\n{html}\n"

    return entry_html


def generate_epub(locales: list[str], epub_path: Path) -> None:
    """Write engrish_test.epub with cover, Universal chapter, and per-locale samples.

    Per-locale sample words are restricted to words unique to that period
    (not present in any other active locale's dictionary).
    """
    active = [loc for loc in DISPLAY_ORDER if loc in locales]
    epub_path.parent.mkdir(parents=True, exist_ok=True)

    # Build Universal chapter (always present; only shows active periods)
    universal_html = _build_universal_chapter(locales)

    # Pre-load word sets for all active locales to enforce uniqueness per chapter
    all_locale_words: dict[str, set[str]] = {
        loc: set(parse_df(df_path(loc))) for loc in active if df_path(loc).exists()
    }

    # Build per-locale sample chapters with words exclusive to that period
    chapters: list[tuple[str, str, str]] = []
    exclude: set[str] = set(UNIVERSAL_WORDS)

    for locale in active:
        title = FORM_NAMES[locale]
        # Words present in any OTHER active locale
        other_words: set[str] = set().union(*(
            words for loc, words in all_locale_words.items() if loc != locale
        ))
        samples = _pick_sample_words(locale, 6, exclude, other_words)
        exclude.update(w for w, _ in samples)

        entry_html = ""
        for word, html in samples:
            entry_html += f"<h2>{word}</h2>\n{html}\n"

        chapters.append((locale, title, entry_html))

    # Build OPF manifest/spine entries
    manifest_items = '    <item id="ch-universal" href="chapter_universal.html" media-type="application/xhtml+xml"/>\n'
    spine_items = '    <itemref idref="ch-universal"/>\n'
    nav_points = (
        '  <navPoint id="np-universal" playOrder="1">\n'
        '    <navLabel><text>Universal</text></navLabel>\n'
        '    <content src="chapter_universal.html"/>\n'
        '  </navPoint>\n'
    )
    play_order = 2

    for i, (locale, title, _) in enumerate(chapters, start=2):
        fname = f"chapter_{locale}.html"
        manifest_items += f'    <item id="ch{i}" href="{fname}" media-type="application/xhtml+xml"/>\n'
        spine_items += f'    <itemref idref="ch{i}"/>\n'
        nav_points += (
            f'  <navPoint id="np{i}" playOrder="{play_order}">\n'
            f'    <navLabel><text>{title}</text></navLabel>\n'
            f'    <content src="{fname}"/>\n'
            f'  </navPoint>\n'
        )
        play_order += 1

    opf = _OPF_TEMPLATE.format(
        manifest_items=manifest_items.rstrip(),
        spine_items=spine_items.rstrip(),
    )
    ncx = _NCX_TEMPLATE.format(nav_points=nav_points.rstrip())

    font_data = (FONTS_DIR / "Charis-Regular.woff").read_bytes()

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", _CHARIS_CSS, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/fonts/Charis-Regular.woff", font_data, compress_type=zipfile.ZIP_STORED)
        zf.writestr("OEBPS/cover.html", _COVER_HTML, compress_type=zipfile.ZIP_DEFLATED)
        universal_chapter = _CHAPTER_TEMPLATE.format(title="Universal", entries=universal_html)
        zf.writestr("OEBPS/chapter_universal.html", universal_chapter, compress_type=zipfile.ZIP_DEFLATED)
        for locale, title, entry_html in chapters:
            chapter = _CHAPTER_TEMPLATE.format(title=title, entries=entry_html)
            zf.writestr(f"OEBPS/chapter_{locale}.html", chapter, compress_type=zipfile.ZIP_DEFLATED)

    log.info("EPUB written: %s", epub_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--engrish-form",
        required=True,
        choices=list(LOCALES_FOR_FORM) + ["all"],
        metavar="FORM",
        help="Which English form(s) to build: " + ", ".join(list(LOCALES_FOR_FORM) + ["all"]),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Delete all cached downloads and pre-processing data before running",
    )
    args = parser.parse_args()

    if args.engrish_form == "all":
        # Delete cache once upfront (covers all locales), then process every form
        if args.no_cache:
            log.info("Clearing cache for all locales")
            delete_cache(["en", "enm", "ang"])
        for form in LOCALES_FOR_FORM:
            _process_form(form, no_cache=False)
    else:
        _process_form(args.engrish_form, no_cache=args.no_cache)

    return 0


def _process_form(form: str, *, no_cache: bool) -> None:
    """Run the full pipeline and generate output for a single form."""
    locales: list[str] = LOCALES_FOR_FORM[form]
    form_dir = engrish_form_dir(form)

    # 1. Clear cache if requested
    if no_cache:
        log.info("Clearing cache for locales: %s", locales)
        delete_cache(locales)

    # 2. Run wikidict pipeline for each locale
    for locale in locales:
        run_wikidict(locale)

    # 3. Build engrish output
    form_dir.mkdir(parents=True, exist_ok=True)
    # Clear any stale output from previous runs
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

    # 4. Always generate the sampler EPUB alongside the StarDict folders
    epub_path = form_dir / f"test-{dict_base_name(form, date)}.epub"
    log.info("Generating sampler EPUB: %s", epub_path)
    generate_epub(locales, epub_path)

    log.info("Done. Output: %s", form_dir)
    log.info("EPUB:   %s", epub_path)


if __name__ == "__main__":
    sys.exit(main())
