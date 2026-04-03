"""EPUB sampler generation."""

from __future__ import annotations

import logging
import random
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .config import DISPLAY_ORDER, FONTS_DIR, FORM_NAMES
from .merge import parse_df
from .paths import df_path, dict_base_name, engrish_form_dir, get_snapshot_date

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_CONTAINER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

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
table {
  border-collapse: collapse;
  margin: 1em 0;
}
th, td {
  border: 1px solid #999;
  padding: 0.3em 0.8em;
  text-align: left;
}
th {
  background-color: #eee;
}
"""


# ---------------------------------------------------------------------------
# Word metadata extraction
# ---------------------------------------------------------------------------

@dataclass
class WordMeta:
    headword: str = ""
    size_kb: float = 0.0
    pos: list[str] = field(default_factory=list)
    synonym_count: int = 0
    pronunciations: list[str] = field(default_factory=list)
    genders: list[str] = field(default_factory=list)
    # locale display name — used in cross-language entries
    name: str = ""


def _extract_meta(word: str, syns: list[str], html: str, locale_name: str = "") -> WordMeta:
    """Extract metadata from a .df entry's HTML without including the definition text."""
    size_kb = len(html.encode("utf-8")) / 1024

    # Parts of speech: look for <b>Noun</b>, <b>Verb</b>, etc. at the start of sections
    pos_matches = re.findall(r"<b>([A-Z][a-z ]+)</b>", html)
    # Deduplicate preserving order
    seen: set[str] = set()
    pos: list[str] = []
    for p in pos_matches:
        if p not in seen:
            seen.add(p)
            pos.append(p)

    # Pronunciations: /.../ or \...\
    pron = re.findall(r"[/\\][^/\\]+[/\\]", html)
    # Deduplicate
    pron = list(dict.fromkeys(pron))

    # Genders: <i>f</i>, <i>m</i>, <i>n</i>, etc.
    gender_matches = re.findall(r"<i>([fmn](?:sing|pl)?)</i>", html)
    genders = list(dict.fromkeys(gender_matches))

    return WordMeta(
        headword=word,
        size_kb=size_kb,
        pos=pos,
        synonym_count=len(syns),
        pronunciations=pron[:4],  # cap at 4 to avoid clutter
        genders=genders,
        name=locale_name,
    )


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

@dataclass
class LocaleStats:
    code: str
    name: str
    entries: int
    synonyms: int


@dataclass
class ChapterInfo:
    id: str
    filename: str
    title: str


def _load_locale_data(locales: list[str]) -> dict[str, dict[str, tuple[list[str], str]]]:
    """Load .df data for all active locales."""
    active = [loc for loc in DISPLAY_ORDER if loc in locales]
    data: dict[str, dict[str, tuple[list[str], str]]] = {}
    for locale in active:
        path = df_path(locale, noetym=False)
        if path.exists():
            data[locale] = parse_df(path)
        else:
            log.warning("No .df for %s, skipping", locale)
    return data


def _locale_stats(locale_data: dict[str, dict[str, tuple[list[str], str]]]) -> list[LocaleStats]:
    """Compute summary stats per locale."""
    result = []
    for code, entries in locale_data.items():
        total_syns = sum(len(syns) for syns, _ in entries.values())
        result.append(LocaleStats(
            code=code,
            name=FORM_NAMES.get(code, code),
            entries=len(entries),
            synonyms=total_syns,
        ))
    return result


def _largest_entries(
    locale_data: dict[str, dict[str, tuple[list[str], str]]], n: int = 3
) -> list[dict]:
    """Return the n largest entries per locale for the stress test chapter."""
    locales = []
    for code, entries in locale_data.items():
        sized = [
            (word, syns, html, len(html.encode("utf-8")))
            for word, (syns, html) in entries.items()
        ]
        sized.sort(key=lambda x: x[3], reverse=True)
        words = [
            _extract_meta(word, syns, html, FORM_NAMES.get(code, code))
            for word, syns, html, _ in sized[:n]
        ]
        locales.append({"name": FORM_NAMES.get(code, code), "words": words})
    return locales


def _cross_language_words(
    locale_data: dict[str, dict[str, tuple[list[str], str]]], n: int = 6
) -> list[dict]:
    """Find words shared across all locales, with per-locale metadata."""
    if len(locale_data) < 2:
        return []

    codes = list(locale_data)
    # Intersect word sets
    shared = set(locale_data[codes[0]])
    for code in codes[1:]:
        shared &= set(locale_data[code])

    # Pick n words, preferring larger combined entries
    candidates = sorted(
        shared,
        key=lambda w: sum(len(locale_data[c][w][1].encode("utf-8")) for c in codes),
        reverse=True,
    )
    selected = candidates[:n]

    # If not enough shared words, fill with words that maximize locale coverage
    if len(selected) < n:
        used = set(selected)
        for code in codes:
            for word in locale_data[code]:
                if word not in used:
                    selected.append(word)
                    used.add(word)
                    if len(selected) >= n:
                        break
            if len(selected) >= n:
                break

    entries = []
    for word in selected:
        locale_entries = []
        for code in codes:
            if word in locale_data[code]:
                syns, html = locale_data[code][word]
                locale_entries.append(
                    _extract_meta(word, syns, html, FORM_NAMES.get(code, code))
                )
        entries.append({"headword": word, "locales": locale_entries})

    return entries


