"""epubcheck wrapper — EPUB 2 validity oracle (M7).

Install: pin a JAR release from https://github.com/w3c/epubcheck/releases (requires JRE),
or ``apt install epubcheck`` / ``npm install -g epubcheck`` (both wrap the same JAR).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TOOL = "epubcheck"
MILESTONE = "M7"


def is_installed() -> bool:
    return shutil.which(TOOL) is not None


def _ensure_installed() -> None:
    if not is_installed():
        raise RuntimeError(
            f"{TOOL} not installed; see {MILESTONE} in task-round-4-execution-plan "
            f"(pin a release JAR from https://github.com/w3c/epubcheck/releases)."
        )


def version() -> str:
    _ensure_installed()
    out = subprocess.run([TOOL, "--version"], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def validate(epub_path: Path | str) -> tuple[int, str]:
    _ensure_installed()
    result = subprocess.run(
        [TOOL, "--mode", "epub2", str(epub_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr
