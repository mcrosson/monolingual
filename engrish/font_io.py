"""Font I/O helpers shared between ``update-fonts`` and ``add-language``.

Created at M11-AC4 (2026-05-02) by extracting the I/O-only helpers that both
``engrish.stages.update_fonts_stage`` and ``engrish.stages.add_language_stage``
need: stem-extraction from filenames, URL candidate enumeration, font-on-disk
lookup, font download, cmap loading, and the cached upstream-stem discovery.

Pre-M11 these lived in ``engrish/font_legacy.py``. The M8 generation rewrite
moved the heavy generation primitives to ``engrish/font.py`` but left the
I/O helpers in font_legacy. M11-AC4 splits them out cleanly: I/O here,
generation primitives in ``engrish.font``, per-subcommand orchestration in
``engrish.stages.*``.

Functions are intentionally module-level (not in a class) so they can be
imported and called directly. ``_cached_cmaps`` and ``_cached_stems`` are
process-local caches; ``clear_caches()`` resets them (used by tests).
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from fontTools.ttLib import TTFont

log = logging.getLogger(__name__)

_NOTO_BASE = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts"
_NOTO_API = "https://api.github.com/repos/notofonts/notofonts.github.io/contents/fonts"
_CJK_BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF"
_CJK_API = "https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/SubsetOTF"
_EMOJI_API = "https://api.github.com/repos/googlefonts/noto-emoji/contents/fonts"

_cached_cmaps: dict[str, set[int]] = {}
_cached_stems: tuple[set[str], set[str]] | None = None


def clear_caches() -> None:
    """Reset module-level caches. Used by tests."""
    global _cached_stems
    _cached_cmaps.clear()
    _cached_stems = None


def extract_font_stem(filename_stem: str) -> str:
    """Extract font family stem from filename, preserving style variants.

    Examples:
        NotoSans[wght] -> NotoSans
        NotoSans-Regular -> NotoSans
        NotoSans-Italic -> NotoSans-Italic (preserve style)
        NotoSans-BoldItalic -> NotoSans-BoldItalic (preserve style)
    """
    stem = filename_stem.split("[")[0]
    parts = stem.split("-")
    if len(parts) >= 2 and parts[-1] in ("Italic", "Bold", "BoldItalic"):
        return "-".join(parts[:-1] + [parts[-1]])
    return parts[0]


def existing_stems(fonts_dir: Path) -> set[str]:
    """Return the set of font stems already present on disk."""
    stems: set[str] = set()
    if fonts_dir.is_dir():
        for f in fonts_dir.iterdir():
            if f.suffix in (".ttf", ".otf"):
                stems.add(extract_font_stem(f.stem))
    return stems


def candidate_urls(stem: str) -> list[tuple[str, str]]:
    """Return (url, filename) candidates to try, in priority order.

    Tries all known Noto font repositories generically. For NotoSans* stems,
    also tries NotoSerif* as a fallback (some scripts only have Serif variants
    published). For a given stem like "NotoSansArabic", the noto-cjk URL will
    simply 404 and be skipped.
    """
    suffix = stem.removeprefix("NotoSans").removeprefix("NotoSerif")
    urls = [
        (
            f"{_NOTO_BASE}/{stem}/unhinted/slim-variable-ttf/{stem}%5Bwght%5D.ttf",
            f"{stem}[wght].ttf",
        ),
        (
            f"{_NOTO_BASE}/{stem}/full/ttf/{stem}-Regular.ttf",
            f"{stem}-Regular.ttf",
        ),
        (
            f"{_CJK_BASE}/{suffix}/{stem}-Regular.otf",
            f"{stem}-Regular.otf",
        ),
    ]
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
    return urls


def download_font(stem: str, fonts_dir: Path) -> bool:
    """Try to download a font for *stem*. Returns True on success."""
    for url, filename in candidate_urls(stem):
        log.info("Trying %s", url)
        try:
            resp = requests.get(url, timeout=60, stream=True)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            dest = fonts_dir / filename
            size = 0
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    size += len(chunk)
            log.info("Downloaded %s (%d bytes)", dest.name, size)
            return True
        log.debug("  %d — %s", resp.status_code, url)
    return False


def font_exists_remote(stem: str) -> bool:
    """Check whether a font for *stem* exists at any known download URL."""
    for url, _ in candidate_urls(stem):
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
    return False


def find_font_file(stem: str, fonts_dir: Path) -> Path | None:
    """Find the font file for a stem on disk, or None."""
    if not fonts_dir.is_dir():
        return None
    for f in fonts_dir.iterdir():
        if f.suffix not in (".ttf", ".otf"):
            continue
        if f.stem.split("[")[0].split("-")[0] == stem:
            return f
    return None


def ensure_font_on_disk(stem: str, fonts_dir: Path) -> Path | None:
    """Return the path to a font file for *stem*, downloading if needed."""
    existing = find_font_file(stem, fonts_dir)
    if existing:
        return existing
    fonts_dir.mkdir(parents=True, exist_ok=True)
    if download_font(stem, fonts_dir):
        return find_font_file(stem, fonts_dir)
    return None


def load_cmap(font_path: Path) -> set[int]:
    """Return the set of codepoints covered by a font file."""
    font = TTFont(font_path)
    cmap = font.getBestCmap()
    font.close()
    return set(cmap) if cmap else set()


def cmap_for_stem(stem: str, fonts_dir: Path) -> set[int]:
    """Return the cmap for a font stem, loading and caching.

    Downloads the font if it is not already on disk.
    """
    if stem in _cached_cmaps:
        return _cached_cmaps[stem]
    cmap: set[int] = set()
    font_path = ensure_font_on_disk(stem, fonts_dir)
    if font_path:
        try:
            cmap = load_cmap(font_path)
        except Exception:
            log.debug("Could not read cmap from %s", font_path)
    _cached_cmaps[stem] = cmap
    return cmap


def discover_available_stems() -> tuple[set[str], set[str]]:
    """Query both Noto font repos for all available font stems.

    Returns ``(all_stems, cjk_stems)`` where *cjk_stems* is the subset that
    came from the noto-cjk repo (region-based naming that can't be derived
    from Unicode character names). Includes both NotoSans and NotoSerif
    stems since some scripts only have Serif variants.

    Results are cached for the lifetime of the process so that adding
    multiple languages in one invocation only makes two API calls total.
    """
    global _cached_stems
    if _cached_stems is not None:
        return _cached_stems

    main_stems: set[str] = set()
    cjk_stems: set[str] = set()
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
    _cached_stems = (main_stems | cjk_stems, cjk_stems)
    return _cached_stems
