"""add-language: add languages to engrish.json from the EN Wiktionary dump."""

from __future__ import annotations

import json
import logging
import sys

from .config import ENGRISH_JSON_PATH
from .stats import load_language_codes

log = logging.getLogger(__name__)


def _load_config() -> dict:
    if ENGRISH_JSON_PATH.exists():
        return json.loads(ENGRISH_JSON_PATH.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg: dict) -> None:
    ENGRISH_JSON_PATH.write_text(
        json.dumps(cfg, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(langs: list[str] | None, *, all_langs: bool = False) -> int:
    """Resolve language codes from the dump and add them to engrish.json."""
    from .pipeline import ensure_wikidict_parsed

    db_path = ensure_wikidict_parsed()
    name_to_code = load_language_codes(db_path)
    code_to_name = {code: name for name, code in name_to_code.items()}

    cfg = _load_config()
    languages = cfg.setdefault("languages", {})

    if all_langs:
        langs = sorted(code_to_name.keys())
        log.info("Adding all %d languages", len(langs))

    # Validate all codes before making any changes
    errors: list[str] = []
    to_add: list[tuple[str, str]] = []
    for code in langs:
        if code in languages:
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

    from .font import collect_headword_chars_batch, detect_fonts

    # Single parallel scan of the dump for ALL languages being added.
    codes_to_add = [code for code, _ in to_add]
    ws_map = {code: name.lower() for code, name in to_add}
    log.info("Scanning dump for %d language(s)...", len(codes_to_add))
    all_chars = collect_headword_chars_batch(codes_to_add, db_path, wiktionary_sections=ws_map)

    for code, name in to_add:
        log.info("Detecting fonts for %s (%s)...", code, name)
        fonts = detect_fonts(code, db_path, chars=all_chars[code], wiktionary_section=name.lower())
        languages[code] = {
            "wiktionary_section": name.lower(),
            "display_name": name,
            "fonts": fonts,
        }
        log.info("Added: %s (%s) — fonts: %s", code, name, ", ".join(fonts))

    _save_config(cfg)
    log.info("Updated %s — %d language(s) added", ENGRISH_JSON_PATH, len(to_add))

    return 0