def _spot_check_words(
    entries: dict[str, tuple[list[str], str]],
    locale_name: str,
    n: int = 6,
    exclude: set[str] | None = None,
) -> list[WordMeta]:
    """Pick n random words from a locale's entries for spot-checking."""
    exclude = exclude or set()
    candidates = [(w, syns, html) for w, (syns, html) in entries.items() if w not in exclude]
    if len(candidates) <= n:
        sample = candidates
    else:
        sample = random.sample(candidates, n)
    return [_extract_meta(w, syns, html, locale_name) for w, syns, html in sample]


# ---------------------------------------------------------------------------
# EPUB assembly
# ---------------------------------------------------------------------------

def generate_epub(locales: list[str], epub_path: Path, form: str = "") -> None:
    """Generate the sampler EPUB."""
    if not form:
        form = "+".join(locales)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    page_tpl = env.get_template("page.xhtml.j2")

    locale_data = _load_locale_data(locales)
    if not locale_data:
        raise RuntimeError("No .df data found for any locale")

    stats = _locale_stats(locale_data)
    active_codes = list(locale_data)

    # -- Build chapters --
    chapters: list[ChapterInfo] = []
    chapter_html: dict[str, str] = {}

    # Cover
    ch_id, ch_file = "cover", "cover.html"
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Cover"))
    cover_content = env.get_template("cover.xhtml.j2").render(form=form)
    chapter_html[ch_file] = page_tpl.render(title="Engrish Dictionary Sampler", content=cover_content)

    # Summary
    ch_id, ch_file = "summary", "summary.html"
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Summary"))
    summary_content = env.get_template("summary.xhtml.j2").render(languages=stats)
    chapter_html[ch_file] = page_tpl.render(title="Summary", content=summary_content)

    # Stress test
    ch_id, ch_file = "stress-test", "stress_test.html"
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Test Dictionary Lookups"))
    stress_locales = _largest_entries(locale_data, n=3)
    stress_content = env.get_template("stress_test.xhtml.j2").render(locales=stress_locales)
    chapter_html[ch_file] = page_tpl.render(title="Test Dictionary Lookups", content=stress_content)

    # Cross-language (only for merged forms)
    if len(active_codes) > 1:
        ch_id, ch_file = "cross-language", "cross_language.html"
        chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Cross-Language Words"))
        cross_entries = _cross_language_words(locale_data, n=6)
        cross_content = env.get_template("cross_language.xhtml.j2").render(entries=cross_entries)
        chapter_html[ch_file] = page_tpl.render(title="Cross-Language Words", content=cross_content)

    # Per-locale spot checks
    all_used: set[str] = set()
    # Collect words already used in stress test and cross-language chapters
    for loc_data in stress_locales:
        all_used.update(w.headword for w in loc_data["words"])
    if len(active_codes) > 1:
        all_used.update(e["headword"] for e in cross_entries)

    for code in active_codes:
        locale_name = FORM_NAMES.get(code, code)
        ch_id = f"spot-{code}"
        ch_file = f"spot_{code}.html"
        chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title=locale_name))
        words = _spot_check_words(locale_data[code], locale_name, n=6, exclude=all_used)
        all_used.update(w.headword for w in words)
        spot_content = env.get_template("spot_check.xhtml.j2").render(
            locale_name=locale_name, words=words
        )
        chapter_html[ch_file] = page_tpl.render(title=locale_name, content=spot_content)

    # -- Render OPF and NCX --
    opf = env.get_template("content.opf.j2").render(form=form, chapters=chapters)
    ncx = env.get_template("toc.ncx.j2").render(form=form, chapters=chapters)

    # -- Font --
    font_path = FONTS_DIR / "Charis-Regular.woff"
    if not font_path.exists():
        raise FileNotFoundError(
            f"Required font not found: {font_path}\n"
            f"Download Charis SIL from https://software.sil.org/charis/ "
            f"and place the web font at {font_path}"
        )
    font_data = font_path.read_bytes()

    # -- Write EPUB --
    epub_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", _CHARIS_CSS, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/fonts/Charis-Regular.woff", font_data, compress_type=zipfile.ZIP_STORED)
        for filename, html in chapter_html.items():
            zf.writestr(f"OEBPS/{filename}", html, compress_type=zipfile.ZIP_DEFLATED)

    log.info("EPUB written: %s", epub_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _discover_all_dicts() -> list[str]:
    """Return form names for all existing dictionary directories in the engrish output."""
    from .config import ENGRISH_DIR

    if not ENGRISH_DIR.exists():
        return []
    return sorted(
        d.name.replace("-", "+")
        for d in ENGRISH_DIR.iterdir()
        if d.is_dir() and any(d.iterdir())
    )


def run(dicts: list[str] | None, *, all_dicts: bool = False) -> int:
    """Validate dictionaries exist, then generate EPUBs for each."""
    if all_dicts:
        dicts = _discover_all_dicts()
        if not dicts:
            print("Error: no existing dictionaries found in engrish output directory", file=sys.stderr)
            return 1
        log.info("Discovered dictionaries: %s", ", ".join(dicts))

    errors: list[str] = []
    for form in dicts:
        form_dir = engrish_form_dir(form)
        if not form_dir.exists() or not any(form_dir.iterdir()):
            errors.append(f"Dictionary '{form}' not found at {form_dir}")

    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    for form in dicts:
        locales = [c.strip() for c in form.split("+") if c.strip()]
        date = get_snapshot_date(locales)

        epub_path = engrish_form_dir(form) / f"test-{dict_base_name(form, date)}.epub"
        log.info("Generating sampler EPUB: %s", epub_path)
        try:
            generate_epub(locales, epub_path, form=form)
            log.info("EPUB: %s", epub_path)
        except FileNotFoundError as exc:
            log.error("EPUB generation failed for '%s': %s", form, exc)

    return 0
