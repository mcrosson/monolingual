"""EPUB sampler generation."""

from __future__ import annotations

import logging
import random
import zipfile
from pathlib import Path

from .config import DISPLAY_ORDER, FONTS_DIR, FORM_NAMES, UNIVERSAL_WORDS
from .merge import parse_df
from .paths import df_path

log = logging.getLogger(__name__)

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
    """Return up to n (word, html) pairs unique to this locale."""
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
    """Build HTML content for the Universal chapter using only the active locales."""
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
    """Write engrish_test.epub with cover, Universal chapter, and per-locale samples."""
    active = [loc for loc in DISPLAY_ORDER if loc in locales]
    epub_path.parent.mkdir(parents=True, exist_ok=True)

    universal_html = _build_universal_chapter(locales)

    all_locale_words: dict[str, set[str]] = {
        loc: set(parse_df(df_path(loc))) for loc in active if df_path(loc).exists()
    }

    chapters: list[tuple[str, str, str]] = []
    exclude: set[str] = set(UNIVERSAL_WORDS)

    for locale in active:
        title = FORM_NAMES[locale]
        other_words: set[str] = set().union(*(
            words for loc, words in all_locale_words.items() if loc != locale
        ))
        samples = _pick_sample_words(locale, 6, exclude, other_words)
        exclude.update(w for w, _ in samples)

        entry_html = ""
        for word, html in samples:
            entry_html += f"<h2>{word}</h2>\n{html}\n"

        chapters.append((locale, title, entry_html))

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

    font_path = FONTS_DIR / "Charis-Regular.woff"
    if not font_path.exists():
        raise FileNotFoundError(
            f"Required font not found: {font_path}\n"
            f"Download Charis SIL from https://software.sil.org/charis/ "
            f"and place the web font at {font_path}"
        )
    font_data = font_path.read_bytes()

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
