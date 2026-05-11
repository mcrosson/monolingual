"""engrish.json config loader + schema validation + module-level data.

Owns both the runtime configuration constants (loaded once at import time
from ``engrish/engrish.json``) and the validation helpers (M3-AC4 schema).

The constants block was inlined from the deleted ``engrish/config_legacy.py``
at M11-AC4 (2026-05-02). Per ``[[task-round-4-execution-plan]]`` M3-AC4.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level data (was engrish.config_legacy)
# ---------------------------------------------------------------------------

ENGRISH_JSON_PATH = Path(__file__).parent / "engrish.json"

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


# ---------------------------------------------------------------------------
# Validation (M3-AC4)
# ---------------------------------------------------------------------------

_REQUIRED_TOP_LEVEL_KEYS = frozenset({"languages", "epub_base_fonts", "seed_fonts"})
_REQUIRED_LANGUAGE_KEYS = frozenset({"wiktionary_section", "display_name", "fonts"})


class ConfigValidationError(ValueError):
    """Raised when engrish.json does not satisfy the documented schema."""


def validate_config(data: Any) -> None:
    """Validate engrish.json structure. Raises ``ConfigValidationError`` on failure.

    Checks (M3-AC4):
    - Root is a dict.
    - Required top-level keys: ``languages``, ``epub_base_fonts``, ``seed_fonts``.
    - ``languages`` is a dict mapping locale code → per-language entry.
    - Every per-language entry carries ``wiktionary_section`` (str), ``display_name`` (str), ``fonts`` (list[str]).
    - ``epub_base_fonts`` and ``seed_fonts`` are each a list of str.
    """
    if not isinstance(data, dict):
        raise ConfigValidationError(f"root must be a dict, got {type(data).__name__}")
    missing = _REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing:
        raise ConfigValidationError(f"missing top-level keys: {sorted(missing)}")
    languages = data["languages"]
    if not isinstance(languages, dict):
        raise ConfigValidationError(f"'languages' must be a dict, got {type(languages).__name__}")
    for code, entry in languages.items():
        if not isinstance(code, str) or not code:
            raise ConfigValidationError(f"language code must be a non-empty str, got {code!r}")
        if not isinstance(entry, dict):
            raise ConfigValidationError(f"languages.{code} must be a dict")
        entry_missing = _REQUIRED_LANGUAGE_KEYS - entry.keys()
        if entry_missing:
            raise ConfigValidationError(f"languages.{code} missing keys: {sorted(entry_missing)}")
        for key in ("wiktionary_section", "display_name"):
            if not isinstance(entry[key], str):
                raise ConfigValidationError(f"languages.{code}.{key} must be str, got {type(entry[key]).__name__}")
        fonts = entry["fonts"]
        if not isinstance(fonts, list) or not all(isinstance(f, str) for f in fonts):
            raise ConfigValidationError(f"languages.{code}.fonts must be a list of str")
    for key in ("epub_base_fonts", "seed_fonts"):
        values = data[key]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ConfigValidationError(f"'{key}' must be a list of str")


def load_config(path: Path = ENGRISH_JSON_PATH) -> dict:
    """Load + validate engrish.json. Raises ``ConfigValidationError`` on failure."""
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_config(data)
    return data
