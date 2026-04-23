"""engrish.json config loader + schema validation.

Backward-compat re-exports from ``engrish.config_legacy`` keep existing callers
(``engrish.__main___legacy``, ``engrish/paths.py``, ``engrish/epub.py``, etc.)
working until M11 deletes the legacy tier. New M3+ code imports pure data from
``engrish.constants`` and validation entry points from here.

Per ``[[task-round-4-execution-plan]]`` M3-AC4.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engrish.config_legacy import (  # noqa: F401 — backward-compat re-exports
    ALL_LOCALES,
    DATA_DIR,
    ENGRISH_DIR,
    ENGRISH_JSON_PATH,
    EPUB_BASE_FONTS,
    FONTS_DIR,
    FORM_NAMES,
    L2_HEADING_PATTERN,
    SEED_FONTS,
    _ENGRISH_CFG,
    _ENGRISH_RAW,
    _REPO_ROOT,
)

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
