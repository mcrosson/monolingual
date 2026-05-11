"""M10-AC1 — peak-RSS instrumentation for engrish pipeline stages.

Runs each user-visible CLI subcommand in turn under process-tree RSS
polling (via psutil), records the high-water memory + wall-clock per
stage, and writes a JSON report to ``engrish/tests/peak_rss/<form>.json``.

Why process-tree polling: render uses multiprocessing spawn workers
(per `engrish/wikidict_shim.py::_engrish_worker_init`). A naive
``getrusage(RUSAGE_SELF)`` would miss them; ``RUSAGE_CHILDREN`` only
captures reaped children's max-of-any-one. ``psutil.Process(pid).
children(recursive=True)`` walks the live tree on every poll tick,
and we accumulate ``sum(rss for proc in tree)`` as the high-water.

Polling interval defaults to 100 ms — enough granularity for the
multi-second / multi-minute stages we measure, low enough overhead
not to perturb the measurement.

JSON schema (``engrish/tests/peak_rss/<form>.json``):
::
    {
      "form": "got",
      "host_total_ram_mb": 16384,
      "measured_at": "2026-04-26T18:30:00",
      "poll_interval_s": 0.1,
      "stages": [
        {"stage": "generate", "argv": [...], "rc": 0,
         "wall_s": 12.4, "peak_rss_kb": 524288, "peak_rss_mb": 512.0},
        ...
      ]
    }

Usage::

    ./venv/bin/python engrish/scripts/measure_pipeline.py got
    # → engrish/tests/peak_rss/got.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def _peak_rss_tree_kb(root_pid: int, stop_event: threading.Event,
                      result_ref: list[int], interval_s: float) -> None:
    """Background poller: track sum(rss) across the process tree rooted at ``root_pid``.

    Updates ``result_ref[0]`` (in KB) with the running maximum until
    ``stop_event`` is set OR the root process exits.
    """
    try:
        root = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        return

    while not stop_event.is_set():
        try:
            procs = [root, *root.children(recursive=True)]
            total_bytes = 0
            for p in procs:
                try:
                    total_bytes += p.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            total_kb = total_bytes // 1024
            if total_kb > result_ref[0]:
                result_ref[0] = total_kb
        except psutil.NoSuchProcess:
            return
        time.sleep(interval_s)


def measure_subcommand(argv: list[str], poll_interval_s: float = 0.1) -> dict:
    """Run ``argv`` as subprocess; return dict with rc + wall + peak RSS."""
    start = time.monotonic()
    proc = subprocess.Popen(argv)
    peak_kb_ref = [0]
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_peak_rss_tree_kb,
        args=(proc.pid, stop_event, peak_kb_ref, poll_interval_s),
        daemon=True,
    )
    poller.start()
    rc = proc.wait()
    stop_event.set()
    poller.join(timeout=2.0)
    wall_s = time.monotonic() - start
    peak_kb = peak_kb_ref[0]
    return {
        "argv": argv,
        "rc": rc,
        "wall_s": round(wall_s, 3),
        "peak_rss_kb": peak_kb,
        "peak_rss_mb": round(peak_kb / 1024, 1),
    }


def _stages_for_form(form: str, py: str) -> list[tuple[str, list[str]]]:
    """Stage label → CLI argv. Order matters (D29 dependency)."""
    return [
        ("generate", [py, "-m", "engrish", "generate", "--form", form]),
        ("epub",     [py, "-m", "engrish", "epub",     "--form", form]),
        ("font",     [py, "-m", "engrish", "font",     "--form", form]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("form", help="form to measure (e.g. 'got', 'en+ang')")
    parser.add_argument("--py", default="./venv/bin/python", help="python interpreter")
    parser.add_argument("--out-dir", default="engrish/tests/peak_rss",
                        help="output directory for JSON report")
    parser.add_argument("--poll-interval-s", type=float, default=0.1)
    parser.add_argument("--clear-form-dir", action="store_true",
                        help="rm -rf data/engrish/<form> before run (forces "
                             "fresh build; defeats D29 require_artifact no-op)")
    args = parser.parse_args()

    if args.clear_form_dir:
        import shutil
        form_dir = Path("data/engrish") / args.form
        if form_dir.exists():
            shutil.rmtree(form_dir)

    stages = _stages_for_form(args.form, args.py)
    results: list[dict] = []
    for label, argv in stages:
        sys.stderr.write(f"[measure] {label} ...\n")
        r = measure_subcommand(argv, poll_interval_s=args.poll_interval_s)
        r["stage"] = label
        sys.stderr.write(
            f"[measure] {label}: rc={r['rc']} wall={r['wall_s']}s "
            f"peak={r['peak_rss_mb']} MB\n"
        )
        results.append(r)
        if r["rc"] != 0:
            sys.stderr.write(f"[measure] {label} failed; aborting form\n")
            break

    host_ram_mb = psutil.virtual_memory().total // (1024 * 1024)
    report = {
        "form": args.form,
        "host_total_ram_mb": host_ram_mb,
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "poll_interval_s": args.poll_interval_s,
        "stages": results,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.form}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    sys.stderr.write(f"[measure] wrote {out_path}\n")
    return 0 if all(r["rc"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
