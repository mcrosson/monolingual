"""Regenerate the wikidict upstream-parity baseline snapshot (M0-AC2).

Extracts the ``wikidict/`` subtree at merge-base commit ``433889e3`` into a
temporary directory via ``git archive | tar``, imports each audited module
from that tree in a subprocess with ``PYTHONPATH`` pointed at the extracted
tree and ``ENGRISH_MODE`` explicitly unset, and writes the resulting
attribute-surface snapshot to ``tests_new/baselines/wikidict_attrs_upstream.json``.

Usage::

    ./venv/bin/python tests_new/baselines/generate_wikidict_baseline.py

The output is deterministic: re-running should yield byte-identical JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASELINE_COMMIT = "433889e3"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = Path(__file__).resolve().parent / "wikidict_attrs_upstream.json"

# Kept in sync with tests_new/harness/parity.py::AUDITED_MODULES.
AUDITED_MODULES = (
    "wikidict",
    "wikidict.constants",
    "wikidict.convert",
    "wikidict.render",
    "wikidict.context",
    "wikidict.parse",
    "wikidict.namespaces",
    "wikidict.stubs",
    "wikidict.utils",
    "wikidict.lang",
    "wikidict.lang.defaults",
    "wikidict.lang.it",
    "wikidict.lang.ru",
    "wikidict.lang.ru.variant_handlers",
    "wikidict.lang.sv.variant_handlers",
)

SUBPROCESS_SCRIPT = r"""
import importlib
import json
import re
import sys

modules = {modules!r}

_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_MODULE_FROM_RE = re.compile(r"from '[^']*/([^/']+)'")
_POSIXPATH_RE = re.compile(r"PosixPath\('[^']*/([^/']+)'\)")
_MAKO_MEMORY_RE = re.compile(r"memory:[0-9a-fA-F]+")


def canonicalize(value):
    value = _ADDR_RE.sub("0x<addr>", value)
    value = _MODULE_FROM_RE.sub(r"from '<...>/\1'", value)
    value = _POSIXPATH_RE.sub(r"PosixPath('<...>/\1')", value)
    value = _MAKO_MEMORY_RE.sub("memory:<addr>", value)
    return value


def stable_repr(value):
    if isinstance(value, (set, frozenset)):
        parts = sorted(stable_repr(v) for v in value)
        return "{{" + ", ".join(parts) + "}}"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: repr(kv[0]))
        return "{{" + ", ".join(f"{{stable_repr(k)}}: {{stable_repr(v)}}" for k, v in items) + "}}"
    if isinstance(value, list):
        return "[" + ", ".join(stable_repr(v) for v in value) + "]"
    if isinstance(value, tuple):
        return "(" + ", ".join(stable_repr(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    return repr(value)


def public_attrs(module):
    out = {{}}
    for name in sorted(dir(module)):
        if name.startswith("_"):
            continue
        value = getattr(module, name)
        if callable(value):
            qn = getattr(value, "__qualname__", type(value).__name__)
            out[name] = f"<callable:{{qn}}>"
        else:
            try:
                out[name] = canonicalize(stable_repr(value))
            except Exception as exc:
                out[name] = f"<unreprable:{{type(value).__name__}}:{{exc}}>"
    return out


snap = {{}}
for name in modules:
    mod = importlib.import_module(name)
    snap[name] = public_attrs(mod)

json.dump(snap, sys.stdout, indent=2, sort_keys=True)
"""


def extract_upstream_wikidict(dest: Path) -> None:
    """Extract ``wikidict/`` subtree at BASELINE_COMMIT into ``dest`` via git archive | tar."""
    proc = subprocess.Popen(
        ["git", "archive", BASELINE_COMMIT, "wikidict"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
    )
    tar = subprocess.Popen(
        ["tar", "-x", "-C", str(dest)],
        stdin=proc.stdout,
    )
    assert proc.stdout is not None
    proc.stdout.close()
    tar.wait()
    proc.wait()
    if proc.returncode != 0 or tar.returncode != 0:
        raise RuntimeError(
            f"extract failed: git archive rc={proc.returncode} tar rc={tar.returncode}"
        )


def snapshot_upstream(extracted_dir: Path) -> dict[str, dict[str, str]]:
    env = {k: v for k, v in os.environ.items() if k != "ENGRISH_MODE"}
    env["PYTHONPATH"] = str(extracted_dir)
    script = SUBPROCESS_SCRIPT.format(modules=AUDITED_MODULES)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(extracted_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"subprocess snapshot failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return json.loads(result.stdout)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td)
        extract_upstream_wikidict(extract_dir)
        snap = snapshot_upstream(extract_dir)
    OUTPUT_PATH.write_text(
        json.dumps(snap, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_modules = len(snap)
    n_attrs = sum(len(v) for v in snap.values())
    print(f"Wrote baseline to {OUTPUT_PATH} ({n_modules} modules, {n_attrs} attrs).")


if __name__ == "__main__":
    main()
