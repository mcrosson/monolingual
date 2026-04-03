"""Configuration loaded from engrish.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

_ENGRISH_JSON = Path(__file__).parent.parent / "engrish.json"
_ENGRISH_CFG: dict[str, dict[str, str]] = (
    json.loads(_ENGRISH_JSON.read_text(encoding="utf-8")) if _ENGRISH_JSON.exists() else {}
)

# All known locale codes: "en" (always present) + everything in the config
ALL_LOCALES = ["en"] + list(_ENGRISH_CFG)

# Human-readable names for each locale
FORM_NAMES: dict[str, str] = {"en": "Modern English"}
FORM_NAMES.update({code: cfg["display_name"] for code, cfg in _ENGRISH_CFG.items()})

# Display order: en first, then config order
DISPLAY_ORDER = list(ALL_LOCALES)

DATA_DIR = Path(os.getenv("CWD", "")) / "data"
ENGRISH_DIR = DATA_DIR / "engrish"
FONTS_DIR = Path(__file__).parent.parent / "fonts" / "Charis-7.000" / "web"
