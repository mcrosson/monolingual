"""M0-AC2: wikidict upstream-parity harness works end-to-end.

Verifies the harness is usable. Does NOT require zero diff at M0 — M2 is the
milestone that drives the diff to empty. This test only asserts shape/usability.
"""

from __future__ import annotations

from tests_new.harness import parity


def test_snapshot_current_returns_dict() -> None:
    snap = parity.snapshot_current()
    assert isinstance(snap, dict)
    assert set(snap.keys()) == set(parity.AUDITED_MODULES)
    for attrs in snap.values():
        assert isinstance(attrs, dict)
        for attr_name, repr_value in attrs.items():
            assert isinstance(attr_name, str)
            assert isinstance(repr_value, str)


def test_load_baseline() -> None:
    baseline = parity.load_baseline()
    assert isinstance(baseline, dict)
    assert set(baseline.keys()) == set(parity.AUDITED_MODULES)
    total_attrs = sum(len(v) for v in baseline.values())
    assert total_attrs > 0


def test_diff_self_is_empty() -> None:
    snap = parity.snapshot_current()
    assert parity.diff_snapshots(snap, snap) == []


def test_diff_returns_parity_diff_tuples() -> None:
    current = parity.snapshot_current()
    baseline = parity.load_baseline()
    diffs = parity.diff_snapshots(current, baseline)
    assert isinstance(diffs, list)
    for d in diffs:
        assert isinstance(d, parity.ParityDiff)
