"""update-fonts: download missing Noto Sans fonts referenced in engrish.json."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from .config import FONTS_DIR, _ENGRISH_CFG

log = logging.getLogger(__name__)

# GitHub raw base URLs
_NOTO_BASE = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts"
_CJK_BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF"

# CJK language suffixes → subdirectory in the noto-cjk repo
_CJK_SUFFIXES: dict[str, str] = {
    "JP": "JP",
    "SC": "SC",
    "KR": "KR",
    "TC": "TC",
    "HK": "HK",
}


def _existing_stems(fonts_dir: Path) -> set[str]:
    """Return the set of font stems already present on disk."""
    stems: set[str] = set()
    if fonts_dir.is_dir():
        for f in fonts_dir.iterdir():
            if f.suffix in (".ttf", ".otf"):
                stems.add(f.stem.split("[")[0].split("-")[0])
    return stems


def _cjk_lang(stem: str) -> str | None:
    """If *stem* is a CJK font (e.g. NotoSansJP), return the lang code."""
    for suffix, lang in _CJK_SUFFIXES.items():
        if stem == f"NotoSans{suffix}":
            return lang
    return None


def _candidate_urls(stem: str) -> list[tuple[str, str]]:
    """Return (url, filename) candidates to try, in priority order."""
    cjk = _cjk_lang(stem)
    if cjk is not None:
        filename = f"{stem}-Regular.otf"
        return [(f"{_CJK_BASE}/{cjk}/{filename}", filename)]

    # Non-CJK: try variable font first, then static
    return [
        (
            f"{_NOTO_BASE}/{stem}/unhinted/slim-variable-ttf/{stem}%5Bwght%5D.ttf",
            f"{stem}[wght].ttf",
        ),
        (
            f"{_NOTO_BASE}/{stem}/full/ttf/{stem}-Regular.ttf",
            f"{stem}-Regular.ttf",
        ),
    ]


def _download_font(stem: str, fonts_dir: Path) -> bool:
    """Try to download a font for *stem*. Returns True on success."""
    candidates = _candidate_urls(stem)
    for url, filename in candidates:
        log.info("Trying %s", url)
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            dest = fonts_dir / filename
            dest.write_bytes(resp.content)
            log.info("Downloaded %s (%d bytes)", dest.name, len(resp.content))
            return True
        log.debug("  %d — %s", resp.status_code, url)
    return False


def run() -> int:
    """Download any fonts referenced in engrish.json but missing from disk."""
    # Collect every font stem referenced in the config
    needed: set[str] = set()
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
