"""Pure-data constants for the engrish rewrite.

Values are sourced from ``engrish.json`` via ``engrish.config`` (which loads +
validates the file on import). This module exposes a stable, no-I/O surface
for consumers that only need the data — the heavy lifting lives in
``engrish.config``.

Per ``[[task-round-4-execution-plan]]`` M3 scope.
"""

from __future__ import annotations

from engrish.config import EPUB_BASE_FONTS, FORM_NAMES

DEFAULT_FONTS: list[str] = list(EPUB_BASE_FONTS)

__all__ = ["DEFAULT_FONTS", "FORM_NAMES"]
