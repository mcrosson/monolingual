"""EPUB sampler generation."""

from __future__ import annotations

import logging
import random
import re
import sqlite3
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .config import EPUB_BASE_FONTS, FONTS_DIR, FORM_NAMES, _ENGRISH_CFG
from .merge import normalize_res_filename, parse_df
from .paths import df_path, dict_base_name, engrish_form_dir, get_snapshot_date, get_sqlite_path

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

# Build font index from whatever font files exist in FONTS_DIR.
# Stem is extracted from filename: "NotoSansArabic[wght].ttf" -> "NotoSansArabic"
_FONT_INDEX: dict[str, Path] = {}
for _f in sorted(FONTS_DIR.glob("*")):
    if _f.suffix in (".ttf", ".otf"):
        _stem = _f.stem.split("[")[0].split("-")[0]
        _FONT_INDEX[_stem] = _f


def _fonts_for_locales(locales: list[str]) -> list[tuple[str, str]]:
    """Return (font_stem, filename) pairs needed for the given locales.

    Reads the 'fonts' list from each locale's engrish.json entry, unions them,
    deduplicates, and sorts by file size (smallest first so common scripts
    appear first in the CSS font-family cascade).

    Raises if any locale is missing the 'fonts' field or references a font
    file not present in the fonts directory.
    """
    needed: set[str] = set(EPUB_BASE_FONTS)
    for locale in locales:
        if locale not in _ENGRISH_CFG:
            raise ValueError(f"Locale '{locale}' not found in engrish.json")
        cfg = _ENGRISH_CFG[locale]
        if "fonts" not in cfg:
            raise ValueError(
                f"Locale '{locale}' is missing the 'fonts' field in engrish.json"
            )
        needed.update(cfg["fonts"])

    missing = needed - set(_FONT_INDEX)
    if missing:
        raise FileNotFoundError(
            f"Font files not found in {FONTS_DIR}: {sorted(missing)}"
        )

    return [
        (stem, _FONT_INDEX[stem].name)
        for stem in sorted(needed, key=lambda s: _FONT_INDEX[s].stat().st_size)
    ]


def _build_css(fonts: list[tuple[str, str]]) -> str:
    """Build CSS with @font-face declarations and font-family cascade."""
    parts = []
    for stem, filename in fonts:
        media_type = "font/otf" if filename.endswith(".otf") else "truetype"
        fmt = "opentype" if filename.endswith(".otf") else "truetype"
        parts.append(
            f"@font-face {{\n"
            f"  font-family: '{stem}';\n"
            f"  src: url('fonts/{filename}') format('{fmt}');\n"
            f"}}"
        )

    family_list = ", ".join(f"'{stem}'" for stem, _ in fonts) + ", serif"
    parts.append(
        f"body {{\n"
        f"  font-family: {family_list};\n"
        f"}}\n"
        f"h1 {{\n"
        f"  text-decoration: underline;\n"
        f"}}\n"
        f"h3 {{\n"
        f"  font-weight: normal;\n"
        f"}}\n"
        f"table {{\n"
        f"  margin: 1em 0;\n"
        f"}}\n"
        f"th, td {{\n"
        f"  padding: 0.3em 0.8em;\n"
        f"  text-align: left;\n"
        f"  border-bottom: 1px solid #999;\n"
        f"}}\n"
        f"th {{\n"
        f"  background-color: #eee;\n"
        f"}}"
    )
    return "\n".join(parts) + "\n"


def _locale_label(code: str) -> str:
    """Return 'Display Name (code)' for a locale."""
    return f"{FORM_NAMES.get(code, code)} ({code})"


# ---------------------------------------------------------------------------
# Word metadata extraction
# ---------------------------------------------------------------------------

@dataclass
class WordMeta:
    headword: str = ""
    size_kb: float = 0.0
    synonym_count: int = 0
    pronunciations: list[str] = field(default_factory=list)
    # locale display name — used in cross-language entries
    name: str = ""


