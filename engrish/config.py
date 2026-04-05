"""Configuration loaded from engrish.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

_ENGRISH_JSON = Path(__file__).parent / "engrish.json"
_ENGRISH_RAW: dict = (
    json.loads(_ENGRISH_JSON.read_text(encoding="utf-8")) if _ENGRISH_JSON.exists() else {}
)
_ENGRISH_CFG: dict[str, dict[str, str]] = _ENGRISH_RAW.get("languages", {})
EPUB_BASE_FONTS: list[str] = _ENGRISH_RAW.get("epub_base_fonts", [])
SEED_FONTS: list[str] = _ENGRISH_RAW.get("seed_fonts", [])

# All known locale codes — driven entirely by the config
ALL_LOCALES = list(_ENGRISH_CFG)

# Human-readable names for each locale
FORM_NAMES: dict[str, str] = {code: cfg["display_name"] for code, cfg in _ENGRISH_CFG.items()}

DATA_DIR = Path(os.getenv("CWD", "")) / "data"
ENGRISH_DIR = DATA_DIR / "engrish"
FONTS_DIR = Path(__file__).parent.parent / "fonts" / "Noto"
