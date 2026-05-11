"""M11-AC3 — engrish.sh integration: end-to-end run produces a complete form.

Runs `./engrish.sh --only <locale>` and asserts the per-form output set is
present and well-formed. Cost-gated by `ENGRISH_RUN_M11_INTEGRATION=1` because
it executes the real pipeline (generate + epub + font) for one locale.

Per D27, engrish.sh is the top-level shell orchestrator. This test verifies
that engrish.sh's per-form `build_form` function correctly wraps the new CLI
(per D26) and produces the same artifact set the per-subcommand tests
(M6/M7/M8) verify in isolation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENGRISH_SH = REPO_ROOT / "engrish.sh"
SMALL_LOCALE = "got"  # ~76 MB peak per M10; M9 golden corpus member


def _form_dir(form: str) -> Path:
    """engrish.paths.engrish_form_dir convention: + → -."""
    return REPO_ROOT / "data" / "engrish" / form.replace("+", "-")


def test_engrish_sh_executable() -> None:
    """Always-on sanity: engrish.sh exists and is shell-syntax-valid."""
    assert ENGRISH_SH.exists(), f"{ENGRISH_SH} missing"
    result = subprocess.run(
        ["bash", "-n", str(ENGRISH_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"engrish.sh syntax error: {result.stderr}"


def test_engrish_sh_only_form_produces_complete_artifact_set() -> None:
    """M11-AC3 — single-form integration via engrish.sh --only <locale>.

    M13-AC13 (2026-05-09): cost gate removed. Default invocation runs the
    full ./engrish.sh --only got pipeline.

    Verifies:
    - engrish.sh exits 0 for `--only <locale>`
    - data/engrish/<locale>/ contains the full StarDict + EPUB + font set
    - Output sizes are non-trivial (not empty / placeholder files)
    """
    form_dir = _form_dir(SMALL_LOCALE)
    # Force a fresh build by removing any prior per-form output.
    if form_dir.exists():
        import shutil
        shutil.rmtree(form_dir)

    result = subprocess.run(
        [str(ENGRISH_SH), "--only", SMALL_LOCALE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"engrish.sh --only {SMALL_LOCALE} exited {result.returncode}\n"
        f"--- stderr (last 30 lines) ---\n"
        + "\n".join(result.stderr.splitlines()[-30:])
    )

    # StarDict file set (M6 contract)
    files = sorted(form_dir.glob("*"))
    file_names = {f.name for f in files}
    expected_stardict_suffixes = {".df", ".dict", ".dict.dz", ".ifo", ".idx", ".idx.oft", ".syn", ".syn.oft"}
    found_suffixes = {("." + f.name.split(".", 1)[1]) for f in files if f.is_file() and "." in f.name}
    missing = expected_stardict_suffixes - found_suffixes
    assert not missing, f"StarDict files missing under {form_dir}: {missing}; found={file_names}"

    # EPUB file (M7 contract)
    epub_files = list(form_dir.glob("*.epub"))
    assert len(epub_files) == 1, f"expected 1 .epub in {form_dir}, got {len(epub_files)}"
    assert epub_files[0].stat().st_size > 1024, f"EPUB suspiciously small: {epub_files[0].stat().st_size} bytes"

    # Font set (M8 contract): 4 TTFs + coverage_gaps.txt under fonts/
    fonts_dir = form_dir / "fonts"
    assert fonts_dir.is_dir(), f"{fonts_dir} missing — font stage didn't run"
    expected_fonts = {"Regular.ttf", "Bold.ttf", "Italic.ttf", "BoldItalic.ttf", "coverage_gaps.txt"}
    found_fonts = set(os.listdir(fonts_dir))
    missing_fonts = expected_fonts - found_fonts
    assert not missing_fonts, f"font artifacts missing under {fonts_dir}: {missing_fonts}; found={found_fonts}"
    for ttf in ("Regular.ttf", "Bold.ttf", "Italic.ttf", "BoldItalic.ttf"):
        assert (fonts_dir / ttf).stat().st_size > 1024, f"{ttf} suspiciously small"
