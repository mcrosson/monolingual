"""fontTools inspection helper — font-correctness oracle primitives (M8).

fontTools is pure Python and already installed in the project venv, so there
is no install gate; these functions are available from M0 onward.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont


def describe(ttf_path: Path | str) -> dict[str, Any]:
    """Summarize a TTF's cmap, glyph count, and name-table entries.

    Returns a dict with keys:
      - ``cmap``: ``set[int]`` — codepoints in the best-cmap.
      - ``glyph_count``: ``int`` — from the ``maxp`` table.
      - ``name_table``: ``dict[int, str]`` — ``nameID`` → string (first decodable record wins).
    """
    font = TTFont(str(ttf_path))
    cmap: set[int] = set(font.getBestCmap().keys())
    glyph_count: int = font["maxp"].numGlyphs
    name_table: dict[int, str] = {}
    for record in font["name"].names:
        name_table.setdefault(record.nameID, record.toUnicode())
    return {"cmap": cmap, "glyph_count": glyph_count, "name_table": name_table}
