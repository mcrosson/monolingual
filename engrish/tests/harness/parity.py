"""Wikidict upstream-parity harness (M0-AC2).

Snapshots ``wikidict``'s public attribute surface and diffs it against a
committed baseline derived from upstream merge-base ``433889e3``. Zero diff
with ``engrish_mode()`` disengaged is the fork re-sync contract
(``[[goal-fork-resyncable]]`` / I2); M2 is the milestone that drives the diff
to zero. At M0 the harness only needs to exist and produce consistent results.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, NamedTuple

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "baselines" / "wikidict_attrs_upstream.json"
)

# Canonicalize per-run noise so the diff reflects real divergence, not
# environment drift. Two classes of noise:
#   1. Memory addresses in object reprs (``<X object at 0x7f...>``) — differ
#      every process invocation; we replace the hex run with ``<addr>``.
#   2. Absolute paths in module reprs (``<module 'M' from '/path/to/M.py'>``)
#      and ``PosixPath('/path/...')`` constants — differ between the baseline
#      (extracted to ``/tmp/tmpXXXX/wikidict`` by git archive) and runtime
#      (``/home/agent/workspace/wikidict``). We preserve the basename and
#      replace the directory prefix with ``<...>``.
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_MODULE_FROM_RE = re.compile(r"from '[^']*/([^/']+)'")
_POSIXPATH_RE = re.compile(r"PosixPath\('[^']*/([^/']+)'\)")
# Mako ``Template`` uses ``memory:<hex>`` instead of ``0x<hex>``.
_MAKO_MEMORY_RE = re.compile(r"memory:[0-9a-fA-F]+")


def _canonicalize(value: str) -> str:
    value = _ADDR_RE.sub("0x<addr>", value)
    value = _MODULE_FROM_RE.sub(r"from '<...>/\1'", value)
    value = _POSIXPATH_RE.sub(r"PosixPath('<...>/\1')", value)
    value = _MAKO_MEMORY_RE.sub("memory:<addr>", value)
    return value

# Scope: the 15 modules called out in D10's audit — enough to detect every known
# divergence. Expand as new divergence surfaces are found.
AUDITED_MODULES: tuple[str, ...] = (
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


class ParityDiff(NamedTuple):
    module: str
    attr: str
    baseline: str
    current: str


def _stable_repr(value: Any) -> str:
    """Deterministic repr that normalizes container iteration order.

    Upstream wikidict constructs some inner dicts via ``dict.fromkeys({set_literal}, ...)``
    (e.g. ``wikidict/lang/ca/template_adapters.py``) which inherits hash-random
    set-iteration order. That isn't a fork delta — it's nondeterminism in
    upstream source that the parity harness has to absorb. We sort:
      - set / frozenset elements (always unordered)
      - dict keys at every nesting level (defined-order in Python 3.7+, but
        that order can itself be hash-random if construction iterates a set)
    For mutable container types we recurse; otherwise ``repr`` is used as-is.
    """
    if isinstance(value, (set, frozenset)):
        parts = sorted(_stable_repr(v) for v in value)
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: repr(kv[0]))
        return "{" + ", ".join(f"{_stable_repr(k)}: {_stable_repr(v)}" for k, v in items) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_stable_repr(v) for v in value) + "]"
    if isinstance(value, tuple):
        return "(" + ", ".join(_stable_repr(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    return repr(value)


def _public_attrs(module: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in sorted(dir(module)):
        if name.startswith("_"):
            continue
        value = getattr(module, name)
        if callable(value):
            qn = getattr(value, "__qualname__", type(value).__name__)
            out[name] = f"<callable:{qn}>"
        else:
            try:
                out[name] = _canonicalize(_stable_repr(value))
            except Exception as exc:  # pragma: no cover — defensive
                out[name] = f"<unreprable:{type(value).__name__}:{exc}>"
    return out


def snapshot_current(
    modules: tuple[str, ...] = AUDITED_MODULES,
) -> dict[str, dict[str, str]]:
    """Snapshot the attribute surface of each listed module as currently imported.

    Caller is responsible for ensuring ``ENGRISH_MODE`` is unset in the environment.
    """
    snap: dict[str, dict[str, str]] = {}
    for name in modules:
        mod = importlib.import_module(name)
        snap[name] = _public_attrs(mod)
    return snap


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def diff_snapshots(
    current: dict[str, dict[str, str]],
    baseline: dict[str, dict[str, str]],
) -> list[ParityDiff]:
    """Return sorted list of diffs; empty list = identical snapshots."""
    diffs: list[ParityDiff] = []
    all_modules = set(current) | set(baseline)
    for mod in sorted(all_modules):
        b = baseline.get(mod, {})
        c = current.get(mod, {})
        all_attrs = set(b) | set(c)
        for attr in sorted(all_attrs):
            bv = b.get(attr, "<missing>")
            cv = c.get(attr, "<missing>")
            if bv != cv:
                diffs.append(ParityDiff(mod, attr, bv, cv))
    return diffs
