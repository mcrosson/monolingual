"""Configuration loaded from engrish.json."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ENGRISH_JSON_PATH = Path(__file__).parent / "engrish.json"

# Load config with error handling
try:
    _ENGRISH_RAW: dict = (
        json.loads(ENGRISH_JSON_PATH.read_text(encoding="utf-8"))
        if ENGRISH_JSON_PATH.exists()
        else {}
    )
except (json.JSONDecodeError, PermissionError) as e:
    print(f"Error loading engrish.json: {e}", file=sys.stderr)
    _ENGRISH_RAW = {}

_ENGRISH_CFG: dict[str, dict[str, str]] = _ENGRISH_RAW.get("languages", {})
EPUB_BASE_FONTS: list[str] = _ENGRISH_RAW.get("epub_base_fonts", [])
SEED_FONTS: list[str] = _ENGRISH_RAW.get("seed_fonts", [])

# All known locale codes — driven entirely by the config (immutable)
ALL_LOCALES: tuple[str, ...] = tuple(_ENGRISH_CFG)

# Human-readable names for each locale
FORM_NAMES: dict[str, str] = {code: cfg["display_name"] for code, cfg in _ENGRISH_CFG.items()}

# Data directories — use repo root as fallback when CWD not set
_REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv("CWD") or _REPO_ROOT) / "data"
ENGRISH_DIR = DATA_DIR / "engrish"
FONTS_DIR = _REPO_ROOT / "fonts" / "Noto"

# Shared regex patterns
L2_HEADING_PATTERN = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.MULTILINE)
