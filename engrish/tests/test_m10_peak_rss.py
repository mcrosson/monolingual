"""M10-AC1 — peak-RSS instrumentation present.

The instrumentation lives at ``engrish/scripts/measure_pipeline.py``. This test
asserts:

1. The instrumentation script runs end-to-end for a small locale and
   writes a well-formed JSON report.
2. Pre-existing committed JSON reports (`engrish/tests/peak_rss/*.json`)
   conform to the documented schema and contain non-zero peak RSS
   measurements for every stage.

**M13-AC13 (2026-05-09):** cost gate removed. Default invocation runs the live
instrumentation smoke test on the smallest D35 locale (got).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PEAK_RSS_DIR = WORKSPACE / "engrish" / "tests" / "peak_rss"
PY = WORKSPACE / "venv" / "bin" / "python"
TOOL = WORKSPACE / "engrish" / "scripts" / "measure_pipeline.py"

_REQUIRED_TOP_KEYS = {"form", "host_total_ram_mb", "measured_at",
                      "poll_interval_s", "stages"}
_REQUIRED_STAGE_KEYS = {"stage", "argv", "rc", "wall_s",
                        "peak_rss_kb", "peak_rss_mb"}


def _committed_reports() -> list[Path]:
    """JSON reports already committed under engrish/tests/peak_rss/."""
    return sorted(p for p in PEAK_RSS_DIR.glob("*.json") if p.is_file())


def test_measure_pipeline_tool_exists() -> None:
    """AC1: the instrumentation script is present and executable."""
    assert TOOL.exists(), f"missing instrumentation script: {TOOL}"
    src = TOOL.read_text()
    # Sanity: script imports psutil and writes a JSON report.
    assert "import psutil" in src
    assert "json.dumps" in src


def test_committed_reports_conform_to_schema() -> None:
    """Every committed peak_rss report has the documented keys + sane values."""
    reports = _committed_reports()
    if not reports:
        pytest.skip("no committed peak_rss reports yet (M10-AC1 in progress)")
    for path in reports:
        data = json.loads(path.read_text())
        missing_top = _REQUIRED_TOP_KEYS - set(data)
        assert not missing_top, f"{path.name}: missing top-level keys {missing_top}"
        assert isinstance(data["stages"], list) and data["stages"], \
            f"{path.name}: stages must be a non-empty list"
        for stage in data["stages"]:
            missing_stage = _REQUIRED_STAGE_KEYS - set(stage)
            assert not missing_stage, \
                f"{path.name}: stage {stage.get('stage')!r} missing keys {missing_stage}"
            assert stage["peak_rss_kb"] > 0, \
                f"{path.name}: stage {stage['stage']!r} peak_rss_kb is 0 (instrumentation broken?)"
            assert stage["wall_s"] > 0


def test_live_instrumentation_run_on_got() -> None:
    """AC1 live verification: run the instrumentation on the smallest D35 locale.

    Asserts the script exits 0 and the resulting JSON conforms to the schema.
    """
    out = subprocess.run(
        [str(PY), str(TOOL), "got", "--clear-form-dir"],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, (
        f"measure_pipeline.py got exited rc={out.returncode}\n"
        f"--- stderr ---\n{out.stderr}\n"
    )
    report_path = PEAK_RSS_DIR / "got.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["form"] == "got"
    stages = {s["stage"] for s in data["stages"]}
    assert stages == {"generate", "epub", "font"}
    for stage in data["stages"]:
        assert stage["rc"] == 0, f"stage {stage['stage']} rc={stage['rc']}"
        assert stage["peak_rss_kb"] > 0