def _extract_meta(word: str, syns: list[str], html: str, locale_name: str = "") -> WordMeta:
    """Extract metadata from a .df entry's HTML without including the definition text."""
    size_kb = len(html.encode("utf-8")) / 1024

    # Pronunciations: /.../ or \...\  — must contain at least one IPA character
    # to avoid matching HTML tag fragments like </b></
    pron = re.findall(r"[/\\]([^/\\<>]{1,50})[/\\]", html)
    pron = [p for p in pron if re.search(r"[\u0250-\u02FF\u0300-\u036F\u00C0-\u024F]", p)]
    pron = [f"/{p}/" for p in dict.fromkeys(pron)]


    return WordMeta(
        headword=word,
        size_kb=size_kb,
        synonym_count=len(syns),
        pronunciations=pron[:4],  # cap at 4 to avoid clutter
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
class SubChapterInfo:
    id: str
    filename: str  # parent filename + #anchor
    title: str


@dataclass
class ChapterInfo:
    id: str
    filename: str
    title: str
    children: list[SubChapterInfo] = field(default_factory=list)
    nav_target: str = ""  # NCX content src; defaults to filename if empty

    def __post_init__(self) -> None:
        if not self.nav_target:
            self.nav_target = self.filename


def _load_locale_data(locales: list[str]) -> dict[str, dict[str, tuple[list[str], str]]]:
    """Load .df data for all active locales."""
    active = list(locales)
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
            _extract_meta(word, syns, html, _locale_label(code))
            for word, syns, html, _ in sized[:n]
        ]
        locales.append({"name": _locale_label(code), "code": code, "words": words})
    return locales


def _has_alternates(word: str, entries_by_locale: dict[str, tuple[list[str], str]]) -> bool:
    """True if every locale's entry for this word has alternates."""
    return all(len(syns) > 0 for syns, _ in entries_by_locale.values())


def _has_pronunciation(word: str, entries_by_locale: dict[str, tuple[list[str], str]]) -> bool:
    """True if every locale's entry for this word has IPA pronunciation."""
    for syns, html in entries_by_locale.values():
        pron = re.findall(r"[/\\]([^/\\<>]{1,50})[/\\]", html)
        if not any(re.search(r"[\u0250-\u02FF\u0300-\u036F\u00C0-\u024F]", p) for p in pron):
            return False
    return True


def _pick_constrained(
    candidates: list[str],
    entries_fn: callable,
    n: int,
    exclude: set[str],
) -> list[str]:
    """Pick n words using the constrained selection strategy.

    Selection order (unfillable slots roll into random):
    1. 2 words with alternates AND pronunciation
    2. 1 word with alternates, NO pronunciation
    3. 1 word with NO alternates, WITH pronunciation
    4. 2 words fully random

    entries_fn(word) returns a dict of {locale: (syns, html)} for that word.
    """
    available = [w for w in candidates if w not in exclude]
    random.shuffle(available)

    selected: list[str] = []
    remaining = list(available)

    slots = [
        (2, lambda w: _has_alternates(w, entries_fn(w)) and _has_pronunciation(w, entries_fn(w))),
        (1, lambda w: _has_alternates(w, entries_fn(w)) and not _has_pronunciation(w, entries_fn(w))),
        (1, lambda w: not _has_alternates(w, entries_fn(w)) and _has_pronunciation(w, entries_fn(w))),
    ]

    random_count = 2  # base random slots

    for count, predicate in slots:
        found = 0
        still_remaining = []
        for w in remaining:
            if found < count and predicate(w):
                selected.append(w)
                found += 1
            else:
                still_remaining.append(w)
        remaining = still_remaining
        random_count += count - found  # unfilled slots roll into random

    # Fill random slots
    for w in remaining:
        if len(selected) >= n:
            break
        selected.append(w)

    return selected[:n]


def _cross_language_words(
    locale_data: dict[str, dict[str, tuple[list[str], str]]],
    exclude: set[str],
    n: int = 6,
) -> list[dict]:
    """Find words shared across all locales, with per-locale metadata."""
    if len(locale_data) < 2:
        return []

    codes = list(locale_data)
    shared = set(locale_data[codes[0]])
    for code in codes[1:]:
        shared &= set(locale_data[code])

    def entries_fn(word: str) -> dict[str, tuple[list[str], str]]:
        return {c: locale_data[c][word] for c in codes}

    selected = _pick_constrained(sorted(shared), entries_fn, n, exclude)

    # If not enough shared words, fill with words that maximize locale coverage
    if len(selected) < n:
        used = set(selected) | exclude
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
                    _extract_meta(word, syns, html, _locale_label(code))
                )
        entries.append({"headword": word, "locales": locale_entries})

    return entries


def _spot_check_words(
    entries: dict[str, tuple[list[str], str]],
    locale_name: str,
    exclude: set[str],
    n: int = 6,
) -> list[WordMeta]:
    """Pick n words from a locale's entries using constrained selection."""
    def entries_fn(word: str) -> dict[str, tuple[list[str], str]]:
        return {locale_name: entries[word]}

    selected = _pick_constrained(sorted(entries), entries_fn, n, exclude)
    return [_extract_meta(w, entries[w][0], entries[w][1], locale_name) for w in selected]


