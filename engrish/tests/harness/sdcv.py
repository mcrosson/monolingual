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
    """Run ``sdcv -n -e`` for ``word`` against ``dict_dir`` and return stdout.

    Implementation notes:
    - ``--`` separates options from positional args so headwords beginning
      with ``-`` (e.g. Gothic prefix entries like ``-areis``) aren't
      misparsed as sdcv flags.
    - ``LANG=C.UTF-8`` / ``LC_ALL=C.UTF-8`` ensures sdcv's iconv path
      handles non-Latin / SMP-plane (e.g. Gothic 𐌰..) headwords. Without
      this, the default POSIX locale rejects multi-byte UTF-8 with
      "Invalid byte sequence in conversion input".
    """
    import os

    _ensure_installed()
    env = dict(os.environ)
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    out = subprocess.run(
        [TOOL, "-n", "-e", "--data-dir", str(dict_dir), "--", word],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return out.stdout
