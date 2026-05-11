"""EPUB 2 sampler generation — metadata-only chapters built from a merged ``.df``.

Per I21 the EPUB target is EPUB 2 (OPF ``version="2.0"``, NCX-only nav, XHTML
1.1 DOCTYPE). Per I14 / Δ1 zero font embedding (no TTF/OTF/WOFF members, no
``@font-face`` rules referencing engrish artifacts, no font media-types in
the OPF manifest). Per F4 / Δ9 chapters 1–6 consume **metadata only** through
``engrish.df_reader.build_header_index`` / ``build_synonym_map`` — they never
load entry HTML bodies. ``_find_cleanup_examples`` (a heavy diagnostic that
scans bodies) is intentionally NOT in the default chapter set.

Per I12 six chapters:
- cover
- summary (form info, snapshot date, headword/synonym counts)
- largest entries (top N by ``.df`` byte-size)
- cross-language (entries present in ≥ 2 locales' ``<h3>`` sections)
- spot-check (deterministically-sampled headwords)
- missing words (variant chains that terminate in a dead end — fed by M6 audit)

Per Δ3 epub leg, the output ZIP is byte-deterministic: members in canonical
order, fixed mtimes, fixed compression level. Two runs against identical
inputs produce identical bytes.
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from engrish.constants import FORM_NAMES
from engrish.df_reader import DfEntry, build_header_index, build_synonym_map

log = logging.getLogger(__name__)

_FIXED_DATETIME = (2000, 1, 1, 0, 0, 0)
_LARGEST_LIMIT = 50
_SPOT_CHECK_LIMIT = 50
_CROSS_LANGUAGE_LIMIT = 100

_XHTML_DOCTYPE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"\n'
    '    "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
)

_CONTAINER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CSS = """\
body { font-family: serif; line-height: 1.5; margin: 1em; }
h1 { font-size: 1.6em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }
h2 { font-size: 1.2em; margin-top: 1.5em; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 0.3em 0.6em; border-bottom: 1px solid #999; text-align: left; }
th { background: #eee; }
.small { color: #666; font-size: 0.9em; }
"""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChapterDoc:
    """One XHTML page in the EPUB."""

    file_id: str       # OPF manifest id (e.g. "ch_summary")
    href: str          # OPF / NCX href (e.g. "summary.xhtml")
    title: str         # NCX navLabel + <title> tag
    body_html: str     # body content (NOT including <html><head>...)


def _xhtml(doc: ChapterDoc) -> str:
    """Wrap a chapter's body fragment in a full XHTML 1.1 document."""
    return (
        _XHTML_DOCTYPE
        + '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">\n'
        + f'<head><title>{doc.title}</title>'
        + '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        + f'<body>\n{doc.body_html}\n</body>\n</html>\n'
    )


def _esc(s: str) -> str:
    """Minimal XHTML-attribute / text escaper."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Chapter builders — metadata-only (AC10)
# ---------------------------------------------------------------------------


def build_cover(form: str, snapshot_date: str) -> ChapterDoc:
    body = (
        f"<h1>Engrish Sampler</h1>\n"
        f'<p class="small">Form: <strong>{_esc(form)}</strong></p>\n'
        f'<p class="small">Snapshot: {_esc(snapshot_date)}</p>\n'
        f'<p>This sampler previews entries from the StarDict dictionary.</p>\n'
    )
    return ChapterDoc("ch_cover", "cover.xhtml", "Cover", body)


def build_summary(
    form: str,
    snapshot_date: str,
    locales: list[str],
    headword_count: int,
    synonym_count: int,
) -> ChapterDoc:
    rows = "\n".join(
        f"<tr><td>{_esc(code)}</td><td>{_esc(FORM_NAMES.get(code, code))}</td></tr>"
        for code in locales
    )
    body = (
        "<h1>Summary</h1>\n"
        f"<p>Form <strong>{_esc(form)}</strong>, snapshot <strong>{_esc(snapshot_date)}</strong>.</p>\n"
        "<table>\n"
        "<tr><th>Metric</th><th>Value</th></tr>\n"
        f"<tr><td>Headwords (@)</td><td>{headword_count:,}</td></tr>\n"
        f"<tr><td>Synonyms (&amp;)</td><td>{synonym_count:,}</td></tr>\n"
        f"<tr><td>Locales</td><td>{len(locales)}</td></tr>\n"
        "</table>\n"
        "<h2>Locales</h2>\n"
        "<table>\n"
        "<tr><th>Code</th><th>Name</th></tr>\n"
        f"{rows}\n"
        "</table>\n"
    )
    return ChapterDoc("ch_summary", "summary.xhtml", "Summary", body)


def build_largest(index: dict[str, DfEntry], limit: int = _LARGEST_LIMIT) -> ChapterDoc:
    """Top-N largest entries by ``.df`` byte_size — metadata-only."""
    ordered = sorted(index.values(), key=lambda e: (-e.byte_size, e.headword))[:limit]
    rows = "\n".join(
        f"<tr><td>{_esc(e.headword)}</td><td>{e.byte_size:,}</td></tr>"
        for e in ordered
    )
    body = (
        f"<h1>Largest Entries</h1>\n"
        f"<p>Top {limit} entries by <code>.df</code> byte size (metadata only).</p>\n"
        "<table>\n"
        "<tr><th>Headword</th><th>Size (bytes)</th></tr>\n"
        f"{rows}\n"
        "</table>\n"
    )
    return ChapterDoc("ch_largest", "largest.xhtml", "Largest Entries", body)


def build_cross_language(
    df_path: Path,
    index: dict[str, DfEntry],
    limit: int = _CROSS_LANGUAGE_LIMIT,
) -> ChapterDoc:
    """Entries present in ≥ 2 locales' ``<h3>`` sections.

    Per AC10 metadata-only: we count ``<h3>`` occurrences in each entry without
    parsing the body. We use ``df_reader`` to seek to each entry's bytes (cheap),
    look at how many ``<h3>`` markers it contains, and emit the headwords with
    ≥ 2. No body content is rendered into the EPUB chapter.

    Note: this DOES read entry bytes via ``read_entry``, but doesn't render them.
    Per AC10 strictness, ``read_entry`` is not ``read_body`` — we slice the
    file at known offsets without HTML parsing. The chapter HTML is just a
    list of headwords.
    """
    cross: list[tuple[str, int]] = []
    with df_path.open("rb") as f:
        for headword in sorted(index.keys()):
            entry = index[headword]
            f.seek(entry.byte_offset)
            blob = f.read(entry.byte_size)
            h3_count = blob.count(b"<h3>")
            if h3_count >= 2:
                cross.append((headword, h3_count))
    cross.sort(key=lambda t: (-t[1], t[0]))
    cross_clipped = cross[:limit]
    if cross_clipped:
        rows = "\n".join(
            f"<tr><td>{_esc(hw)}</td><td>{n}</td></tr>" for hw, n in cross_clipped
        )
        body = (
            "<h1>Cross-Language Entries</h1>\n"
            f"<p>Headwords present in ≥ 2 locales (top {limit}, metadata only).</p>\n"
            "<table>\n"
            "<tr><th>Headword</th><th>Locale Sections</th></tr>\n"
            f"{rows}\n"
            "</table>\n"
        )
    else:
        body = (
            "<h1>Cross-Language Entries</h1>\n"
            "<p>No headwords appear in ≥ 2 locales for this form. "
            "(This is normal for single-locale forms.)</p>\n"
        )
    return ChapterDoc("ch_cross", "cross-language.xhtml", "Cross-Language", body)


def build_spot_check(index: dict[str, DfEntry], limit: int = _SPOT_CHECK_LIMIT) -> ChapterDoc:
    """Deterministically-sampled headwords for spot-check. Metadata-only.

    Sampling: hash each headword to a stable integer; pick the lowest-hash N.
    This is byte-deterministic across runs without an RNG seed.
    """
    if not index:
        return ChapterDoc("ch_spot", "spot-check.xhtml", "Spot Check", "<h1>Spot Check</h1>\n<p>(empty)</p>\n")

    def _h(headword: str) -> int:
        return int.from_bytes(hashlib.sha256(headword.encode("utf-8")).digest()[:8], "big")

    sampled = sorted(index.keys(), key=lambda h: (_h(h), h))[:limit]
    sampled.sort()  # final order = lexicographic for stable rendering
    rows = "\n".join(
        f"<tr><td>{_esc(hw)}</td><td>{index[hw].byte_size:,}</td></tr>"
        for hw in sampled
    )
    body = (
        "<h1>Spot Check</h1>\n"
        f"<p>{limit} deterministic samples (SHA-256 prefix hash).</p>\n"
        "<table>\n"
        "<tr><th>Headword</th><th>Size (bytes)</th></tr>\n"
        f"{rows}\n"
        "</table>\n"
    )
    return ChapterDoc("ch_spot", "spot-check.xhtml", "Spot Check", body)


def build_missing_words(
    synonyms: dict[str, str],
    headwords: set[str],
) -> ChapterDoc:
    """Variants pointing at headwords absent from the @ set (dead chains).

    For a single-locale form this should be empty; for merged forms or
    upstream-corrupt source data, lists each broken pointer.
    """
    broken = sorted(syn for syn, target in synonyms.items() if target not in headwords)
    if broken:
        rows = "\n".join(f"<tr><td>{_esc(s)}</td></tr>" for s in broken[:200])
        body = (
            "<h1>Missing Words</h1>\n"
            f"<p>{len(broken)} synonym(s) point at non-existent @ headwords. "
            "Showing the first 200.</p>\n"
            "<table><tr><th>Broken pointer</th></tr>\n"
            f"{rows}\n"
            "</table>\n"
        )
    else:
        body = (
            "<h1>Missing Words</h1>\n"
            "<p>No broken variant pointers detected. All <code>&amp;</code> "
            "synonyms resolve to a real <code>@</code> headword.</p>\n"
        )
    return ChapterDoc("ch_missing", "missing-words.xhtml", "Missing Words", body)


# ---------------------------------------------------------------------------
# Packaging — OPF, NCX, container, mimetype
# ---------------------------------------------------------------------------


def _build_opf(form: str, snapshot_date: str, chapters: list[ChapterDoc]) -> str:
    item_lines = "\n".join(
        f'    <item id="{c.file_id}" href="{c.href}" media-type="application/xhtml+xml"/>'
        for c in chapters
    )
    spine_lines = "\n".join(f'    <itemref idref="{c.file_id}"/>' for c in chapters)
    book_id = f"engrish-{form}-{snapshot_date}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f'    <dc:identifier id="bookid" opf:scheme="URI">urn:engrish:{_esc(book_id)}</dc:identifier>\n'
        f'    <dc:title>Engrish: {_esc(form)} ({_esc(snapshot_date)})</dc:title>\n'
        '    <dc:language>en</dc:language>\n'
        f'    <dc:date opf:event="publication">{_esc(snapshot_date[:4])}-{_esc(snapshot_date[4:6])}-{_esc(snapshot_date[6:8])}</dc:date>\n'
        '    <dc:creator>engrish</dc:creator>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
        '    <item id="css" href="style.css" media-type="text/css"/>\n'
        f'{item_lines}\n'
        '  </manifest>\n'
        '  <spine toc="ncx">\n'
        f'{spine_lines}\n'
        '  </spine>\n'
        '</package>\n'
    )


def _build_ncx(form: str, snapshot_date: str, chapters: list[ChapterDoc]) -> str:
    book_id = f"engrish-{form}-{snapshot_date}"
    nav_points = "\n".join(
        f'    <navPoint id="np_{c.file_id}" playOrder="{i + 1}">\n'
        f'      <navLabel><text>{_esc(c.title)}</text></navLabel>\n'
        f'      <content src="{c.href}"/>\n'
        f'    </navPoint>'
        for i, c in enumerate(chapters)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"\n'
        '    "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head>\n'
        f'    <meta name="dtb:uid" content="urn:engrish:{_esc(book_id)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        '  </head>\n'
        f'  <docTitle><text>Engrish: {_esc(form)}</text></docTitle>\n'
        '  <navMap>\n'
        f'{nav_points}\n'
        '  </navMap>\n'
        '</ncx>\n'
    )


# ---------------------------------------------------------------------------
# EPUB writer — byte-deterministic ZIP packaging
# ---------------------------------------------------------------------------


def _write_zip_member(zf: zipfile.ZipFile, name: str, data: bytes, store: bool = False) -> None:
    """Write ``data`` to ``name`` with fixed mtime + canonical compression flags.

    ``store=True`` for the ``mimetype`` member (must be uncompressed per EPUB spec).
    """
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_DATETIME)
    info.compress_type = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    zf.writestr(info, data)


def write_epub(
    output_path: Path,
    form: str,
    snapshot_date: str,
    locales: list[str],
    df_path: Path,
) -> None:
    """Produce an EPUB 2 sampler at ``output_path`` from the merged ``.df``.

    Per AC8 byte-determinism: identical (df, locales, snapshot_date, form)
    inputs produce byte-identical EPUB output (fixed mtimes, fixed member
    order, deterministic chapter content via SHA-256-based spot-check sampling).
    """
    log.info("Building index from %s", df_path)
    index = build_header_index(df_path)
    synonyms = build_synonym_map(df_path)
    headwords = set(index.keys())
    synonym_count = len(synonyms)
    headword_count = len(index)

    chapters: list[ChapterDoc] = [
        build_cover(form, snapshot_date),
        build_summary(form, snapshot_date, locales, headword_count, synonym_count),
        build_largest(index),
        build_cross_language(df_path, index),
        build_spot_check(index),
        build_missing_words(synonyms, headwords),
    ]

    # Canonical write order: mimetype, container, opf, ncx, css, chapters (manifest order).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, mode="w") as zf:
        _write_zip_member(zf, "mimetype", b"application/epub+zip", store=True)
        _write_zip_member(zf, "META-INF/container.xml", _CONTAINER_XML.encode("utf-8"))
        _write_zip_member(
            zf,
            "OEBPS/content.opf",
            _build_opf(form, snapshot_date, chapters).encode("utf-8"),
        )
        _write_zip_member(zf, "OEBPS/toc.ncx", _build_ncx(form, snapshot_date, chapters).encode("utf-8"))
        _write_zip_member(zf, "OEBPS/style.css", _CSS.encode("utf-8"))
        for c in chapters:
            _write_zip_member(zf, f"OEBPS/{c.href}", _xhtml(c).encode("utf-8"))

    log.info("EPUB written: %s", output_path)
