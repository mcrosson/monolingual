"""Font management: download, detect, and generate minimized dictionary fonts."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import requests

from .config import EPUB_BASE_FONTS, FONTS_DIR, SEED_FONTS, _ENGRISH_CFG

log = logging.getLogger(__name__)

# GitHub raw base URLs for font repositories.
_NOTO_BASE = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts"
_NOTO_API = "https://api.github.com/repos/notofonts/notofonts.github.io/contents/fonts"
_CJK_BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF"
_CJK_API = "https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/SubsetOTF"
_EMOJI_BASE = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts"
_EMOJI_API = "https://api.github.com/repos/googlefonts/noto-emoji/contents/fonts"
_GOOGLE_FONTS_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl"
_GOOGLE_FONTS_API = "https://api.github.com/repos/google/fonts/contents/ofl"


def _existing_stems(fonts_dir: Path) -> set[str]:
    """Return the set of font stems already present on disk."""
    stems: set[str] = set()
    if fonts_dir.is_dir():
        for f in fonts_dir.iterdir():
            if f.suffix in (".ttf", ".otf"):
                stems.add(f.stem.split("[")[0].split("-")[0])
    return stems


def _candidate_urls(stem: str) -> list[tuple[str, str]]:
    """Return (url, filename) candidates to try, in priority order.

    Tries all known Noto font repositories generically.  For NotoSans*
    stems, also tries NotoSerif* as a fallback (some scripts only have
    Serif variants published).  For a given stem like "NotoSansArabic",
    the noto-cjk URL will simply 404 and be skipped.
    """
    suffix = stem.removeprefix("NotoSans").removeprefix("NotoSerif")
    urls = [
        # notofonts.github.io — variable weight font
        (
            f"{_NOTO_BASE}/{stem}/unhinted/slim-variable-ttf/{stem}%5Bwght%5D.ttf",
            f"{stem}[wght].ttf",
        ),
        # notofonts.github.io — static Regular font
        (
            f"{_NOTO_BASE}/{stem}/full/ttf/{stem}-Regular.ttf",
            f"{stem}-Regular.ttf",
        ),
        # noto-cjk — subset OTF (directory name = suffix after NotoSans)
        (
            f"{_CJK_BASE}/{suffix}/{stem}-Regular.otf",
            f"{stem}-Regular.otf",
        ),
    ]
    # Serif fallback: if this is a NotoSans* stem, also try NotoSerif*
    if stem.startswith("NotoSans"):
        serif = "NotoSerif" + stem.removeprefix("NotoSans")
        urls.extend([
            (
                f"{_NOTO_BASE}/{serif}/unhinted/slim-variable-ttf/{serif}%5Bwght%5D.ttf",
                f"{serif}[wght].ttf",
            ),
            (
                f"{_NOTO_BASE}/{serif}/full/ttf/{serif}-Regular.ttf",
                f"{serif}-Regular.ttf",
            ),
        ])
    # googlefonts/noto-emoji — color emoji font
    urls.extend([
        (f"{_EMOJI_BASE}/{stem}-noflags.ttf", f"{stem}-noflags.ttf"),
        (f"{_EMOJI_BASE}/{stem}.ttf", f"{stem}.ttf"),
    ])
    # google/fonts repo (ofl directory) — additional Noto fonts not in notofonts.github.io
    ofl_name = stem.lower()
    urls.extend([
        (
            f"{_GOOGLE_FONTS_BASE}/{ofl_name}/{stem}%5Bwght%5D.ttf",
            f"{stem}[wght].ttf",
        ),
        (
            f"{_GOOGLE_FONTS_BASE}/{ofl_name}/{stem}-Regular.ttf",
            f"{stem}-Regular.ttf",
        ),
    ])
    return urls


def _font_exists_remote(stem: str) -> bool:
    """Check whether a font for *stem* exists at any known download URL."""
    for url, _ in _candidate_urls(stem):
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
    return False


def _download_font(stem: str, fonts_dir: Path) -> bool:
    """Try to download a font for *stem*. Returns True on success."""
    for url, filename in _candidate_urls(stem):
        log.info("Trying %s", url)
        try:
            resp = requests.get(url, timeout=60)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            dest = fonts_dir / filename
            dest.write_bytes(resp.content)
            log.info("Downloaded %s (%d bytes)", dest.name, len(resp.content))
            return True
        log.debug("  %d — %s", resp.status_code, url)
    return False


# ---------------------------------------------------------------------------
# Font detection — used by add-language to populate the "fonts" config field
# ---------------------------------------------------------------------------

_cached_stems: tuple[set[str], set[str]] | None = None


def _discover_available_stems() -> tuple[set[str], set[str]]:
    """Query both Noto font repos for all available font stems.

    Returns ``(all_stems, cjk_stems)`` where *cjk_stems* is the subset
    that came from the noto-cjk repo (region-based naming that can't be
    derived from Unicode character names).  Includes both NotoSans and
    NotoSerif stems since some scripts only have Serif variants.

    Results are cached for the lifetime of the process so that adding
    multiple languages in one invocation only makes two API calls total.
    """
    global _cached_stems
    if _cached_stems is not None:
        return _cached_stems

    main_stems: set[str] = set()
    cjk_stems: set[str] = set()
    # Main notofonts repo — one directory per font family
    try:
        resp = requests.get(_NOTO_API, timeout=15)
        if resp.status_code == 200:
            main_stems.update(
                item["name"]
                for item in resp.json()
                if item.get("type") == "dir"
                and (item["name"].startswith("NotoSans") or item["name"].startswith("NotoSerif"))
            )
    except requests.RequestException:
        log.warning("Could not query notofonts.github.io for font discovery")
    # CJK repo — one subdirectory per region (JP, SC, etc.)
    try:
        resp = requests.get(_CJK_API, timeout=15)
        if resp.status_code == 200:
            cjk_stems.update(
                f"NotoSans{item['name']}"
                for item in resp.json()
                if item.get("type") == "dir"
            )
    except requests.RequestException:
        log.warning("Could not query noto-cjk repo for CJK font discovery")
    # Emoji repo — font files in the fonts/ directory
    try:
        resp = requests.get(_EMOJI_API, timeout=15)
        if resp.status_code == 200:
            for item in resp.json():
                if item.get("type") == "file" and item["name"].endswith(".ttf"):
                    stem = item["name"].split("-")[0].split(".")[0]
                    if stem.startswith("Noto"):
                        main_stems.add(stem)
    except requests.RequestException:
        log.warning("Could not query noto-emoji repo for emoji font discovery")
    # google/fonts repo — used as a download fallback in _candidate_urls
    # but not enumerated here (thousands of non-Noto entries).
    # Fonts only in google/fonts are found via name-prefix matching
    # and downloaded through _candidate_urls which includes google/fonts URLs.
    _cached_stems = (main_stems | cjk_stems, cjk_stems)
    return _cached_stems


# ---------------------------------------------------------------------------
# Parallel dump scanning
# ---------------------------------------------------------------------------

def _scan_chunk(
    db_path: str,
    targets_lower: list[str],
    row_lo: int,
    row_hi: int,
) -> dict[str, set[str]]:
    """Worker: scan a rowid range for headword chars across all target languages.

    Each worker opens its own SQLite connection so there is no GIL
    contention.  Returns ``{section_name_lower: set_of_chars}``.
    """
    l2 = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.MULTILINE)
    target_set = set(targets_lower)
    result: dict[str, set[str]] = {t: set() for t in targets_lower}
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT title, body FROM pages "
            "WHERE namespace_id = 0 AND rowid >= ? AND rowid < ?",
            (row_lo, row_hi),
        )
        for title, body in cur:
            if body is None:
                continue
            body_lower = body.lower()
            # Quick pre-filter: only parse headings if any target appears
            possible = [t for t in target_set if t in body_lower]
            if not possible:
                continue
            for m in l2.finditer(body):
                heading = m.group(1).lower()
                if heading in target_set:
                    for ch in title:
                        if ord(ch) > 127:
                            result[heading].add(ch)
    finally:
        con.close()
    return result


def collect_headword_chars_batch(
    locale_codes: list[str],
    db_path: Path,
    *,
    wiktionary_sections: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """Collect headword chars for one or more languages in a single parallel scan.

    *locale_codes* is a list of language codes (e.g. ``["en", "ang", "fr"]``).
    The wiktionary L2 heading for each code is resolved from the engrish
    config.  For codes not yet in the config (being added), pass
    *wiktionary_sections* as a ``{code: section}`` mapping.

    Partitions the dump by rowid range across ``os.cpu_count()`` worker
    processes, each scanning for all requested languages simultaneously.
    Returns ``{code: set_of_non_ascii_chars}``.
    """
    # Resolve wiktionary section headings for each code.
    sections: dict[str, str] = {}
    for code in locale_codes:
        if wiktionary_sections and code in wiktionary_sections:
            sections[code] = wiktionary_sections[code]
        elif code in _ENGRISH_CFG:
            sections[code] = _ENGRISH_CFG[code]["wiktionary_section"]
        else:
            raise ValueError(
                f"Locale '{code}' not in config and no wiktionary_section provided"
            )

    targets_lower = [s.lower() for s in sections.values()]
    code_for_target = {s.lower(): code for code, s in sections.items()}
    db_str = str(db_path)

    # Determine rowid range for partitioning.
    con = sqlite3.connect(db_str)
    try:
        cur = con.cursor()
        cur.execute("SELECT MIN(rowid), MAX(rowid) FROM pages WHERE namespace_id = 0")
        row = cur.fetchone()
        if row is None or row[0] is None:
            return {code: set() for code in locale_codes}
        lo, hi = row
    finally:
        con.close()

    n_workers = os.cpu_count() or 4
    chunk_size = (hi - lo + 2) // n_workers
    chunks = []
    for i in range(n_workers):
        c_lo = lo + i * chunk_size
        c_hi = min(lo + (i + 1) * chunk_size, hi + 1)
        if c_lo < c_hi:
            chunks.append((db_str, targets_lower, c_lo, c_hi))

    # Run workers.
    merged: dict[str, set[str]] = {t: set() for t in targets_lower}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        for partial in pool.map(_scan_chunk, *zip(*chunks)):
            for t in targets_lower:
                merged[t].update(partial[t])

    # Return keyed by locale code.
    return {code: merged[sections[code].lower()] for code in locale_codes}


def _best_stem_for_char(ch: str, confirmed: set[str]) -> str | None:
    """Return the longest confirmed font stem matching *ch*, or None.

    Tries both NotoSans and NotoSerif prefixes, preferring Sans over Serif
    at the same prefix length.
    """
    name = unicodedata.name(ch, None)
    if not name:
        return None
    words = name.split()
    best: str | None = None
    best_n = 0
    for n in range(1, min(5, len(words) + 1)):
        suffix = "".join(w.title() for w in words[:n])
        # Prefer Sans over Serif at the same prefix length
        for prefix in ("NotoSans", "NotoSerif"):
            stem = prefix + suffix
            for s in (stem + "s", stem):  # try plural first
                if s in confirmed and n > best_n:
                    best = s
                    best_n = n
    return best if best and best not in ("NotoSans", "NotoSerif") else None


def _load_cmap(font_path: Path) -> set[int]:
    """Return the set of codepoints covered by a font file."""
    from fontTools.ttLib import TTFont

    font = TTFont(font_path)
    cmap = font.getBestCmap()
    font.close()
    return set(cmap) if cmap else set()


_cached_cmaps: dict[str, set[int]] = {}


def _find_font_file(stem: str, fonts_dir: Path) -> Path | None:
    """Find the font file for a stem on disk, or None."""
    if not fonts_dir.is_dir():
        return None
    for f in fonts_dir.iterdir():
        if f.suffix not in (".ttf", ".otf"):
            continue
        if f.stem.split("[")[0].split("-")[0] == stem:
            return f
    return None


def _ensure_font_on_disk(stem: str, fonts_dir: Path) -> Path | None:
    """Return the path to a font file for *stem*, downloading if needed."""
    existing = _find_font_file(stem, fonts_dir)
    if existing:
        return existing
    fonts_dir.mkdir(parents=True, exist_ok=True)
    if _download_font(stem, fonts_dir):
        return _find_font_file(stem, fonts_dir)
    return None


def _cmap_for_stem(stem: str, fonts_dir: Path) -> set[int]:
    """Return the cmap for a font stem, loading and caching.

    Downloads the font if it is not already on disk.
    """
    if stem in _cached_cmaps:
        return _cached_cmaps[stem]
    cmap: set[int] = set()
    font_path = _ensure_font_on_disk(stem, fonts_dir)
    if font_path:
        try:
            cmap = _load_cmap(font_path)
        except Exception:
            log.debug("Could not read cmap from %s", font_path)
    _cached_cmaps[stem] = cmap
    return cmap


def detect_fonts(
    locale_code: str,
    db_path: Path,
    chars: set[str] | None = None,
    *,
    wiktionary_section: str | None = None,
) -> list[str]:
    """Detect font stems needed for a language.

    *locale_code* is the ISO language code (e.g. ``"en"``, ``"ang"``).
    The wiktionary L2 heading is resolved from the config.  For codes
    not yet in the config (being added), pass *wiktionary_section*.

    If *chars* is provided (from a prior ``collect_headword_chars_batch``
    call), uses those directly.  Otherwise performs a full parallel scan.

    Returns exactly the set of validated stems that cover the headword
    characters — nothing hardcoded, nothing assumed.
    """
    if chars is None:
        ws = {locale_code: wiktionary_section} if wiktionary_section else None
        chars = collect_headword_chars_batch([locale_code], db_path, wiktionary_sections=ws)[locale_code]
    if not chars:
        return []

    log.info("Found %d unique non-ASCII headword characters for %s", len(chars), locale_code)

    # --- Stage 0: seed fonts ---
    # Download seed fonts if missing.  These are checked first via cmap to
    # resolve characters (e.g. Latin, Greek, Cyrillic) that can't be mapped
    # to a script-specific font via name-prefix.
    if SEED_FONTS:
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        for stem in SEED_FONTS:
            if not _find_font_file(stem, FONTS_DIR):
                log.info("Downloading seed font %s...", stem)
                if not _download_font(stem, FONTS_DIR):
                    raise RuntimeError(
                        f"Seed font '{stem}' could not be downloaded. "
                        f"Check the seed_fonts list in engrish.json."
                    )

    # Fetch the authoritative set of available font stems (local + remote).
    remote_available, cjk_stems = _discover_available_stems()
    available = _existing_stems(FONTS_DIR) | remote_available

    # --- Stage 1: name-prefix → download fonts ---
    # Build candidate stems from character name prefixes and download any
    # that are not already on disk.
    candidates: set[str] = set()
    for ch in chars:
        name = unicodedata.name(ch, None)
        if not name:
            continue
        words = name.split()
        for n in range(1, min(5, len(words) + 1)):
            suffix = "".join(w.title() for w in words[:n])
            for prefix in ("NotoSans", "NotoSerif"):
                candidates.add(prefix + suffix)
                candidates.add(prefix + suffix + "s")

    to_download = (candidates & available) - _existing_stems(FONTS_DIR)
    if to_download:
        log.info("Downloading %d name-matched font(s)...", len(to_download))
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        for stem in sorted(to_download):
            _download_font(stem, FONTS_DIR)

    # --- Stage 2: cmap-verified greedy set cover ---
    # Seed fonts first, then candidate fonts, then all other local fonts,
    # then CJK.  Each round adds new stem cmaps to the pool and continues
    # the same greedy cover until all characters are matched or no fonts
    # help.
    all_cps = {ord(ch) for ch in chars}
    confirmed = candidates & available
    stem_cmaps: dict[str, set[int]] = {}
    result: set[str] = set()
    remaining_cps = set(all_cps)

    def _load_stems(stems: set[str]) -> None:
        """Load cmaps for *stems* not already in stem_cmaps."""
        for stem in stems:
            if stem not in stem_cmaps:
                cm = _cmap_for_stem(stem, FONTS_DIR)
                if cm:
                    stem_cmaps[stem] = cm

    def _stem_sort_key(stem: str) -> tuple[int, int, str]:
        """Sort key: Sans before Serif, smaller cmap before larger, then alphabetical.

        This prefers more targeted fonts (e.g. NotoSansMath over NotoSans)
        when coverage count is equal, while still preferring Sans over Serif.
        """
        cmap_size = len(stem_cmaps.get(stem, ()))
        return (0 if stem.startswith("NotoSans") else 1, cmap_size, stem)

    def _greedy_cover() -> None:
        """Pick fonts covering the most remaining codepoints until exhausted.

        Tiebreakers when coverage count is equal: prefer Sans over Serif,
        then alphabetical.  This ensures deterministic results.
        """
        nonlocal remaining_cps
        while remaining_cps:
            best_stem: str | None = None
            best_count = 0
            best_covered: set[int] = set()
            for stem in sorted(stem_cmaps, key=_stem_sort_key):
                covered = remaining_cps & stem_cmaps[stem]
                if len(covered) > best_count:
                    best_stem = stem
                    best_count = len(covered)
                    best_covered = covered
            if not best_stem:
                break
            result.add(best_stem)
            remaining_cps -= best_covered

    # Round 1: seed fonts
    if SEED_FONTS:
        _load_stems(set(SEED_FONTS))
        _greedy_cover()

    # Round 2: candidate fonts (name-prefix matched)
    if remaining_cps:
        _load_stems(confirmed)
        _greedy_cover()

    # Round 3: all other local fonts
    if remaining_cps:
        _load_stems(_existing_stems(FONTS_DIR))
        _greedy_cover()

    # Round 3: CJK fonts (download if needed)
    if remaining_cps and cjk_stems:
        cjk_to_download = cjk_stems - _existing_stems(FONTS_DIR)
        if cjk_to_download:
            log.info("Downloading %d CJK font(s)...", len(cjk_to_download))
            FONTS_DIR.mkdir(parents=True, exist_ok=True)
            for stem in sorted(cjk_to_download):
                _download_font(stem, FONTS_DIR)
        _load_stems(cjk_stems)
        _greedy_cover()

    # --- Stage 4: warn about truly uncovered characters ---
    unmatched_chars = {ch for ch in chars if ord(ch) in remaining_cps}
    if unmatched_chars:
        char_list = "\n".join(
            f"  U+{ord(ch):04X}  {unicodedata.name(ch, '?')}"
            for ch in sorted(unmatched_chars)
        )
        log.warning(
            "%d headword character(s) could not be matched to any available "
            "NotoSans font:\n%s",
            len(unmatched_chars),
            char_list,
        )
        log.warning(
            "To add coverage: download the appropriate .ttf/.otf font, place "
            "it in %s, and add the font stem to the 'fonts' list in the "
            "config entry for this language.",
            FONTS_DIR,
        )

    return sorted(result)


# ---------------------------------------------------------------------------
# update-fonts command
# ---------------------------------------------------------------------------

def run_update() -> int:
    """Download any fonts referenced in engrish.json but missing from disk."""
    needed: set[str] = set(SEED_FONTS)
    for cfg in _ENGRISH_CFG.values():
        needed.update(cfg.get("fonts", []))

    if not needed:
        log.info("No fonts referenced in config")
        return 0

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    present = _existing_stems(FONTS_DIR)
    missing = sorted(needed - present)

    if not missing:
        log.info("All %d referenced fonts already present", len(needed))
        return 0

    log.info("%d font(s) missing: %s", len(missing), ", ".join(missing))
    failed: list[str] = []
    for stem in missing:
        if not _download_font(stem, FONTS_DIR):
            log.error("Could not download font: %s", stem)
            failed.append(stem)

    if failed:
        log.error(
            "%d font(s) could not be downloaded: %s",
            len(failed),
            ", ".join(failed),
        )
        return 1

    log.info("All missing fonts downloaded successfully")
    return 0


# ---------------------------------------------------------------------------
# Font index and locale font resolution (moved from epub.py)
# ---------------------------------------------------------------------------

# Build font index from whatever font files exist in FONTS_DIR.
_FONT_INDEX: dict[str, Path] = {}
for _f in sorted(FONTS_DIR.glob("*")):
    if _f.suffix in (".ttf", ".otf"):
        _stem = _f.stem.split("[")[0].split("-")[0]
        _FONT_INDEX[_stem] = _f


def _fonts_for_locales(locales: list[str]) -> list[tuple[str, str]]:
    """Return (font_stem, filename) pairs needed for the given locales.

    Reads the 'fonts' list from each locale's engrish.json entry, unions them,
    deduplicates, and sorts by file size (largest first so common scripts
    appear first in the CSS font-family cascade).
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
        for stem in sorted(needed, key=lambda s: -_FONT_INDEX[s].stat().st_size)
    ]