def _find_missing_words(
    locale_data: dict[str, dict[str, tuple[list[str], str]]],
) -> list[dict]:
    """Find words in the SQLite dump that didn't make it into the .df output."""
    from wikidict import lang, utils

    db_path = get_sqlite_path()
    con = sqlite3.connect(str(db_path))
    results = []

    try:
        cur = con.cursor()
        for code in locale_data:
            _, lang_dst = utils.guess_locales(code, use_log=False)
            head_sections = tuple(
                hs.replace(" ", "") for hs in lang.head_sections[lang_dst]
            )

            # Get all words from the dump that have a section for this language
            cur.execute("SELECT title, body FROM pages WHERE namespace_id = 0")
            dump_words: set[str] = set()
            for title, body in cur:
                if body is None:
                    continue
                body_lower = body.lower().replace(" ", "")
                if any(f"=={hs}==" in body_lower for hs in head_sections):
                    dump_words.add(title)

            df_words = set(locale_data[code].keys())
            missing = sorted(dump_words - df_words)

            # Format into rows of 5 comma-separated words
            word_rows = []
            for i in range(0, len(missing), 5):
                word_rows.append(", ".join(missing[i:i + 5]))

            results.append({
                "name": _locale_label(code),
                "code": code,
                "missing_count": len(missing),
                "word_rows": word_rows,
            })
    finally:
        con.close()

    return results


# ---------------------------------------------------------------------------
# Source data cleanup examples
# ---------------------------------------------------------------------------

_CLEANUP_CATEGORIES = [
    ("anchor", "Anchor/Fragment",
     "Wiktionary section anchors (#) and alternate form separators (//) stripped."),
    ("zwnj", "Zero-Width Non-Joiner",
     "Persian/Arabic ZWNJ characters removed or replaced with spaces."),
    ("nfkc", "NFKC Normalization",
     "Fullwidth characters, Arabic presentation forms, CJK radicals, and compatibility characters normalized to standard Unicode forms."),
    ("combining", "Combining Mark",
     "Diacritical combining marks stripped to match base headword."),
    ("case", "Case Normalization",
     "Capitalization differences resolved (lowercase, capitalize, decapitalize)."),
    ("bidi", "Bidirectional Match",
     "Both target and headword stripped to a common base form to find a match (e.g. Greek accent direction, Russian \u0451/\u0435)."),
    ("punct", "Punctuation",
     "Interpuncts, dots, dashes, smart quotes, apostrophe variants, and tatweel normalized."),
    ("spacing", "Spacing",
     "Hyphens and spaces interchanged or removed to find a match."),
    ("article", "Article",
     "Leading articles (the/a/an) stripped or added."),
    ("reflexive", "Reflexive",
     "Romance se/s\u2019 and German sich prefixes stripped."),
    ("suru", "Japanese \u3059\u308B",
     "Japanese verb \u3059\u308B suffix stripped to match the noun/stem headword."),
    ("comma", "Comma Split",
     "Comma-separated targets split into individual headwords."),
    ("images", "Inline Images",
     "Resource file references normalized to locale-prefixed flat filenames. Look up these headwords to verify images render correctly."),
    ("dangling", "Dropped (Unresolvable)",
     "Targets with no matching headword in the source data, removed during cleanup."),
]

_MAX_EXAMPLES_PER_CATEGORY = 3


