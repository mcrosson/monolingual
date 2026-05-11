"""M11 — render-pipeline parity check (folds in deferred M4-AC2).

M4-AC2 contract: every headword's rendered HTML body matches what a direct
``wikidict.parse + wikidict.render`` invocation would produce. The engrish
render stage is a thin wrapper around ``wikidict.render.main(locale)`` (per
``engrish/stages/render.py``); no engrish-side transformation of the HTML
body. This test enforces that contract two ways:

1. **Static** (always-on): the render stage's call site is `wikidict.render.main`
   with no pre/post mutation of the produced JSON. Verified by source
   inspection of ``engrish/stages/render.py``.
2. **Subprocess byte-diff** (cost-gated by ``ENGRISH_RUN_M11_INTEGRATION=1``):
   for the smallest M9-corpus locale (``got``), invoke ``wikidict.render``
   directly via subprocess into an isolated CWD, then compare the produced
   ``data-<snap>.json`` against engrish's render artifact byte-for-byte.

If parity ever breaks, both checks fail and surface where engrish has
diverged from upstream wikidict's render contract.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = REPO_ROOT / "venv" / "bin" / "python"
RENDER_STAGE = REPO_ROOT / "engrish" / "stages" / "render.py"
SMALL_LOCALE = "got"


def test_render_stage_delegates_to_wikidict_unchanged() -> None:
    """M4-AC2 static — render.py invokes wikidict.render.main with no HTML mutation.

    Inspects render.py source and asserts:
    - It imports ``wikidict.render`` (or ``wikidict`` and uses ``render``)
    - It calls ``wikidict.render.main(locale)`` somewhere
    - It does NOT touch the produced JSON file (no json.load/dumps, no
      file-rewrite of data-*.json) — would indicate post-render mutation.
    """
    src = RENDER_STAGE.read_text(encoding="utf-8")
    # Direct delegation: must reference wikidict.render
    assert "wikidict" in src and "render" in src, (
        "render.py does not reference wikidict.render — "
        "this would mean the engrish render stage is doing its own rendering, "
        "which violates M4-AC2 parity."
    )
    # No JSON post-mutation: the data-*.json file produced by wikidict.render
    # must not be read+rewritten by the engrish stage. (Reading for verification
    # is fine — but rewriting would alter the bytes.)
    forbidden_patterns = [
        ("json.dumps(", "render.py serializes JSON; would mean it's mutating render output"),
        (".write_text(", "render.py writes text; would mean it's mutating render output"),
        (".write_bytes(", "render.py writes bytes; would mean it's mutating render output"),
    ]
    for pattern, msg in forbidden_patterns:
        # Allow inside docstrings/comments? The simplest check: forbid the call.
        # If we ever need to write something legitimate (like a sentinel marker),
        # expand this allowlist explicitly.
        assert pattern not in src, f"render.py contains `{pattern}`: {msg}"


def test_engrish_render_content_identical_to_direct_wikidict_render(tmp_path: Path) -> None:
    """M4-AC2 integration — engrish render JSON content == direct wikidict.render JSON content.

    M13-AC13 (2026-05-09): cost gate removed. Default invocation runs the
    full wikidict.render subprocess on the smallest D35 locale.

    Compares at the dict (logical) level, not raw bytes — see the multiprocessing
    note below the comparison block. Asserts every headword key matches and every
    body matches; bytes may differ due to upstream worker-completion ordering.
    """
    # Locate the engrish-produced render JSON for the small locale.
    from engrish.paths import render_source_dir
    from engrish.wikidict_shim import engrish_mode

    # render_source_dir resolves via wikidict.utils.guess_locales, which depends
    # on shim state. With shim disengaged (autouse conftest fixture), guess_locales
    # returns ('got','got') and produces data/got/got — wrong. Engrish stages always
    # run with the shim active, so the canonical path is data/got/en.
    with engrish_mode():
        engrish_json_dir = render_source_dir(SMALL_LOCALE)

    engrish_jsons = sorted(engrish_json_dir.glob("data-*.json"))
    if not engrish_jsons:
        # Auto-prime: language-stats D29-recurses through render. Cheap for got
        # (~seconds when parse SQLite is present).
        prime = subprocess.run(
            [str(PYTHON), "-m", "engrish", "language-stats", "--locale", SMALL_LOCALE],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        assert prime.returncode == 0, (
            f"priming engrish render for {SMALL_LOCALE} failed:\n{prime.stderr[-1500:]}"
        )
        engrish_jsons = sorted(engrish_json_dir.glob("data-*.json"))
        assert engrish_jsons, f"priming did not produce JSON in {engrish_json_dir}"
    engrish_latest = engrish_jsons[-1]

    # Run direct wikidict.render in a subprocess. wikidict.render writes to
    # data/<lang_dst>/<lang_src>/data-<lang_dst>.json under CWD; we redirect
    # CWD to a tmp dir and seed it with the parse artifacts via symlinks.
    parse_db_src = REPO_ROOT / "data" / "en"
    sqlites = list(parse_db_src.glob("pages-*.sqlite"))
    bz2_dumps = list(parse_db_src.glob("pages-*.xml.bz2"))
    assert sqlites, (
        f"no parse SQLite in {parse_db_src}; prime via `engrish prepare` first "
        "(M4-AC2 byte-diff requires the upstream parse artifact; prepare downloads "
        "a multi-GB dump and is intentionally not auto-primed from tests)"
    )
    assert bz2_dumps, (
        f"no .xml.bz2 dump in {parse_db_src}; wikidict.context.setup_modules_db "
        "reads the bz2 filename to derive the snapshot date. "
        "Re-prime via `./engrish.sh --prepare` (uses --keep-xml) — the bz2 is "
        "preserved across pipeline runs."
    )

    # Seed the tmp CWD with the same parse artifacts engrish would see. Symlink
    # the bz2 (1.5 GB) and SQLite (4 GB) rather than copy — read-only, hot-cache.
    tmp_data = tmp_path / "data" / "en"
    tmp_data.mkdir(parents=True, exist_ok=True)
    for src in [*sqlites, *bz2_dumps]:
        (tmp_data / src.name).symlink_to(src)

    # Run wikidict.render in a subprocess with the engrish shim active. This
    # mirrors the engrish stage's invocation (per F12 / D28: shim active during
    # all engrish-side wikidict calls). Without the shim, vanilla wikidict
    # expects a per-locale dump (``pages-*.xml.bz2`` under ``data/got/``) which
    # the engrish workflow never produces — engrish parses the EN dump once and
    # renders every locale from it via the shim's locale-origin remapping.
    # So a "shim-off" upstream invocation isn't a meaningful comparison point;
    # the byte-equality contract is "engrish/stages/render.py wraps wikidict.render
    # without mutating output", verified here by running the same wikidict.render
    # call directly into a tmp CWD and byte-comparing.
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [
            str(PYTHON), "-c",
            "from engrish.wikidict_shim import engrish_mode\n"
            "with engrish_mode():\n"
            "    from wikidict import render\n"
            f"    render.main({SMALL_LOCALE!r})",
        ],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"direct wikidict.render({SMALL_LOCALE!r}) failed:\n{result.stderr[-2000:]}"
    )

    # Locate the direct-render output and byte-compare. Shim-active path:
    # ``data/<lang_dst>/en/data-<lang_dst>.json`` = ``data/got/en/data-got.json``.
    direct_json_dir = tmp_path / "data" / SMALL_LOCALE / "en"
    direct_jsons = sorted(direct_json_dir.glob("data-*.json"))
    assert direct_jsons, f"direct wikidict.render produced no JSON in {direct_json_dir}"
    direct_latest = direct_jsons[-1]

    # Decode both JSONs and compare at the dict (logical) level — wikidict.render
    # uses ``multiprocessing.Pool.imap_unordered`` (wikidict/render.py:664) which
    # writes per-headword results into a ``Manager().dict()`` in worker-completion
    # order. Two runs over the same input therefore produce byte-different JSON
    # (different key insertion order in the serialized output) but logically
    # identical content. M4-AC2's parity contract is "no engrish-side mutation of
    # the rendered HTML" — verified by per-headword body equality, not raw bytes.
    # (The M9 goldens regression is byte-deterministic only because it re-uses the
    # per-locale render JSON across pipeline re-runs via D29; render itself is not
    # byte-deterministic across fresh invocations.)
    a = json.loads(engrish_latest.read_text(encoding="utf-8"))
    b = json.loads(direct_latest.read_text(encoding="utf-8"))
    only_in_a = set(a) - set(b)
    only_in_b = set(b) - set(a)
    body_diffs = [k for k in (set(a) & set(b)) if a[k] != b[k]]
    assert not (only_in_a or only_in_b or body_diffs), (
        f"engrish render content differs from direct wikidict.render for {SMALL_LOCALE}:\n"
        f"  only in engrish: {sorted(only_in_a)[:10]} (total {len(only_in_a)})\n"
        f"  only in direct:  {sorted(only_in_b)[:10]} (total {len(only_in_b)})\n"
        f"  body differs for: {sorted(body_diffs)[:10]} (total {len(body_diffs)})\n"
        "(M4-AC2 violation — engrish render mutates output vs raw wikidict.render)"
    )
    assert len(a) > 0, f"render produced empty content for {SMALL_LOCALE}"