# ---------------------------------------------------------------------------
# Minimized font generation (subset + merge)
# ---------------------------------------------------------------------------


def collect_codepoints(locales: list[str]) -> set[int]:
    """Collect all unique codepoints from .df files for the given locales."""
    from .paths import df_path

    codepoints: set[int] = set()
    for locale in locales:
        path = df_path(locale, noetym=False)
        if not path.exists():
            log.warning("No .df for %s at %s, skipping", locale, path)
            continue
        text = path.read_text(encoding="utf-8")
        codepoints.update(ord(ch) for ch in text)
    return codepoints


def _select_fonts(
    dict_cps: set[int],
    fonts_dir: Path,
    *,
    italic: bool = False,
) -> list[tuple[str, Path, set[int]]]:
    """Greedy set cover: pick fonts from fonts_dir covering dict_cps.

    Skips CBDT-only fonts (no glyf, no CFF).
    If italic=True, only includes fonts that have an italic variant file on disk.

    Returns list of (stem, font_path, assigned_codepoints).
    """
    from fontTools.ttLib import TTFont

    available: dict[str, tuple[Path, set[int]]] = {}
    for fpath in sorted(fonts_dir.glob("*")):
        if fpath.suffix not in (".ttf", ".otf"):
            continue
        if fpath.name.startswith("engrish"):
            continue
        # Skip italic files from the upright selection (they'll be used directly for italic builds)
        if "-Italic" in fpath.name:
            continue
        stem = fpath.stem.split("[")[0].split("-")[0]
        font = TTFont(fpath)
        has_glyf = "glyf" in font
        has_cff = "CFF " in font
        cmap = font.getBestCmap() or {}
        font.close()
        if not has_glyf and not has_cff:
            continue
        if italic:
            # Only include this font if an italic variant file exists
            italic_file = _find_italic_file(stem, fonts_dir)
            if not italic_file:
                continue
        available[stem] = (fpath, set(cmap.keys()))

    remaining = set(dict_cps)
    selected: list[tuple[str, Path, set[int]]] = []
    while remaining:
        best_stem = None
        best_count = 0
        for stem, (fpath, cmap_cps) in available.items():
            overlap = len(remaining & cmap_cps)
            if overlap > best_count:
                best_stem = stem
                best_count = overlap
        if not best_stem or best_count == 0:
            break
        fpath, cmap_cps = available.pop(best_stem)
        covered = remaining & cmap_cps
        remaining -= covered
        selected.append((best_stem, fpath, covered))

    return selected