def _find_cleanup_examples(
    locale_data: dict[str, dict[str, tuple[list[str], str]]],
) -> list[dict]:
    """Find examples of each normalization category from source data.

    Returns only categories that have at least one example.
    """
    import json

    from .paths import render_source_dir
    from .pipeline import (
        _build_base_form_map,
        _try_article_normalize,
        _try_bidi_normalize,
        _try_case_normalize,
        _try_comma_split,
        _try_nfkc,
        _try_normalize_zwnj,
        _try_punct_normalize,
        _try_reflexive_normalize,
        _try_spacing_normalize,
        _try_strip_anchor,
        _try_strip_combining,
        _try_suru_normalize,
    )

    # Per-category: count + examples
    counts: dict[str, int] = {key: 0 for key, _, _ in _CLEANUP_CATEGORIES}
    examples: dict[str, list[dict]] = {key: [] for key, _, _ in _CLEANUP_CATEGORIES}
    # Track which locales already have an example per category (prefer diversity)
    locale_seen: dict[str, set[str]] = {key: set() for key, _, _ in _CLEANUP_CATEGORIES}

    for code in locale_data:
        locale_name = _locale_label(code)
        render_dir = render_source_dir(code)
        jsons = sorted(render_dir.glob("data-*.json"))
        if not jsons:
            continue

        source = json.loads(jsons[-1].read_text("utf-8"))
        headwords = set(source.keys())
        base_map = _build_base_form_map(headwords)

        for hw, entry in source.items():
            variants = entry.get("variants")
            if not variants:
                continue
            for target in variants:
                if target in headwords:
                    continue

                # Test each normalizer individually, in order.
                # First match determines the category.
                categorized = False
                normalizers = [
                    ("anchor", lambda t: _try_strip_anchor(t, headwords)),
                    ("zwnj", lambda t: _try_normalize_zwnj(t, headwords)),
                    ("nfkc", lambda t: _try_nfkc(t, headwords)),
                    ("combining", lambda t: _try_strip_combining(t, headwords)),
                    ("case", lambda t: _try_case_normalize(t, headwords)),
                    ("bidi", lambda t: _try_bidi_normalize(t, headwords, base_map)),
                    ("punct", lambda t: (_try_punct_normalize(t, headwords) or [None])[0]),
                    ("spacing", lambda t: (_try_spacing_normalize(t, headwords) or [None])[0]),
                    ("article", lambda t: _try_article_normalize(t, headwords)),
                    ("reflexive", lambda t: _try_reflexive_normalize(t, headwords)),
                    ("suru", lambda t: _try_suru_normalize(t, headwords)),
                    ("comma", lambda t: (_try_comma_split(t, headwords) or [None])[0]),
                ]

                for cat_key, normalizer in normalizers:
                    resolved = normalizer(target)
                    if resolved:
                        counts[cat_key] += 1
                        exs = examples[cat_key]
                        if (
                            len(exs) < _MAX_EXAMPLES_PER_CATEGORY
                            and code not in locale_seen[cat_key]
                        ):
                            exs.append({
                                "locale": locale_name,
                                "original": target,
                                "normalized": resolved,
                                "headword": hw,
                            })
                            locale_seen[cat_key].add(code)
                        categorized = True
                        break

                if not categorized:
                    # Dangling — no normalizer fixed it
                    counts["dangling"] += 1
                    exs = examples["dangling"]
                    if (
                        len(exs) < _MAX_EXAMPLES_PER_CATEGORY
                        and code not in locale_seen["dangling"]
                    ):
                        exs.append({
                            "locale": locale_name,
                            "original": target,
                            "normalized": "\u2014",  # em-dash for "none"
                            "headword": hw,
                        })
                        locale_seen["dangling"].add(code)

    # Image scanning — find entries with res/ file references in definition HTML
    for code in locale_data:
        locale_name = _locale_label(code)
        for hw, (_, html) in locale_data[code].items():
            for m in re.finditer(r'src="(res/([^"]+))"', html):
                raw_path = m.group(1)
                rel = m.group(2)
                counts["images"] += 1
                exs = examples["images"]
                if (
                    len(exs) < _MAX_EXAMPLES_PER_CATEGORY
                    and code not in locale_seen["images"]
                ):
                    exs.append({
                        "locale": locale_name,
                        "original": raw_path,
                        "normalized": f"res/{normalize_res_filename(rel, code)}",
                        "headword": hw,
                    })
                    locale_seen["images"].add(code)

    # Build result — only categories with examples
    result = []
    for key, name, description in _CLEANUP_CATEGORIES:
        if not examples[key]:
            continue
        result.append({
            "name": name,
            "anchor": f"cleanup-{key}",
            "description": description,
            "count": counts[key],
            "examples": examples[key],
        })

    return result


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
    date = get_snapshot_date(locales)
    snapshot_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    # -- Build chapters --
    chapters: list[ChapterInfo] = []
    chapter_html: dict[str, str] = {}

    # Cover
    ch_id, ch_file = "cover", "cover.html"
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Cover"))
    display_names = " + ".join(FORM_NAMES.get(c, c) for c in active_codes)
    cover_content = env.get_template("cover.xhtml.j2").render(form=form, display_names=display_names)
    chapter_html[ch_file] = page_tpl.render(title="Engrish Dictionary Sampler", content=cover_content)

    # Summary
    ch_id, ch_file = "summary", "summary.html"
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Summary"))
    summary_content = env.get_template("summary.xhtml.j2").render(languages=stats, snapshot_date=snapshot_date)
    chapter_html[ch_file] = page_tpl.render(title="Summary", content=summary_content)

    # Stress test
    ch_id, ch_file = "stress-test", "stress_test.html"
    stress_locales = _largest_entries(locale_data, n=3)
    for loc in stress_locales:
        loc["anchor"] = f"test-{loc['code']}"
    stress_children = [
        SubChapterInfo(id=f"test-{loc['code']}", filename=f"{ch_file}#test-{loc['code']}", title=loc["name"])
        for loc in stress_locales
    ]
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Test", children=stress_children))
    stress_content = env.get_template("stress_test.xhtml.j2").render(locales=stress_locales)
    chapter_html[ch_file] = page_tpl.render(title="Test", content=stress_content)

    # Global exclusion set — no word appears in more than one chapter
    all_used: set[str] = set()
    for loc_data in stress_locales:
        all_used.update(w.headword for w in loc_data["words"])

    # Cross-language (only for merged forms)
    if len(active_codes) > 1:
        ch_id, ch_file = "cross-language", "cross_language.html"
        chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Shared"))
        cross_entries = _cross_language_words(locale_data, all_used, n=6)
        all_used.update(e["headword"] for e in cross_entries)
        cross_content = env.get_template("cross_language.xhtml.j2").render(entries=cross_entries)
        chapter_html[ch_file] = page_tpl.render(title="Shared", content=cross_content)

    # Per-locale spot checks
    for code in active_codes:
        locale_name = _locale_label(code)
        ch_id = f"spot-{code}"
        ch_file = f"spot_{code}.html"
        chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title=locale_name))
        words = _spot_check_words(locale_data[code], locale_name, all_used, n=6)
        all_used.update(w.headword for w in words)
        spot_content = env.get_template("spot_check.xhtml.j2").render(
            locale_name=locale_name, words=words
        )
        chapter_html[ch_file] = page_tpl.render(title=locale_name, content=spot_content)

    # Source Data Cleanup
    ch_id, ch_file = "source-cleanup", "source_cleanup.html"
    cleanup_categories = _find_cleanup_examples(locale_data)
    cleanup_children = [
        SubChapterInfo(id="cleanup-overview", filename=f"{ch_file}#cleanup-overview", title="Overview"),
    ] + [
        SubChapterInfo(id=cat["anchor"], filename=f"{ch_file}#{cat['anchor']}", title=cat["name"])
        for cat in cleanup_categories
    ]
    chapters.append(ChapterInfo(
        id=ch_id, filename=ch_file, title="Source Data Cleanup",
        children=cleanup_children, nav_target=f"{ch_file}#top",
    ))
    cleanup_content = env.get_template("source_cleanup.xhtml.j2").render(categories=cleanup_categories)
    chapter_html[ch_file] = page_tpl.render(title="Source Data Cleanup", content=cleanup_content)

    # Missing words
    ch_id, ch_file = "missing", "missing.html"
    missing_locales = _find_missing_words(locale_data)
    for loc in missing_locales:
        loc["anchor"] = f"missing-{loc['code']}"
    missing_children = [
        SubChapterInfo(id="missing-overview", filename=f"{ch_file}#missing-overview", title="Overview"),
    ] + [
        SubChapterInfo(id=f"missing-{loc['code']}", filename=f"{ch_file}#missing-{loc['code']}", title=loc["name"])
        for loc in missing_locales
    ]
    chapters.append(ChapterInfo(id=ch_id, filename=ch_file, title="Missing", children=missing_children, nav_target=f"{ch_file}#top"))
    missing_content = env.get_template("missing.xhtml.j2").render(locales=missing_locales)
    chapter_html[ch_file] = page_tpl.render(title="Missing", content=missing_content)

    # -- Fonts --
    fonts = _fonts_for_locales(locales)
    css = _build_css(fonts)

    font_data: dict[str, bytes] = {}
    for stem, filename in fonts:
        fp = FONTS_DIR / filename
        if not fp.exists():
            raise FileNotFoundError(
                f"Required font not found: {fp}\n"
                f"Download Noto Sans fonts and place them in {FONTS_DIR}"
            )
        font_data[filename] = fp.read_bytes()

    # -- Render OPF and NCX --
    opf = env.get_template("content.opf.j2").render(form=form, chapters=chapters, fonts=fonts)
    ncx = env.get_template("toc.ncx.j2").render(form=form, chapters=chapters)

    # -- Write EPUB --
    epub_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", css, compress_type=zipfile.ZIP_DEFLATED)
        for filename, data in font_data.items():
            zf.writestr(f"OEBPS/fonts/{filename}", data, compress_type=zipfile.ZIP_STORED)
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
