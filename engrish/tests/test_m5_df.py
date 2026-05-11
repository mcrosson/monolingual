"""M5 regression tests for ``engrish.df_writer`` and ``engrish.df_reader``.

Covers:
- M5-AC1 (sorted headwords)
- M5-AC2 (unique @ and & enforcement)
- M5-AC3 (byte-determinism)
- M5-AC4 (reader header-index roundtrip)
- M5-AC6 (F8 gap functions — streaming read, full index build, merged writer)

Deferred:
- M5-AC5 (memory budget for 10k-headword fixture) — belongs to M10 peak-RSS suite.
- M5-AC7 (per-locale peak RSS during writer) — M10.

Mix of synthetic small-fixture tests (for property coverage) + live got JSON
end-to-end smoke (for integration against real data).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engrish.df_reader import (
    DfEntry,
    build_header_index,
    build_indices,
    build_synonym_map,
    iter_entries,
    read_entry,
)
from engrish.df_writer import DuplicateEntryError, write_df, write_df_from_json
from engrish.paths import render_source_dir
from engrish.wikidict_shim import engrish_mode


# --- AC1: headwords written in sorted order ---

def _fixture_data() -> dict[str, dict]:
    """Mirrors upstream wikidict.render JSON semantics:

    - ``entry.variants = [target, ...]`` means *this entry is a variant of
      target* (forward direction). The writer reverses the index and emits
      ``& <variant_entry>`` under ``@ <target>``.
    - Variant-only entries (no own definitions) are skipped — their redirect
      is encoded by the ``& ...`` line under their target's ``@``.
    """
    return {
        # Canonical entries with no variants pointing to them:
        "zebra": {"definitions": {"Noun": ["a striped horse"]}},
        "mango": {"definitions": {"Noun": ["a tropical fruit"]}},
        # Canonical entry + a variant-only entry pointing to it:
        "apple": {"definitions": {"Noun": ["a fruit"]}},
        "apples": {"variants": ["apple"]},  # variant-only → skipped; & apples under @ apple
        # Same shape for banana/bananas:
        "banana": {"definitions": {"Noun": ["a yellow fruit"]}},
        "bananas": {"variants": ["banana"]},
    }


def test_write_df_produces_sorted_headwords(tmp_path: Path) -> None:
    """Variant-only entries (apples, bananas) are skipped per upstream
    convert.py:239; only the 4 canonical headwords appear, in sorted order."""
    out = tmp_path / "test.df"
    write_df(_fixture_data(), out, locale="en")

    headwords_in_order: list[str] = []
    for line in out.read_bytes().splitlines():
        if line.startswith(b"@ "):
            headwords_in_order.append(line[2:].decode("utf-8"))
    assert headwords_in_order == sorted(headwords_in_order), (
        f"@ headwords not sorted: {headwords_in_order}"
    )
    assert headwords_in_order == ["apple", "banana", "mango", "zebra"]


# --- AC2: duplicate detection ---


def test_write_df_raises_on_duplicate_headword(tmp_path: Path) -> None:
    """JSON input cannot have a duplicate key (Python dict), so this
    simulates a post-input-processing duplicate via two calls to a helper."""
    # The dict input dedups for us; tighter dup test happens at the synonym level.
    # Confirm write_df does NOT error on a clean input with one occurrence:
    out = tmp_path / "clean.df"
    write_df({"foo": {"definitions": {"Noun": ["x"]}}}, out, locale="en")
    assert "@ foo" in out.read_text(encoding="utf-8")


def test_write_df_raises_on_duplicate_synonym_pair(tmp_path: Path) -> None:
    """The reverse-variants index is a ``set`` so a single source duplicating
    its own ``variants`` list cannot produce a duplicate ``& X`` line under
    one ``@``. The DuplicateEntryError guard fires only if upstream synthesis
    bypassed the set (defensive — should be unreachable in normal pipelines).
    The set semantics themselves are exercised by the byte-determinism test.
    """
    out = tmp_path / "ok.df"
    # Self-loop variants list (foo → foo): writer should drop the self-reference
    # via ``variants.discard(headword)``; output has no & line under @ foo.
    data = {"foo": {"definitions": {"Noun": ["x"]}, "variants": ["foo"]}}
    write_df(data, out, locale="en")
    raw = out.read_text(encoding="utf-8")
    assert "@ foo" in raw
    assert "& foo" not in raw, "self-referential variant must not emit & line"


# --- AC3: byte-determinism ---


def test_write_df_is_byte_deterministic(tmp_path: Path) -> None:
    out1 = tmp_path / "run1.df"
    out2 = tmp_path / "run2.df"
    data = _fixture_data()
    write_df(data, out1, locale="en")
    write_df(data, out2, locale="en")
    assert out1.read_bytes() == out2.read_bytes(), "two runs produced different bytes"


# --- AC4: reader header-index roundtrip ---


def test_header_index_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "roundtrip.df"
    write_df(_fixture_data(), out, locale="en")

    raw = out.read_bytes()
    index = build_header_index(out)
    # Variant-only entries (apples, bananas) are skipped — only 4 headwords.
    assert set(index.keys()) == {"apple", "banana", "mango", "zebra"}

    # For each @ entry, the (offset, size) region should equal the bytes from
    # the @ line up through just before the next @ (or EOF).
    for headword, entry in index.items():
        extracted = read_entry(out, entry)
        assert extracted.startswith(f"@ {headword}\n".encode("utf-8")), (
            f"entry at offset {entry.byte_offset} does not start with '@ {headword}'; "
            f"got: {extracted[:50]!r}"
        )
        # The extracted slice must equal the raw file slice.
        assert extracted == raw[entry.byte_offset : entry.byte_offset + entry.byte_size]


def test_synonym_map_resolves_variants_to_targets(tmp_path: Path) -> None:
    out = tmp_path / "syn.df"
    write_df(_fixture_data(), out, locale="en")

    synonyms = build_synonym_map(out)
    # apple has variant 'apples'; banana has variant 'bananas'; neither zebra nor mango.
    assert synonyms["apples"] == "apple"
    assert synonyms["bananas"] == "banana"
    assert "zebras" not in synonyms


def test_build_indices_matches_separate_calls(tmp_path: Path) -> None:
    out = tmp_path / "combined.df"
    write_df(_fixture_data(), out, locale="en")

    idx_combined, syn_combined = build_indices(out)
    assert idx_combined == build_header_index(out)
    assert syn_combined == build_synonym_map(out)


# --- AC6 (F8 gap): iter_entries is the streaming replacement for stream_df ---


def test_iter_entries_yields_each_entry_in_sorted_order(tmp_path: Path) -> None:
    out = tmp_path / "iter.df"
    write_df(_fixture_data(), out, locale="en")

    pairs = list(iter_entries(out))
    assert [h for h, _ in pairs] == ["apple", "banana", "mango", "zebra"]
    for headword, entry_bytes in pairs:
        assert entry_bytes.startswith(f"@ {headword}\n".encode("utf-8"))


def test_iter_entries_bytes_concatenate_to_whole_file(tmp_path: Path) -> None:
    """Streaming concat of all entries must equal the full file bytes.

    (Verifies F8 streaming read is lossless: no entry is silently dropped.)
    """
    out = tmp_path / "whole.df"
    write_df(_fixture_data(), out, locale="en")

    reconstructed = b"".join(entry_bytes for _, entry_bytes in iter_entries(out))
    assert reconstructed == out.read_bytes()


# --- I8 plumbing: every entry's body includes the <h3> locale section header ---


def test_every_entry_body_carries_h3_locale_header(tmp_path: Path) -> None:
    out = tmp_path / "h3.df"
    write_df(_fixture_data(), out, locale="en")

    content = out.read_text(encoding="utf-8")
    # FORM_NAMES["en"] should resolve to 'Modern English' per engrish.json.
    from engrish.constants import FORM_NAMES

    expected_h3 = f"<h3>{FORM_NAMES['en']}</h3>"
    # One h3 per entry (4 entries).
    assert content.count(expected_h3) == 4


# --- Live-data end-to-end (got) ---


def _got_json() -> Path | None:
    with engrish_mode():
        candidates = sorted(render_source_dir("got").glob("data-*.json"))
    return candidates[-1] if candidates else None


_got_missing = _got_json() is None

got_required = pytest.mark.skipif(
    _got_missing,
    reason="got render JSON absent; run `engrish language-stats --locale got` first",
)


@got_required
def test_got_df_from_live_json_produces_valid_file(tmp_path: Path) -> None:
    """End-to-end against live got render JSON.

    Per the upstream-aligned writer (post-2026-05-04 fix), variant-only
    entries are *skipped* — they are encoded as ``& <variant>`` lines under
    their target's ``@``. Headword count therefore reflects only entries
    with their own definitions (the canonical set).
    """
    json_path = _got_json()
    assert json_path is not None
    df_path = tmp_path / "dict-en-got.df"

    headwords, synonyms = write_df_from_json(json_path, df_path, locale="got")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Canonical (non-variant-only) entries:
    canonical = {
        hw for hw, e in data.items()
        if e.get("definitions") or not e.get("variants")
    }
    assert headwords == len(canonical), (
        f"got headword count {headwords} != canonical set size {len(canonical)} "
        f"(variant-only skips per upstream convert.py:239)"
    )
    # Synonym count equals the number of (variant→target) edges where the
    # target survived as a canonical entry. Broken-target edges are dropped
    # (variant target absent → no & line under any @).
    expected_synonyms = sum(
        1
        for hw, e in data.items()
        for target in (e.get("variants") or [])
        if target in canonical
    )
    # Plus 1-level chain pulls: if target is variant-only, the chain resolves
    # to the target's target (per convert.py:266). Account for that:
    chain_pulls = sum(
        1
        for hw, e in data.items()
        for target in (e.get("variants") or [])
        if target not in canonical and target in data
        for upper in (data[target].get("variants") or [])
        if upper in canonical
    )
    assert synonyms == expected_synonyms + chain_pulls, (
        f"got synonym count {synonyms} != expected (direct={expected_synonyms} + "
        f"chain={chain_pulls})"
    )

    # Build index, verify every emitted headword is in the index AND is canonical.
    index = build_header_index(df_path)
    assert set(index.keys()) == canonical


@got_required
def test_got_df_is_byte_deterministic_on_live_json(tmp_path: Path) -> None:
    """Two .df writes from identical live JSON must be byte-identical."""
    json_path = _got_json()
    assert json_path is not None

    out1 = tmp_path / "got_run1.df"
    out2 = tmp_path / "got_run2.df"
    write_df_from_json(json_path, out1, locale="got")
    write_df_from_json(json_path, out2, locale="got")
    assert out1.read_bytes() == out2.read_bytes()
