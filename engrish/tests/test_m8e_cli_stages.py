"""M8-E regression tests: CLI wiring + stage orchestrator + update-fonts."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# --- M8-AC1: CLI exposes both subcommands wired to real handlers ---


def test_cli_font_and_update_fonts_no_longer_stubs() -> None:
    """Neither ``font`` nor ``update-fonts`` should raise NotImplementedError now."""
    from engrish.cli import main as cli_main

    # We can't actually invoke them end-to-end (would download / build); just
    # verify they're not the stub handlers. Inspecting the parser confirms.
    from engrish.cli import build_parser

    parser = build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.dest == "command"]
    assert len(actions) == 1
    sub_choices = actions[0].choices
    # Both subcommands should resolve to a parser whose `func` defaults are not
    # the _not_implemented stubs.
    from engrish.cli import _cmd_font, _cmd_update_fonts

    font_func = sub_choices["font"].get_default("func")
    update_func = sub_choices["update-fonts"].get_default("func")
    assert font_func is _cmd_font, "font subcommand not wired to _cmd_font"
    assert update_func is _cmd_update_fonts, "update-fonts not wired to _cmd_update_fonts"


# --- M8-AC10: update-fonts idempotent when all stems present ---


def test_update_fonts_idempotent_no_op_when_all_stems_present() -> None:
    """If existing_stems returns the full needed set, run() returns 0
    without touching download_font."""
    from engrish.stages.update_fonts_stage import _needed_stems, run

    needed = _needed_stems()
    with (
        mock.patch(
            "engrish.stages.update_fonts_stage.existing_stems", return_value=needed
        ),
        mock.patch(
            "engrish.stages.update_fonts_stage.download_font"
        ) as mock_download,
    ):
        rc = run()
    assert rc == 0
    assert mock_download.call_count == 0


def test_update_fonts_calls_download_for_missing_stems() -> None:
    """A missing stem should trigger one download_font call per missing stem."""
    from engrish.stages.update_fonts_stage import _needed_stems, run

    needed = _needed_stems()
    # Pretend exactly two stems are missing.
    missing_two = sorted(needed)[:2]
    present = needed - set(missing_two)
    with (
        mock.patch(
            "engrish.stages.update_fonts_stage.existing_stems", return_value=present
        ),
        mock.patch(
            "engrish.stages.update_fonts_stage.download_font", return_value=True
        ) as mock_download,
    ):
        rc = run()
    assert rc == 0
    assert mock_download.call_count == len(missing_two)


def test_update_fonts_returns_nonzero_on_download_failure() -> None:
    from engrish.stages.update_fonts_stage import _needed_stems, run

    needed = _needed_stems()
    missing = sorted(needed)[:1]
    present = needed - set(missing)
    with (
        mock.patch(
            "engrish.stages.update_fonts_stage.existing_stems", return_value=present
        ),
        mock.patch(
            "engrish.stages.update_fonts_stage.download_font", return_value=False
        ),
    ):
        rc = run()
    assert rc == 1


# --- M8-AC12: D29 recursion (font → generate when .df missing) ---


def test_font_stage_recurses_into_generate_via_require_artifact(tmp_path, monkeypatch) -> None:
    """When the merged .df is missing, font stage should invoke generate via
    require_artifact's producer hook."""
    from engrish.stages import font_stage

    # Patch the heavy internals: the recursion path is what we're testing.
    fake_df = tmp_path / "form.df"

    def _fake_get_snapshot_date(_locales):
        return "20260401"

    def _fake_engrish_form_dir(_form):
        return tmp_path

    def _fake_dict_base_name(_form, _date, noetym=False):
        return "form"

    generate_calls: list[str] = []

    def _fake_generate(form):
        generate_calls.append(form)
        fake_df.write_bytes(b"@ stub\n")  # produce the artifact so require_artifact returns

    fake_outputs: dict[str, Path] = {}

    def _fake_build(**kwargs):
        return fake_outputs

    monkeypatch.setattr(font_stage, "get_snapshot_date", _fake_get_snapshot_date)
    monkeypatch.setattr(font_stage, "engrish_form_dir", _fake_engrish_form_dir)
    monkeypatch.setattr(font_stage, "dict_base_name", _fake_dict_base_name)
    monkeypatch.setattr(font_stage, "build_form_fonts", _fake_build)
    # Patch the lazily-imported generate.run.
    fake_generate_module = mock.MagicMock()
    fake_generate_module.run = _fake_generate
    monkeypatch.setitem(__import__("sys").modules, "engrish.stages.generate", fake_generate_module)

    rc = font_stage.run("got")
    assert rc == 0
    assert generate_calls == ["got"], f"expected exactly one generate('got') invocation, got {generate_calls}"
