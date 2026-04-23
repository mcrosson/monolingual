"""sdcv wrapper — StarDict programmatic oracle (M6).

Install: ``apt install sdcv`` (Debian/Ubuntu).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TOOL = "sdcv"
MILESTONE = "M6"


def is_installed() -> bool:
    return shutil.which(TOOL) is not None


def _ensure_installed() -> None:
    if not is_installed():
        raise RuntimeError(
            f"{TOOL} not installed; see {MILESTONE} in task-round-4-execution-plan "
            f"(install via `apt install sdcv`)."
        )


def version() -> str:
    _ensure_installed()
    out = subprocess.run([TOOL, "--version"], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def lookup(word: str, dict_dir: Path | str) -> str:
    _ensure_installed()
    out = subprocess.run(
        [TOOL, "-n", "--data-dir", str(dict_dir), word],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout
