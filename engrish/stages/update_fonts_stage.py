"""``engrish update-fonts`` — refresh the local Noto source-font tree.

Reads every ``fonts`` list from ``engrish.json``'s per-language entries plus
the top-level ``seed_fonts``, computes the union of stem names needed,
checks which already exist on disk, and downloads only the missing ones.

Idempotent (M8-AC10): a second invocation when all stems are present is a
no-op (no network calls, no file writes).

I/O helpers (download, existing-stems, candidate-urls, etc.) live in
``engrish.font_io``; shared with ``engrish.stages.add_language_stage``.
"""

from __future__ import annotations

import logging

from engrish.config import FONTS_DIR, SEED_FONTS, _ENGRISH_CFG
from engrish.font_io import download_font, existing_stems

log = logging.getLogger(__name__)


def _needed_stems() -> set[str]:
    """Union of stems across SEED_FONTS + every locale's ``fonts`` list."""
    needed: set[str] = set(SEED_FONTS)
    for cfg in _ENGRISH_CFG.values():
        needed.update(cfg.get("fonts", []))
    return needed


def run() -> int:
    """Entry: download missing source fonts. Returns 0 on success, 1 if any download fails."""
    needed = _needed_stems()
    if not needed:
        log.info("update-fonts: no fonts referenced in engrish.json; nothing to do")
        return 0

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    present = existing_stems(FONTS_DIR)
    missing = sorted(needed - present)

    if not missing:
        log.info("update-fonts: all %d referenced fonts already present (no-op)", len(needed))
        return 0

    log.info("update-fonts: %d font(s) missing: %s", len(missing), ", ".join(missing))
    failed: list[str] = []
    for stem in missing:
        if not download_font(stem, FONTS_DIR):
            log.error("update-fonts: could not download %s", stem)
            failed.append(stem)

    if failed:
        log.error(
            "update-fonts: %d/%d download(s) failed: %s",
            len(failed), len(missing), ", ".join(failed),
        )
        return 1

    log.info("update-fonts: downloaded %d new font(s)", len(missing))
    return 0