def _find_italic_file(stem: str, fonts_dir: Path) -> Path | None:
    """Find an italic variant font file for a stem on disk."""
    for fpath in fonts_dir.glob("*"):
        if fpath.suffix not in (".ttf", ".otf"):
            continue
        name = fpath.name
        # Match patterns like NotoSans-Italic[wght].ttf or NotoSans-Italic-Regular.ttf
        if name.startswith(f"{stem}-Italic"):
            return fpath
    return None


def _download_italic(stem: str, fonts_dir: Path) -> bool:
    """Download the italic variant for a font stem.

    Italic files live under the parent family directory, not a separate
    directory.  E.g. NotoSans-Italic[wght].ttf is at:
      notofonts.github.io/fonts/NotoSans/unhinted/slim-variable-ttf/NotoSans-Italic[wght].ttf
    """
    italic_stem = f"{stem}-Italic"
    urls = [
        (
            f"{_NOTO_BASE}/{stem}/unhinted/slim-variable-ttf/{italic_stem}%5Bwght%5D.ttf",
            f"{italic_stem}[wght].ttf",
        ),
        (
            f"{_NOTO_BASE}/{stem}/full/ttf/{italic_stem}-Regular.ttf",
            f"{italic_stem}-Regular.ttf",
        ),
        (
            f"{_GOOGLE_FONTS_BASE}/{stem.lower()}/{italic_stem}%5Bwght%5D.ttf",
            f"{italic_stem}[wght].ttf",
        ),
        (
            f"{_GOOGLE_FONTS_BASE}/{stem.lower()}/{italic_stem}-Regular.ttf",
            f"{italic_stem}-Regular.ttf",
        ),
    ]
    for url, filename in urls:
        log.info("Trying italic: %s", url)
        try:
            resp = requests.get(url, timeout=60)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            dest = fonts_dir / filename
            dest.write_bytes(resp.content)
            log.info("Downloaded %s (%d bytes)", dest.name, len(resp.content))
            return True
        log.debug("  %d — %s", resp.status_code, url)
    return False


