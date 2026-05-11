"""M3 regression tests for engrish CLI scaffold + config schema + pipeline helper.

Covers M3-AC1 (CLI surface), M3-AC3 (NotImplementedError stubs), M3-AC4 (schema
validation), M3-AC5 (``require_artifact`` helper). M3-AC2 (add-language config
mutation) is deferred to M11-AC3 because it requires an initialized Wiktionary
SQLite dump fixture to resolve language codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engrish.cli import build_parser
from engrish.config import (
    ConfigValidationError,
    ENGRISH_JSON_PATH,
    load_config,
    validate_config,
)
from engrish.constants import DEFAULT_FONTS, FORM_NAMES
from engrish.pipeline import require_artifact


# --- M3-AC1: CLI exposes exactly 7 subcommands ---

_EXPECTED_SUBCOMMANDS = frozenset({
    "prepare",
    "language-stats",
    "add-language",
    "generate",
    "epub",
    "font",
    "update-fonts",
})


def test_cli_exposes_expected_seven_subcommands() -> None:
    parser = build_parser()
    # argparse stores subparsers in a subparser action; extract choices.
    subparsers_actions = [
        a for a in parser._actions if hasattr(a, "choices") and a.dest == "command"
    ]
    assert len(subparsers_actions) == 1, "expected exactly one subparsers action"
    actual = set(subparsers_actions[0].choices.keys())
    assert actual == _EXPECTED_SUBCOMMANDS, (
        f"CLI subcommand surface drift: expected {_EXPECTED_SUBCOMMANDS}, got {actual}"
    )


# --- M3-AC3: structural guard — no subcommand handler is stubbed ---


def test_no_subcommand_handler_is_stubbed_with_not_implemented_error() -> None:
    """M3-AC3 structural guard — every CLI subcommand has a real handler.

    Originally a parametrize over still-stubbed subcommand names, asserting each
    raised ``NotImplementedError`` with a milestone pointer. All 6 non-add-language
    handlers landed by M8 close (2026-04-25), so the parametrize list emptied.
    Replaced with a positive assertion: scan each subcommand handler's source for
    ``raise NotImplementedError`` — the list of stubbed handlers must remain empty.
    """
    import inspect
    from argparse import _SubParsersAction

    from engrish.cli import build_parser

    parser = build_parser()
    subaction = next(
        a for a in parser._actions if isinstance(a, _SubParsersAction)
    )
    stubbed: list[str] = []
    for sub_name, sub_parser in subaction.choices.items():
        handler = sub_parser.get_default("func")
        assert handler is not None, f"subcommand {sub_name!r} has no handler"
        src = inspect.getsource(handler)
        if "raise NotImplementedError" in src:
            stubbed.append(sub_name)
    assert not stubbed, (
        f"M3-AC3 regression — subcommand handler(s) still stubbed: {stubbed}. "
        "Every subcommand must have a real implementation by M11 close per D26."
    )


# --- M3-AC4: schema validation rejects malformed, accepts well-formed ---


def test_validate_config_accepts_live_engrish_json() -> None:
    """The committed engrish.json must validate."""
    data = json.loads(ENGRISH_JSON_PATH.read_text(encoding="utf-8"))
    validate_config(data)  # should not raise


def test_load_config_matches_live_engrish_json() -> None:
    """``load_config()`` reads + validates the committed engrish.json end-to-end."""
    data = load_config()
    assert "languages" in data
    assert isinstance(data["languages"], dict)
    assert "en" in data["languages"]


def test_validate_config_rejects_non_dict_root() -> None:
    with pytest.raises(ConfigValidationError, match="root must be a dict"):
        validate_config([])


def test_validate_config_rejects_missing_top_level_key() -> None:
    with pytest.raises(ConfigValidationError, match="missing top-level keys"):
        validate_config({"languages": {}})


def test_validate_config_rejects_non_dict_language_entry() -> None:
    with pytest.raises(ConfigValidationError, match="must be a dict"):
        validate_config(
            {
                "languages": {"xx": "not-a-dict"},
                "epub_base_fonts": [],
                "seed_fonts": [],
            }
        )


def test_validate_config_rejects_language_missing_required_keys() -> None:
    with pytest.raises(ConfigValidationError, match="missing keys"):
        validate_config(
            {
                "languages": {"xx": {"wiktionary_section": "x"}},
                "epub_base_fonts": [],
                "seed_fonts": [],
            }
        )


def test_validate_config_rejects_language_fonts_not_list_of_str() -> None:
    with pytest.raises(ConfigValidationError, match="fonts must be a list of str"):
        validate_config(
            {
                "languages": {
                    "xx": {
                        "wiktionary_section": "x",
                        "display_name": "X",
                        "fonts": "not-a-list",
                    }
                },
                "epub_base_fonts": [],
                "seed_fonts": [],
            }
        )


def test_validate_config_rejects_non_list_epub_base_fonts() -> None:
    with pytest.raises(ConfigValidationError, match="epub_base_fonts"):
        validate_config(
            {
                "languages": {},
                "epub_base_fonts": "not-a-list",
                "seed_fonts": [],
            }
        )


# --- M3-AC5: require_artifact invokes producer only when path is absent ---


def test_require_artifact_skips_producer_when_path_exists(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("hello")
    called = []

    def producer() -> None:
        called.append(True)

    result = require_artifact(path, producer)
    assert result == path
    assert called == []


def test_require_artifact_invokes_producer_when_path_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    called = []

    def producer() -> None:
        called.append(True)
        path.write_text("produced")

    result = require_artifact(path, producer)
    assert result == path
    assert called == [True]
    assert path.read_text() == "produced"


def test_require_artifact_raises_if_producer_fails_to_create_path(tmp_path: Path) -> None:
    path = tmp_path / "still-missing.txt"

    def producer() -> None:
        pass  # does not create the file

    with pytest.raises(RuntimeError, match="did not create required artifact"):
        require_artifact(path, producer)


# --- M3 constants surface ---


def test_constants_module_exposes_form_names_and_default_fonts() -> None:
    assert isinstance(FORM_NAMES, dict)
    assert "en" in FORM_NAMES
    assert isinstance(DEFAULT_FONTS, list)
    assert DEFAULT_FONTS  # non-empty
    assert all(isinstance(f, str) for f in DEFAULT_FONTS)
