"""add-language: add languages to engrish.json from the EN Wiktionary dump."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .paths import get_sqlite_path
from .stats import load_language_codes

log = logging.getLogger(__name__)

_ENGRISH_JSON = Path(__file__).parent / "engrish.json"


def _load_config() -> dict[str, dict[str, str]]:
    if _ENGRISH_JSON.exists():
        return json.loads(_ENGRISH_JSON.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg: dict[str, dict[str, str]]) -> None:
    _ENGRISH_JSON.write_text(
        json.dumps(cfg, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(langs: list[str] | None, *, all_langs: bool = False) -> int:
    """Resolve language codes from the dump and add them to engrish.json."""
    from wikidict import download, parse

    log.info("Ensuring EN Wiktionary dump is downloaded and parsed...")
    download.main("en")
    parse.main("en")

    db_path = get_sqlite_path()
    name_to_code = load_language_codes(db_path)
    code_to_name = {code: name for name, code in name_to_code.items()}

    cfg = _load_config()

    if all_langs:
        langs = sorted(code_to_name.keys())
        log.info("Adding all %d languages", len(langs))

    # Validate all codes before making any changes
    errors: list[str] = []
    to_add: list[tuple[str, str]] = []
    for code in langs:
        if code == "en":
            log.info("Skipping 'en' — always present as the base language")
            continue
        if code in cfg:
            log.info("Skipping '%s' — already configured", code)
            continue
        if code not in code_to_name:
            errors.append(f"Unknown language code '{code}'. Run 'language-stats' to see available codes.")
            continue
        to_add.append((code, code_to_name[code]))

    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    if not to_add:
        log.info("Nothing to add")
        return 0

    for code, name in to_add:
        cfg[code] = {
            "wiktionary_section": name.lower(),
            "display_name": name,
        }
        log.info("Added: %s (%s)", code, name)

    _save_config(cfg)
    log.info("Updated %s — %d language(s) added", _ENGRISH_JSON, len(to_add))

    return 0