def _build_merged_font(
    selected: list[tuple[str, Path, set[int]]],
    wght: float,
    output_path: Path,
    *,
    italic: bool = False,
    fonts_dir: Path | None = None,
) -> None:
    """Subset, flatten, convert, and merge selected fonts into a single .ttf.

    Args:
        selected: list of (stem, upright_font_path, assigned_codepoints)
        wght: weight value to pin variable fonts to (400 for regular, 700 for bold)
        output_path: where to write the merged font
        italic: if True, use italic variant files instead of upright
        fonts_dir: directory to search for italic files (required if italic=True)
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.merge import Merger
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.subset import Subsetter
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.scaleUpem import scale_upem
    from fontTools.varLib.instancer import instantiateVariableFont

    KEEP = {
        "glyf", "cmap", "head", "hhea", "hmtx", "loca", "maxp",
        "name", "post", "OS/2", "GSUB", "GPOS", "GDEF",
    }

    import tempfile

    glyf_temps: list[str] = []
    try:
        for stem, upright_path, cps_to_keep in selected:
            # Use italic file if requested
            if italic and fonts_dir:
                fpath = _find_italic_file(stem, fonts_dir)
                if not fpath:
                    continue
            else:
                fpath = upright_path

            font = TTFont(fpath)

            # Subset to assigned codepoints
            sub = Subsetter()
            sub.populate(unicodes=list(cps_to_keep))
            sub.subset(font)

            # Flatten variable fonts to target weight
            if "fvar" in font:
                axes = {}
                for axis in font["fvar"].axes:
                    if axis.axisTag == "wght":
                        # Clamp to axis range
                        axes["wght"] = max(axis.minValue, min(wght, axis.maxValue))
                    else:
                        axes[axis.axisTag] = axis.defaultValue
                instantiateVariableFont(font, axes, inplace=True, overlap=0)

            # Scale UPM if needed
            if font["head"].unitsPerEm != 1000:
                scale_upem(font, 1000)

            # Convert CFF -> glyf
            if "CFF " in font:
                saved_tables = {t: font[t] for t in ("GSUB", "GPOS", "GDEF") if t in font}
                gs = font.getGlyphSet()
                go = font.getGlyphOrder()
                cm = font.getBestCmap()
                upm = font["head"].unitsPerEm
                fb = FontBuilder(upm, isTTF=True)
                fb.setupGlyphOrder(go)
                fb.setupCharacterMap(cm)
                gd: dict = {}
                mt: dict = {}
                for gn in go:
                    tp = TTGlyphPen(None)
                    cp = Cu2QuPen(tp, max_err=1.0, reverse_direction=True)
                    gs[gn].draw(cp)
                    gd[gn] = tp.glyph()
                    mt[gn] = (gs[gn].width, 0)
                fb.setupGlyf(gd)
                fb.setupHorizontalMetrics(mt)
                fb.setupHorizontalHeader(ascent=800, descent=-200)
                fb.setupNameTable({"familyName": "Engrish", "styleName": "Regular"})
                fb.setupOS2()
                fb.setupPost()
                fb.setupHead(unitsPerEm=upm)
                font = fb.font
                for t, v in saved_tables.items():
                    font[t] = v

            # Strip non-essential tables
            for tag in list(font.keys()):
                if tag not in KEEP:
                    del font[tag]

            tmp = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
            font.save(tmp.name)
            glyf_temps.append(tmp.name)
            font.close()

        if not glyf_temps:
            log.warning("No fonts to merge for %s", output_path)
            return

        # Merge
        merger = Merger()
        merged = merger.merge(glyf_temps)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged.save(str(output_path))
        merged.close()

        log.info("Built %s (%d bytes)", output_path.name, output_path.stat().st_size)

    finally:
        import os
        for f in glyf_temps:
            os.unlink(f)


def generate_fonts(locales: list[str], output_dir: Path, form: str = "") -> None:
    """Generate minimized font files for a dictionary form.

    Produces 4 font files in output_dir:
        engrish-regular.ttf     — all scripts, wght=400
        engrish-bold.ttf        — all scripts, wght=700
        engrish-italic.ttf      — italic-available scripts only, wght=400
        engrish-bold-italic.ttf — italic-available scripts only, wght=700
    """
    if not form:
        form = "+".join(locales)

    dict_cps = collect_codepoints(locales)
    if not dict_cps:
        raise RuntimeError(f"No codepoints collected for form '{form}'")
    log.info("Collected %d codepoints for form '%s'", len(dict_cps), form)

    # Ensure italic variants are available for fonts that publish them
    for fpath in sorted(FONTS_DIR.glob("*")):
        if fpath.suffix not in (".ttf", ".otf") or fpath.name.startswith("engrish"):
            continue
        if "-Italic" in fpath.name:
            continue
        stem = fpath.stem.split("[")[0].split("-")[0]
        if not _find_italic_file(stem, FONTS_DIR):
            # Only try downloading for stems likely to have italic (variable weight fonts)
            from fontTools.ttLib import TTFont
            f = TTFont(fpath)
            has_fvar = "fvar" in f
            f.close()
            if has_fvar:
                _download_italic(stem, FONTS_DIR)

    # Select fonts for upright variants (all scripts)
    upright_selected = _select_fonts(dict_cps, FONTS_DIR, italic=False)
    log.info("Selected %d fonts for upright variants", len(upright_selected))

    # Select fonts for italic variants (only fonts with italic files)
    italic_selected = _select_fonts(dict_cps, FONTS_DIR, italic=True)
    log.info("Selected %d fonts for italic variants", len(italic_selected))

    # Build all 4 variants
    variants = [
        ("engrish-regular.ttf", upright_selected, 400.0, False),
        ("engrish-bold.ttf", upright_selected, 700.0, False),
        ("engrish-italic.ttf", italic_selected, 400.0, True),
        ("engrish-bold-italic.ttf", italic_selected, 700.0, True),
    ]

    for filename, selected, wght, is_italic in variants:
        if not selected:
            log.warning("No fonts selected for %s, skipping", filename)
            continue
        out_path = output_dir / filename
        log.info("Building %s (wght=%.0f, %d fonts)...", filename, wght, len(selected))
        _build_merged_font(
            selected, wght, out_path,
            italic=is_italic, fonts_dir=FONTS_DIR,
        )

    log.info("Font generation complete for form '%s'", form)


def run_font(dicts: list[str] | None, *, all_dicts: bool = False) -> int:
    """CLI entry point for the 'font' subcommand."""
    import sys

    from .epub import _discover_all_dicts
    from .paths import engrish_form_dir

    if all_dicts:
        dicts = _discover_all_dicts()
        if not dicts:
            print("Error: no existing dictionaries found", file=sys.stderr)
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
        form_dir = engrish_form_dir(form)
        log.info("Generating fonts: %s -> %s", form, form_dir)
        try:
            generate_fonts(locales, form_dir, form=form)
        except Exception as exc:
            log.error("Font generation failed for '%s': %s", form, exc)

    return 0
