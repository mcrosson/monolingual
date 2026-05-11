"""M11-AC2 — Per-subcommand `--help` byte-equal goldens.

For each of the 7 subcommands plus the root parser, the live `--help` output
must byte-equal the committed golden under engrish/tests/goldens/cli/. Catches
silent CLI surface drift (renamed flags, removed metavars, changed help text).

To refresh after an intentional CLI change:
  COLUMNS=80 ./venv/bin/python -m engrish <sub> --help > engrish/tests/goldens/cli/<sub>.txt
  COLUMNS=80 ./venv/bin/python -m engrish --help > engrish/tests/goldens/cli/_root.txt

Then commit the updated goldens with a rationale that explains the surface change.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDENS_DIR = Path(__file__).resolve().parent / "goldens" / "cli"
PYTHON = REPO_ROOT / "venv" / "bin" / "python"
SUBCOMMANDS = ("prepare", "language-stats", "add-language", "generate", "epub", "font", "update-fonts")


def _help_output(args: list[str]) -> str:
    """Run `python -m engrish [args] --help` with fixed COLUMNS for stable wrapping."""
    env = {**os.environ, "COLUMNS": "80"}
    result = subprocess.run(
        [str(PYTHON), "-m", "engrish", *args, "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def test_goldens_dir_exists() -> None:
    assert GOLDENS_DIR.is_dir(), f"{GOLDENS_DIR} missing — run M11-AC2 capture"


def test_root_help_matches_golden() -> None:
    golden = (GOLDENS_DIR / "_root.txt").read_text(encoding="utf-8")
    actual = _help_output([])
    assert actual == golden, (
        "Root --help output drifted from golden.\n"
        "Refresh: COLUMNS=80 ./venv/bin/python -m engrish --help > engrish/tests/goldens/cli/_root.txt"
    )


@pytest.mark.parametrize("sub", SUBCOMMANDS)
def test_subcommand_help_matches_golden(sub: str) -> None:
    golden_path = GOLDENS_DIR / f"{sub}.txt"
    assert golden_path.exists(), f"golden missing: {golden_path}"
    golden = golden_path.read_text(encoding="utf-8")
    actual = _help_output([sub])
    assert actual == golden, (
        f"`engrish {sub} --help` drifted from golden.\n"
        f"Refresh: COLUMNS=80 ./venv/bin/python -m engrish {sub} --help "
        f"> engrish/tests/goldens/cli/{sub}.txt"
    )


def test_every_subcommand_has_a_golden() -> None:
    """Catches the case where the parser gains a subcommand and the goldens
    are not refreshed. Uses build_parser() to enumerate all subcommands and
    asserts a corresponding golden file exists."""
    from argparse import _SubParsersAction
    from engrish.cli import build_parser

    parser = build_parser()
    subs: list[str] = []
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            subs.extend(action.choices.keys())
    for sub in subs:
        assert (GOLDENS_DIR / f"{sub}.txt").exists(), (
            f"parser exposes subcommand `{sub}` but no golden exists at "
            f"{GOLDENS_DIR / (sub + '.txt')}; refresh M11-AC2 goldens"
        )
    # And SUBCOMMANDS in this test file must match parser exactly:
    assert set(subs) == set(SUBCOMMANDS), (
        f"SUBCOMMANDS in test_m11_cli_goldens.py is stale; "
        f"parser={sorted(subs)}, test={sorted(SUBCOMMANDS)}"
    )
