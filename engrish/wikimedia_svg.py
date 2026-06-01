"""Synthesize TTF glyphs from Wikimedia Commons SVGs for codepoints not
covered by any on-disk Noto font.

Motivation: a handful of newly-encoded Unicode characters (Unicode 14-16
additions to Latin Extended-G, IDC Chars, CJK Strokes, Symbols for Legacy
Computing Supplement, Enclosed Ideographic Supplement, alchemical/asteroid
symbols) have Wiktionary entries but no Noto outline coverage. Wiktionary
itself sidesteps this by embedding Wikimedia Commons SVGs as headword
images. This module fetches those SVGs, vectorizes them into ``glyf``
outlines, and packs them into a single minimal TTF that ``font.py``'s
``pairwise_merge`` consumes as just another source.

Pipeline (per codepoint):
  1. Look up Wikimedia Commons SVG via three canonical name forms
     (``U+XXXX.svg``, ``UXXXX.svg`` lowercase hex, ``Unicode_0xXXXX.svg``).
     Fall back to scraping the Wiktionary entry HTML for the first
     ``commons/.../*.svg`` reference and try that name.
  2. Cache the SVG under ``data/engrish/wikimedia_svg_cache/`` so repeat
     runs don't re-fetch.
  3. Parse with ``fontTools.svgLib.path.SVGPath`` (handles path, circle,
     rect, polygon, etc.), apply a viewBox-derived Transform to normalize
     to ``UPM=1000`` with a ~80% em-height target, flip the Y axis
     (SVG y-down → TT y-up).
  4. Convert cubic to quadratic via ``Cu2QuPen`` over ``TTGlyphPen``.
  5. Pack all glyphs into a synthetic TTF with minimum-viable tables
     (head, hhea, maxp, OS/2, name, cmap, hmtx, post, glyf, loca).

The output font has UPM 1000 so ``_normalize_upem`` in ``font.py`` is a
no-op. It carries no GSUB/GPOS so no layout-overflow risk.

The output is saved by the caller (typically ``build_form_fonts``) and
added to the merge source list dynamically. Codepoints where the SVG
fetch fails fall through to the Tier-3 ``coverage_gaps.txt`` artifact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

import requests
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.transform import Transform
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import SVGPath
from fontTools.ttLib import TTFont

log = logging.getLogger(__name__)

_UA = {
    "User-Agent": (
        "engrish-coverage-builder/1.0 "
        "(https://github.com/mpcrossley/engrish; mpcrosso@gmail.com)"
    )
}
_FILE_PATH_BASE = "https://commons.wikimedia.org/wiki/Special:FilePath/"
_WIKT_BASE = "https://en.wiktionary.org/wiki/"

_TARGET_UPM = 1000
# Symbol glyphs fill ~80% of the em box, baseline at 20% from the bottom —
# matches what a 1em-tall asteroid/IDC symbol should look like next to
# body Latin text.
_GLYPH_HEIGHT_RATIO = 0.80
_BASELINE_OFFSET_RATIO = 0.20

# Skip these chrome / unrelated images when scraping Wiktionary HTML for
# a fallback SVG filename. Patterns are substring-matched, lowercased.
_CHROME_IMG_FILTERS = (
    "wikimedia-button", "powered_by", "commons-logo", "wiktionary-",
    "mediawiki", "oojs", "penrose", "flag_of", "-icon", "poweredby",
    "wikipedia.png", "audio_a.svg", "loudspeaker", "edit_icon",
)

_HTML_IMG_RE = re.compile(
    r'//upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[^/]+/[^/]+/([^/"\']+\.(?:svg|png))'
)


@dataclass
class _GlyphRecord:
    codepoint: int
    glyph_name: str
    advance_width: int


# ---------------------------------------------------------------------------
# SVG fetch + cache
# ---------------------------------------------------------------------------


def _candidate_filenames(cp: int) -> list[str]:
    """Wikimedia Commons filenames worth trying for a given codepoint, in order."""
    return [
        f"U+{cp:04X}.svg",
        f"U{cp:04x}.svg",
        f"Unicode_0x{cp:04X}.svg",
    ]


def _try_download(filename: str, cache_path: Path) -> Path | None:
    """Try one Commons filename; cache to ``cache_path`` on success."""
    url = _FILE_PATH_BASE + quote(filename)
    try:
        r = requests.get(url, headers=_UA, timeout=30, allow_redirects=True)
    except requests.RequestException as exc:
        log.debug("wikimedia fetch %s failed: %s", filename, exc)
        return None
    if r.status_code != 200:
        return None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(r.content)
    return cache_path


def _scrape_wiktionary_for_image(cp: int) -> str | None:
    """Fetch the Wiktionary entry HTML for ``chr(cp)`` and return the first
    plausible Wikimedia Commons ``.svg`` filename embedded in the page,
    skipping site chrome. ``None`` if entry doesn't exist or has no SVG.
    """
    url = _WIKT_BASE + quote(chr(cp))
    try:
        r = requests.get(url, headers=_UA, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    for match in _HTML_IMG_RE.findall(r.text):
        if not match.lower().endswith(".svg"):
            continue
        if any(skip in match.lower() for skip in _CHROME_IMG_FILTERS):
            continue
        # Scraped filenames come URL-encoded from the HTML (``%28`` etc.).
        # Decode before returning so the caller's ``quote()`` doesn't
        # double-encode on its way to Special:FilePath.
        return unquote(match)
    return None


def fetch_svg(cp: int, cache_dir: Path) -> Path | None:
    """Return a cached SVG path for ``cp``, downloading from Wikimedia Commons
    on first request. Tries canonical filenames first, then falls back to
    scraping the Wiktionary entry HTML for a themed filename
    (e.g. ``Hebe_symbol_(fixed_width).svg``). Returns ``None`` if no SVG
    found at any path.
    """
    cache_path = cache_dir / f"U+{cp:04X}.svg"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    for fn in _candidate_filenames(cp):
        if _try_download(fn, cache_path):
            return cache_path

    themed = _scrape_wiktionary_for_image(cp)
    if themed and _try_download(themed, cache_path):
        return cache_path

    return None


# ---------------------------------------------------------------------------
# SVG → TT glyph outline
# ---------------------------------------------------------------------------


_VIEWBOX_RE = re.compile(r'viewBox\s*=\s*"\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*"')
_WIDTH_RE = re.compile(r'\bwidth\s*=\s*"\s*([\d.\-]+)(?:px)?\s*"')
_HEIGHT_RE = re.compile(r'\bheight\s*=\s*"\s*([\d.\-]+)(?:px)?\s*"')

# SVG transform attribute can contain a whitespace-separated chain of
# ``translate(...)``, ``rotate(...)``, ``scale(...)``, ``skewX(...)``,
# ``skewY(...)``, or ``matrix(...)`` functions. fontTools' SVGPath parser
# only handles the ``matrix(...)`` form — every other form raises
# ``NotImplementedError``. We flatten arbitrary transform chains into a
# single ``matrix(a,b,c,d,e,f)`` so SVGPath sees only the supported form.
_TRANSFORM_FN_RE = re.compile(r"(matrix|translate|rotate|scale|skewX|skewY)\s*\(([^)]*)\)")
_TRANSFORM_ATTR_RE = re.compile(r'\btransform\s*=\s*"([^"]*)"')


def _parse_transform_args(s: str) -> list[float]:
    return [float(p) for p in re.split(r"[\s,]+", s.strip()) if p]


def _compose_svg_transform(value: str) -> Transform:
    """Parse an SVG ``transform`` attribute chain into a single ``Transform``.

    The chain reads left-to-right; SVG composes transforms by post-multiplication
    (each successive function is applied to the result-space of the previous).
    fontTools' ``Transform.transform(other)`` returns ``self * other`` in
    pre-multiplication order, which matches: starting from identity, ``t =
    t.transform(next)`` accumulates left-to-right composition.
    """
    import math
    t = Transform()
    for fn_match in _TRANSFORM_FN_RE.finditer(value):
        fn = fn_match.group(1)
        args = _parse_transform_args(fn_match.group(2))
        if fn == "matrix":
            if len(args) != 6:
                raise ValueError(f"matrix() needs 6 args, got {len(args)}")
            step = Transform(*args)
        elif fn == "translate":
            tx = args[0]
            ty = args[1] if len(args) > 1 else 0.0
            step = Transform(1, 0, 0, 1, tx, ty)
        elif fn == "scale":
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            step = Transform(sx, 0, 0, sy, 0, 0)
        elif fn == "rotate":
            angle = math.radians(args[0])
            c, s = math.cos(angle), math.sin(angle)
            step = Transform(c, s, -s, c, 0, 0)
            if len(args) == 3:
                cx, cy = args[1], args[2]
                # rotate(a, cx, cy) = translate(cx, cy) rotate(a) translate(-cx, -cy)
                step = (
                    Transform(1, 0, 0, 1, cx, cy)
                    .transform(step)
                    .transform(Transform(1, 0, 0, 1, -cx, -cy))
                )
        elif fn == "skewX":
            step = Transform(1, 0, math.tan(math.radians(args[0])), 1, 0, 0)
        elif fn == "skewY":
            step = Transform(1, math.tan(math.radians(args[0])), 0, 1, 0, 0)
        else:
            raise ValueError(f"unknown SVG transform function: {fn}")
        t = t.transform(step)
    return t


def _flatten_svg_transforms(svg_text: str) -> str:
    """Rewrite every ``transform="..."`` attribute as ``matrix(a,b,c,d,e,f)``
    so fontTools' SVGPath (which only handles the matrix form) can apply them.
    """
    def repl(m: re.Match) -> str:
        value = m.group(1)
        # Already pure matrix? Leave it.
        stripped = re.sub(r"\s+", "", value)
        if re.fullmatch(r"matrix\([^)]*\)", stripped):
            return m.group(0)
        try:
            t = _compose_svg_transform(value)
        except (ValueError, ZeroDivisionError):
            return m.group(0)
        return f'transform="matrix({t[0]},{t[1]},{t[2]},{t[3]},{t[4]},{t[5]})"'
    return _TRANSFORM_ATTR_RE.sub(repl, svg_text)


def _svg_bbox(svg_text: str) -> tuple[float, float, float, float]:
    """Return ``(min_x, min_y, width, height)`` for the SVG's drawing area.

    Prefers explicit ``viewBox``; falls back to ``width``/``height`` with
    origin at ``(0, 0)``; ultimately defaults to ``(0, 0, 1000, 1000)``.
    """
    m = _VIEWBOX_RE.search(svg_text)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))
    w = _WIDTH_RE.search(svg_text)
    h = _HEIGHT_RE.search(svg_text)
    if w and h:
        return (0.0, 0.0, float(w.group(1)), float(h.group(1)))
    return (0.0, 0.0, 1000.0, 1000.0)


def svg_to_tt_glyph(svg_bytes: bytes) -> tuple["object", int]:
    """Convert an SVG byte string into a ``(TTGlyph, advance_width)`` pair.

    Returns the compiled glyph and its advance width in font units (UPM
    1000). Centers the glyph horizontally within the advance and vertically
    around the baseline + ``_BASELINE_OFFSET_RATIO * UPM``.

    The Y-axis flip is folded into the SVG-units → font-units Transform
    so the underlying ``parse_path`` output already lands in TT-space.
    """
    svg_text = svg_bytes.decode("utf-8", errors="replace")
    svg_text = _flatten_svg_transforms(svg_text)
    min_x, min_y, w, h = _svg_bbox(svg_text)
    if w <= 0 or h <= 0:
        raise ValueError(f"invalid SVG bbox: {(min_x, min_y, w, h)}")

    # Choose uniform scale so the glyph's longer dimension fills
    # ``_GLYPH_HEIGHT_RATIO`` of the em box.
    target_height = _TARGET_UPM * _GLYPH_HEIGHT_RATIO
    scale = min(target_height / h, target_height / w)
    glyph_w = w * scale
    glyph_h = h * scale

    baseline = int(_BASELINE_OFFSET_RATIO * _TARGET_UPM)
    # Center horizontally inside an advance of full em width (the typical
    # display footprint for these symbols). Vertical translate places the
    # glyph above the baseline at the target offset.
    advance_width = _TARGET_UPM
    x_translate = (advance_width - glyph_w) / 2 - min_x * scale
    y_translate = baseline + glyph_h - (-min_y) * scale

    # Affine: SVG (x, y) → (scale*x + tx, -scale*y + ty).
    # The negative Y scale handles the axis flip in one step.
    transform = Transform(scale, 0, 0, -scale, x_translate, y_translate)

    tt_pen = TTGlyphPen(None)
    cu2qu_pen = Cu2QuPen(tt_pen, max_err=1.0)
    SVGPath.fromstring(svg_text.encode("utf-8"), transform=transform).draw(cu2qu_pen)
    return tt_pen.glyph(), int(advance_width)


# ---------------------------------------------------------------------------
# Pack glyphs into a TTF
# ---------------------------------------------------------------------------


def build_wikimedia_glyph_font(
    codepoints: Iterable[int],
    out_path: Path,
    cache_dir: Path,
    family_name: str = "Engrish Wikimedia Symbols",
) -> tuple[Path | None, set[int], set[int]]:
    """Build a minimal TTF carrying glyphs synthesized from Wikimedia SVGs
    for the requested ``codepoints``.

    Returns ``(out_path or None, found, missing)``:
      - ``out_path`` is the saved file, or ``None`` if no glyphs could be
        synthesized (caller should skip adding it to the merge sources).
      - ``found`` is the set of cps that successfully became glyphs.
      - ``missing`` is the set that fell through (no SVG, or SVG that
        couldn't be parsed / vectorized).

    The TTF is saved deterministically (fixed ``head.created/modified``).
    """
    codepoints = sorted(set(codepoints))
    if not codepoints:
        return None, set(), set()

    glyph_records: list[_GlyphRecord] = []
    glyphs: dict[str, object] = {}
    found: set[int] = set()
    missing: set[int] = set()

    for cp in codepoints:
        svg_path = fetch_svg(cp, cache_dir)
        if svg_path is None:
            missing.add(cp)
            continue
        try:
            tt_glyph, adv = svg_to_tt_glyph(svg_path.read_bytes())
        except Exception as exc:  # noqa: BLE001 — SVG parsers raise varied types
            log.warning("wikimedia: vectorize failed for U+%04X: %s", cp, exc)
            missing.add(cp)
            continue
        glyph_name = f"u{cp:04X}"
        glyphs[glyph_name] = tt_glyph
        glyph_records.append(_GlyphRecord(cp, glyph_name, adv))
        found.add(cp)

    if not glyph_records:
        return None, found, missing

    # FontBuilder synthesizes head/hhea/maxp/OS_2/post/cmap/glyf/loca etc.
    fb = FontBuilder(_TARGET_UPM, isTTF=True)

    glyph_order = [".notdef"] + [r.glyph_name for r in glyph_records]
    fb.setupGlyphOrder(glyph_order)

    cmap = {r.codepoint: r.glyph_name for r in glyph_records}
    fb.setupCharacterMap(cmap)

    # ``.notdef`` is a minimal empty glyph — FontBuilder won't synthesize
    # one if it isn't in ``glyphs``.
    notdef_pen = TTGlyphPen(None)
    glyphs[".notdef"] = notdef_pen.glyph()

    fb.setupGlyf(glyphs)

    metrics = {r.glyph_name: (r.advance_width, 0) for r in glyph_records}
    metrics[".notdef"] = (_TARGET_UPM // 2, 0)
    fb.setupHorizontalMetrics(metrics)

    # Ascent/descent: match a typical UPM-1000 Latin font envelope so the
    # merged hhea aggregation downstream works cleanly.
    ascent = int(_TARGET_UPM * 0.80)
    descent = -int(_TARGET_UPM * 0.20)
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, usWinAscent=ascent, usWinDescent=-descent)

    fb.setupNameTable({
        "familyName": family_name,
        "styleName": "Regular",
    })

    fb.setupPost()

    # Deterministic save — mirror font.py's _FIXED_HEAD_TIMESTAMP convention
    fb.font["head"].created = 2082844800
    fb.font["head"].modified = 2082844800
    fb.font.recalcTimestamp = False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))

    log.info(
        "wikimedia: synthesized %d / %d glyph(s) into %s (missing: %d)",
        len(found), len(codepoints), out_path, len(missing),
    )
    return out_path, found, missing


def cmap_of(path: Path) -> set[int]:
    """Read the cmap of a synthesized font; convenience for source-union calc."""
    t = TTFont(str(path), lazy=True)
    try:
        return set(t.getBestCmap().keys())
    finally:
        t.close()
