"""M9-AC3 — golden-fixture regression test.

For each of the 7 D35 locales, run the full pipeline (`generate` + `epub` +
`font`) into a temporary output dir and assert ``diff -r`` against the
committed ``engrish/tests/goldens/<locale>/`` is empty.

**M13-AC13 (2026-05-09):** cost gate removed. Default invocation runs every
test. Wall ~2m25s for the full 7-locale audit on warm cache.

See ``engrish/tests/GOLDENS.md`` for the refresh workflow when goldens
need legitimate regeneration.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
from pathlib import Path

import pytest

D35_LOCALES = ("got", "egy", "pi", "ang", "it", "ru", "grc")
WORKSPACE = Path(__file__).resolve().parent.parent.parent
GOLDENS_ROOT = WORKSPACE / "engrish" / "tests" / "goldens"
PY = WORKSPACE / "venv" / "bin" / "python"


def _diff_dirs(left: Path, right: Path) -> list[str]:
    """Recursive byte-equality diff, returning a human-readable failure list."""
    cmp = filecmp.dircmp(str(left), str(right))
    diffs: list[str] = []

    def walk(c: filecmp.dircmp, prefix: str = "") -> None:
        for name in c.left_only:
            diffs.append(f"only in goldens: {prefix}{name}")
        for name in c.right_only:
            diffs.append(f"only in pipeline output: {prefix}{name}")
        # diff_files is filecmp's shallow comparison (size + mtime); promote
        # to true byte-equality before reporting.
        for name in c.diff_files + c.funny_files:
            l = Path(c.left) / name
            r = Path(c.right) / name
            if not _bytes_equal(l, r):
                diffs.append(f"bytes differ: {prefix}{name}")
        for sub_name, sub in c.subdirs.items():
            walk(sub, f"{prefix}{sub_name}/")

    walk(cmp)
    return diffs


def _bytes_equal(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca = fa.read(65536)
            cb = fb.read(65536)
            if ca != cb:
                return False
            if not ca:
                return True


@pytest.mark.parametrize("locale", D35_LOCALES)
def test_pipeline_output_matches_golden(locale: str) -> None:
    """Full pipeline re-run for ``locale`` produces byte-identical artifacts to
    the committed golden set under ``engrish/tests/goldens/<locale>/``."""
    golden = GOLDENS_ROOT / locale
    if not golden.exists():
        pytest.fail(f"golden directory missing: {golden}")

    # Re-run pipeline into a fresh output dir. We force-clear the form's
    # data/engrish/<locale>/ first so D29's require_artifact does NOT no-op.
    form_out = WORKSPACE / "data" / "engrish" / locale
    if form_out.exists():
        shutil.rmtree(form_out)

    env = {**os.environ}
    for stage in ("generate", "epub", "font"):
        result = subprocess.run(
            [str(PY), "-m", "engrish", stage, "--form", locale],
            cwd=str(WORKSPACE),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min per stage cap; covers it/ru/grc worst case
        )
        if result.returncode != 0:
            pytest.fail(
                f"engrish {stage} --form {locale} failed (rc={result.returncode}):\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}\n"
            )

    diffs = _diff_dirs(golden, form_out)
    assert not diffs, (
        f"{locale}: {len(diffs)} byte-equality failures vs golden:\n  "
        + "\n  ".join(diffs[:25])
        + (f"\n  ... ({len(diffs) - 25} more)" if len(diffs) > 25 else "")
    )


def test_d35_corpus_locales_have_goldens() -> None:
    """Every D35 locale must have a populated ``engrish/tests/goldens/<locale>/`` dir.

    Sentinel test (always runs, no opt-in) — catches missing fixture commits
    before the cost-gated AC3 test would even attempt to diff.
    """
    missing = [loc for loc in D35_LOCALES if not (GOLDENS_ROOT / loc).is_dir()]
    assert not missing, f"missing golden dirs for D35 locales: {missing}"

    # Each locale dir must contain at minimum the StarDict + EPUB + 4 TTFs
    # (M9-AC2 explicit list).
    required_suffixes = (".df", ".ifo", ".idx", ".dict", ".dict.dz", ".epub")
    required_ttfs = ("Regular.ttf", "Bold.ttf", "Italic.ttf", "BoldItalic.ttf")
    for loc in D35_LOCALES:
        d = GOLDENS_ROOT / loc
        present = {p.name for p in d.iterdir()}
        missing_suffix = [
            s for s in required_suffixes
            if not any(name.endswith(s) for name in present)
        ]
        assert not missing_suffix, f"{loc}: missing files with suffixes {missing_suffix}"
        fonts_dir = d / "fonts"
        assert fonts_dir.is_dir(), f"{loc}: missing fonts/ subdir"
        font_present = {p.name for p in fonts_dir.iterdir()}
        missing_ttf = [t for t in required_ttfs if t not in font_present]
        assert not missing_ttf, f"{loc}: missing TTFs {missing_ttf}"
