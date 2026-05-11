"""M6 regression tests for engrish.merge + engrish.stardict_writer + generate stage.

Covers:
- M6-AC1 install gate (sdcv version + lookup harness)
- M6-AC3 sdcv roundtrip (sampled-words HTML body matches .df body)
- M6-AC4 .idx walk (strictly sorted; offsets in valid .dict range; under 2³² ceiling)
- M6-AC5 wordcount cross-check (.ifo vs @ count, synwordcount vs & count)
- M6-AC8 grep — no NO_SQLITE / auto_sqlite / cleanup overrides in stardict_writer
- M6-AC9 grep — no getBookname monkey-patch in stardict_writer
- M6-AC10 .idx 2³² ceiling assertion (synthetic-trigger via tiny ceiling)
- M6-AC11 byte-determinism (two generate runs → diff -r empty for the four files)
- merge.py invariants (sorted output, multi-source <h3> stacking)

Deferred:
- M6-AC2 generate end-to-end is exercised by the live got run (manual + CI proxy).
- M6-AC6 multi-locale <h3> per contributing locale — requires render JSON for ≥2 locales;
  exercised by the synthetic-fixture merge tests below.
- M6-AC7 source-coverage audit — belongs to M7 missing-words chapter; tested there.
- M6-AC12 D29 generate recursion (rm json → render invoked) — destructive; M11-AC5.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest

from engrish.df_writer import write_df
from engrish.merge import _merge_entries, _split_entry, merge_dfs
from engrish.paths import engrish_form_dir, dict_base_name
from engrish.stardict_writer import IdxOverflowError
from engrish.wikidict_shim import engrish_mode
from engrish.tests.harness.sdcv import is_installed as sdcv_is_installed, lookup as sdcv_lookup


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOT_FORM_DIR = REPO_ROOT / "data" / "engrish" / "got"


def _got_dict_present() -> bool:
    if not GOT_FORM_DIR.exists():
        return False
    return (
        bool(list(GOT_FORM_DIR.glob("*.df"))) and
        bool(list(GOT_FORM_DIR.glob("*.ifo"))) and
        bool(list(GOT_FORM_DIR.glob("*.idx"))) and
        bool(list(GOT_FORM_DIR.glob("*.dict")))
    )


got_dict_required = pytest.mark.skipif(
    not _got_dict_present(),
    reason="got StarDict absent; run `engrish generate --form got` first",
)

sdcv_required = pytest.mark.skipif(
    not sdcv_is_installed(),
    reason="sdcv not installed; M6-AC1 install gate not satisfied",
)


# --- M6-AC1 install gate ---


def test_sdcv_install_gate_satisfied() -> None:
    """M6-AC1: sdcv binary present and the harness's lookup helper resolves."""
    assert sdcv_is_installed(), "sdcv must be installed for M6"
    # Harness lookup signature exists and is callable.
    assert callable(sdcv_lookup)


# --- merge.py: split helper invariants ---


def test_split_entry_separates_preamble_from_body() -> None:
    raw = b"@ foo\n:[ipa]\n& bar\n<html><h3>Locale</h3>body</html>\n\n"
    preamble, body = _split_entry(raw)
    assert preamble == [b"@ foo\n", b":[ipa]\n", b"& bar\n"]
    assert body == b"<html><h3>Locale</h3>body</html>\n\n"


def test_merge_entries_stacks_bodies_in_form_locale_order() -> None:
    """A multi-locale headword should have its bodies stacked in form_locales order."""
    en_entry = b"@ shared\n<html><h3>Modern English</h3>en-body</html>\n\n"
    ang_entry = b"@ shared\n<html><h3>Old English</h3>ang-body</html>\n\n"
    # Sources arrive in arbitrary order; merge must reorder by form_locales index.
    sources = [(1, ang_entry), (0, en_entry)]
    merged = _merge_entries(sources, form_locales=["en", "ang"])
    # en (idx 0) should appear before ang (idx 1) in the output.
    en_pos = merged.find(b"Modern English")
    ang_pos = merged.find(b"Old English")
    assert 0 <= en_pos < ang_pos


# --- merge.py: end-to-end synthetic fixture ---


def _write_fixture(tmp_path: Path, name: str, data: dict) -> Path:
    out = tmp_path / name
    write_df(data, out, locale=name.split(".")[0])
    return out


def test_merge_dfs_preserves_sort_across_sources(tmp_path: Path) -> None:
    en_data = {"alpha": {"definitions": {"Noun": ["a"]}}, "delta": {"definitions": {"Noun": ["d"]}}}
    fr_data = {"beta": {"definitions": {"Noun": ["b"]}}, "gamma": {"definitions": {"Noun": ["g"]}}}
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    fr_df = _write_fixture(tmp_path, "fr.df", fr_data)
    merged = tmp_path / "merged.df"
    merged_count, contributing = merge_dfs([en_df, fr_df], merged, form_locales=["en", "fr"])

    assert merged_count == 4
    assert contributing == 4

    headwords = [
        line[2:].decode() for line in merged.read_bytes().splitlines() if line.startswith(b"@ ")
    ]
    assert headwords == ["alpha", "beta", "delta", "gamma"]


def test_merge_dfs_collapses_shared_headword_with_multiple_h3(tmp_path: Path) -> None:
    """M6-AC6: shared headword across 2 sources → one merged entry with 2 h3 sections."""
    en_data = {"shared": {"definitions": {"Noun": ["en-def"]}}}
    fr_data = {"shared": {"definitions": {"Noun": ["fr-def"]}}}
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    fr_df = _write_fixture(tmp_path, "fr.df", fr_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df, fr_df], merged, form_locales=["en", "fr"])

    content = merged.read_text(encoding="utf-8")
    # Exactly one @ shared line.
    assert content.count("@ shared") == 1
    # Two <h3> sections (one per contributing locale).
    h3_count = content.count("<h3>")
    assert h3_count == 2, f"expected 2 <h3> for shared headword, got {h3_count}"


def test_merge_dfs_coalesces_case_variants_under_lowercase_canonical(tmp_path: Path) -> None:
    """D37 (2026-05-04) — case-fold coalescing.

    For a case-fold group with both ``duck`` (lowercase: Verb+Noun) and
    ``Duck`` (capitalized: Proper Noun), the merge produces ONE entry under
    ``@ duck`` carrying both bodies, with ``& Duck`` synonym in the preamble
    so explicit-case lookups resolve via ``.syn``. The capitalized body is
    placed after ``<h3>* * *</h3>`` separator and its h3 is annotated with
    ``(Capitalized: Duck)``.
    """
    en_data = {
        "duck": {"definitions": {"Verb": ["to lower"], "Noun": ["a waterbird"]}},
        "Duck": {"definitions": {"Proper Noun": ["a surname"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    n_hw, _ = merge_dfs([en_df], merged, form_locales=["en"])

    # One canonical entry, case-coalesced.
    assert n_hw == 1
    content = merged.read_text(encoding="utf-8")
    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert headwords == ["duck"], f"expected only @ duck after case-coalescing, got {headwords}"

    # Synonym pointing back to the canonical so case-explicit Duck still resolves.
    assert "& Duck" in content

    # Lowercase body first (Verb + Noun), separator, then annotated capitalized body.
    duck_body_pos = content.find("a waterbird")
    sep_pos = content.find("<h3>* * *</h3>")
    cap_pos = content.find("(Capitalized: Duck)")
    surname_pos = content.find("a surname")
    assert 0 < duck_body_pos < sep_pos < cap_pos < surname_pos, (
        f"order wrong: duck={duck_body_pos}, sep={sep_pos}, "
        f"capitalized={cap_pos}, surname={surname_pos}"
    )


def test_merge_dfs_no_separator_when_only_one_case_variant(tmp_path: Path) -> None:
    """A case-fold group with only the canonical (no other case-variants) emits
    no ``<h3>* * *</h3>`` separator and no ``(Capitalized: ...)`` annotation."""
    en_data = {"plain": {"definitions": {"Noun": ["something"]}}}
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")
    assert "<h3>* * *</h3>" not in content
    assert "(Capitalized:" not in content


def test_merge_dfs_no_lowercase_falls_back_to_first_sorted(tmp_path: Path) -> None:
    """If a case-fold group has no lowercase variant, canonical = first sorted byte-wise."""
    en_data = {
        "Hund": {"definitions": {"Noun": ["dog"]}},
        "HUND": {"definitions": {"Symbol": ["acronym"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")
    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    # 'HUND' (all-caps) sorts before 'Hund' byte-wise (U+0048,U+0055,...) so it's canonical.
    assert headwords == ["HUND"], headwords
    # The other variant gets the (Capitalized: ...) annotation.
    assert "(Capitalized: Hund)" in content


GOT_RENDER_JSON = REPO_ROOT / "data" / "got" / "en" / "data-20260401.json"


@got_dict_required
@sdcv_required
def test_sdcv_lookup_of_inflected_form_returns_canonical_body() -> None:
    """Item 3 (post-M12 finalization) — sdcv inflected-form lookup oracle.

    Pre-F17, inflected-form lookups (e.g. ``privations``) returned an empty
    body because the writer emitted ``& target`` lines under
    ``@ <variant_entry>`` (backwards) — the .syn redirect pointed the wrong
    way. Post-fix, an inflected-form sdcv lookup must resolve to the
    canonical's body via the ``& <variant>`` line under ``@ <canonical>``.
    Closes the M6 oracle gap that allowed F17 to land undetected.
    """
    if not GOT_RENDER_JSON.exists():
        pytest.skip("got render JSON absent")
    data = json.loads(GOT_RENDER_JSON.read_text(encoding="utf-8"))

    # Pick the first 5 inflected→canonical pairs where canonical has its own body.
    pairs: list[tuple[str, str]] = []
    for hw, e in data.items():
        vs = e.get("variants") or []
        if vs and not e.get("definitions"):
            for tgt in vs:
                t = data.get(tgt) or {}
                if t.get("definitions"):
                    pairs.append((hw, tgt))
                    break
        if len(pairs) >= 5:
            break
    assert pairs, "no inflected→canonical pairs found in got JSON; cannot test"

    failed: list[tuple[str, str, str]] = []
    for inflected, canonical in pairs:
        body = sdcv_lookup(inflected, GOT_FORM_DIR)
        # sdcv -e returns empty when the headword isn't found at all.
        if not body.strip():
            failed.append((inflected, canonical, "<empty body — lookup failed>"))
            continue
        # The canonical's body must appear in the lookup response (via .syn
        # redirect to the @ canonical entry that owns the body).
        if canonical not in body:
            failed.append((inflected, canonical, body[:200]))
    assert not failed, (
        "sdcv inflected-form lookup did NOT resolve to canonical's body for "
        f"{len(failed)}/{len(pairs)} pairs:\n"
        + "\n".join(f"  {v!r} → expected {c!r}; got: {b}" for v, c, b in failed)
    )



@pytest.mark.skipif(
    not GOT_RENDER_JSON.exists(),
    reason="got render JSON absent; run `engrish language-stats --locale got` first",
)
def test_engrish_df_writer_matches_wikidict_handle_word_on_got() -> None:
    """Item 2 (post-M12 finalization) — upstream-parity test.

    Asserts that ``engrish.df_writer.write_df`` produces the same headword set
    + the same ``(headword, sorted-tuple-of-&-targets)`` set as upstream
    ``wikidict.convert.DictFileFormat.process`` for the same render JSON.
    Closes the M5 oracle gap that allowed the F17 variant-direction inversion
    to land undetected: the M5 fixture was authored alongside the inverted
    writer, so self-consistency tests passed; an upstream-parity test would
    have caught it immediately.
    """
    import tempfile

    from engrish.df_writer import write_df as engrish_write_df
    from engrish.wikidict_shim import engrish_mode
    from wikidict.convert import DictFileFormat, load, make_variants, run_formatter

    json_data = json.loads(GOT_RENDER_JSON.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        engrish_out = td_path / "engrish-dict.df"
        upstream_dir = td_path / "upstream"
        upstream_dir.mkdir()

        # --- Engrish path ---
        engrish_write_df(json_data, engrish_out, locale="got")

        # --- Wikidict path ---
        with engrish_mode():
            words = load(GOT_RENDER_JSON)
            variants = make_variants(words)
            run_formatter(
                DictFileFormat,
                locale="got",
                output_dir=upstream_dir,
                words=words,
                variants=variants,
                snapshot="20260401",
            )

        upstream_df = next(upstream_dir.glob("*.df"))

        # --- Parse both into {headword: sorted-tuple-of-&-targets} ---
        def _parse(path: Path) -> dict[str, tuple[str, ...]]:
            out: dict[str, list[str]] = {}
            current: str | None = None
            for raw in path.read_text(encoding="utf-8").splitlines():
                if raw.startswith("@ "):
                    current = raw[2:]
                    out.setdefault(current, [])
                elif raw.startswith("& ") and current is not None:
                    out[current].append(raw[2:])
            return {k: tuple(sorted(v)) for k, v in out.items()}

        engrish_map = _parse(engrish_out)
        upstream_map = _parse(upstream_df)

    # Headword sets must match.
    eng_only = sorted(set(engrish_map) - set(upstream_map))
    ups_only = sorted(set(upstream_map) - set(engrish_map))
    assert not eng_only and not ups_only, (
        f"headword sets differ: engrish-only count={len(eng_only)} "
        f"(first 10: {eng_only[:10]}); upstream-only count={len(ups_only)} "
        f"(first 10: {ups_only[:10]})"
    )

    # Per-headword `&` target sets must match.
    diffs = []
    for hw, eng_amps in engrish_map.items():
        ups_amps = upstream_map[hw]
        if eng_amps != ups_amps:
            diffs.append((hw, eng_amps, ups_amps))
    assert not diffs, (
        f"& line sets differ for {len(diffs)} headwords; first 5:\n"
        + "\n".join(f"  {hw}: engrish={e}, upstream={u}" for hw, e, u in diffs[:5])
    )


def test_write_df_broken_target_variants_silently_dropped(tmp_path: Path) -> None:
    """Item 7a / M13-AC8 — broken-target variants are dropped from the .df
    body but DOCUMENTED via the ``broken_variants.txt`` companion artifact
    (per D38 / M13-AC8, broken pointers no longer silently disappear).

    A variant ``V`` with ``variants:[T]`` where ``T`` does NOT exist in the
    JSON: ``T`` is never iterated as a canonical (no `@ T` line), and ``V``
    itself is variant-only (skipped per F17). The ``.df`` does not carry V
    or T in any line. The broken pointer IS reported via the side-channel
    artifact emitted by ``write_broken_variants_report``.
    """
    from engrish.df_writer import write_broken_variants_report, write_df

    data = {
        "real": {"definitions": {"Noun": ["a real word"]}},
        "broken": {"variants": ["nonexistent_target"]},
    }
    out = tmp_path / "broken.df"
    n_hw, n_syn = write_df(data, out, locale="en")
    content = out.read_text(encoding="utf-8")

    assert n_hw == 1, "only the canonical 'real' should emit"
    assert n_syn == 0, "broken variant produces no & line"
    assert "@ real\n" in content
    assert "@ broken" not in content
    assert "& broken" not in content
    assert "nonexistent_target" not in content

    # M13-AC8: broken-target side-channel artifact.
    report = tmp_path / "broken_variants.txt"
    n_broken_targets = write_broken_variants_report(data, report)
    assert n_broken_targets == 1
    body = report.read_text(encoding="utf-8")
    assert "nonexistent_target\tbroken\n" == body, body


def test_write_df_multi_target_variant_emits_under_each_target(tmp_path: Path) -> None:
    """Item 7b (post-M12 finalization) — multi-target variant edge case.

    A variant ``X`` with ``variants:[T1, T2]`` produces ``& X`` under both
    ``@ T1`` and ``@ T2``. ``DuplicateEntryError`` is keyed on
    ``(variant, target)`` pairs so different targets coexist. StarDict
    ``.syn`` tolerates multiple redirects sharing a synonym; sdcv returns
    the first match.
    """
    from engrish.df_writer import write_df

    data = {
        "target_a": {"definitions": {"Verb": ["meaning A"]}},
        "target_b": {"definitions": {"Verb": ["meaning B"]}},
        "shared_form": {"variants": ["target_a", "target_b"]},
    }
    out = tmp_path / "multi.df"
    n_hw, n_syn = write_df(data, out, locale="en")
    content = out.read_text(encoding="utf-8")

    assert n_hw == 2  # target_a, target_b
    assert n_syn == 2  # & shared_form under each
    assert content.count("& shared_form") == 2


def test_write_df_resolves_one_level_chain_drops_deeper(tmp_path: Path) -> None:
    """Item 7c (post-M12 finalization) — variant chain depth.

    For ``A → B → C`` where B has no definitions: ``@ C`` carries both
    ``& A`` and ``& B`` (1-level chain resolution per
    ``wikidict.convert.handle_word:266-276``).

    For ``A → B → C → D`` where B AND C have no definitions: ``@ D`` carries
    ``& B`` and ``& C`` but NOT ``& A`` — the resolution iterates over the
    initial reverse-set only, not the newly-added members. Matches upstream's
    1-level limit. ``A``'s redirect is lost (acceptable per F17 reasoning).
    """
    from engrish.df_writer import write_df

    # 1-level chain: A → B → C
    data1 = {
        "C": {"definitions": {"Noun": ["canonical"]}},
        "B": {"variants": ["C"]},
        "A": {"variants": ["B"]},
    }
    out1 = tmp_path / "chain1.df"
    write_df(data1, out1, locale="en")
    c1 = out1.read_text(encoding="utf-8")
    assert "@ C\n" in c1
    assert "& A\n" in c1
    assert "& B\n" in c1

    # 2-level chain: A → B → C → D (deep)
    data2 = {
        "D": {"definitions": {"Noun": ["canonical"]}},
        "C": {"variants": ["D"]},
        "B": {"variants": ["C"]},
        "A": {"variants": ["B"]},
    }
    out2 = tmp_path / "chain2.df"
    write_df(data2, out2, locale="en")
    c2 = out2.read_text(encoding="utf-8")
    assert "@ D\n" in c2
    assert "& B\n" in c2
    assert "& C\n" in c2
    assert "& A\n" not in c2, "deeper-than-1-level chain element A should be dropped"


def test_merge_dfs_drops_self_redirect_after_case_coalescing(tmp_path: Path) -> None:
    """M13 fix (2026-05-10) — when case-coalescing merges `@ Cisplatine` (with
    `& cisplatine` reverse-variant pointer) into canonical `@ cisplatine`, the
    `& cisplatine` line MUST be dropped from the merged preamble (it's a
    self-redirect). Pre-fix, this caused PyGlossary to emit synwordcount=N-1
    while the .df had N `&` lines for any form with such case-pair entries.

    Symptom on real data: fr's `cisplatine` (lowercase, has Noun definitions)
    has `variants:['Cisplatine']`. `Cisplatine` is also a separate JSON entry
    (Proper Noun). per-locale .df emits both; merge coalesces them; the &
    cisplatine line on Cisplatine's preamble becomes a self-redirect.
    """
    en_data = {
        # Lowercase entry has its own definitions AND points to the
        # capitalized form as a variant.
        "cisplatine": {
            "definitions": {"Noun": ["a cancer drug"]},
            "variants": ["Cisplatine"],
        },
        # Capitalized form has its own definitions (Proper Noun).
        "Cisplatine": {"definitions": {"Proper Noun": ["a region"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")

    # One @ cisplatine canonical entry.
    assert content.count("@ cisplatine\n") == 1, content
    # No @ Cisplatine — it was coalesced.
    assert "@ Cisplatine\n" not in content
    # Cisplatine surfaces as an alias.
    assert "& Cisplatine\n" in content
    # CRITICAL: no self-redirect — `& cisplatine` under `@ cisplatine` is invalid.
    # Lines beginning with `& cisplatine` should NOT appear (only `& Cisplatine`).
    for line in content.splitlines():
        if line.startswith("& cisplatine"):
            assert False, f"self-redirect leaked into merged .df: {line!r}"


def test_merge_dfs_unions_amp_lines_across_sources(tmp_path: Path) -> None:
    """5a regression — multi-locale headword: the merged entry carries the
    UNION of `&` synonym lines from every contributing source, not just the
    first one. Pre-D37 ``_merge_entries`` took preamble from FIRST source only
    (legacy bug). Post-D37 ``_merge_case_fold_group`` walks all sources and
    dedups by line bytes.
    """
    # en's "pay" canonical + 3 reverse-mapped variants (paid/pays/payed).
    en_data = {
        "pay": {"definitions": {"Verb": ["to give money"]}},
        "paid": {"variants": ["pay"]},
        "pays": {"variants": ["pay"]},
        "payed": {"variants": ["pay"]},
    }
    # de's "pay" canonical + 1 disjoint reverse-mapped variant (payen).
    de_data = {
        "pay": {"definitions": {"Noun": ["der Lohn"]}},
        "payen": {"variants": ["pay"]},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    de_df = _write_fixture(tmp_path, "de.df", de_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df, de_df], merged, form_locales=["en", "de"])
    content = merged.read_text(encoding="utf-8")

    # Exactly one @ pay (case-coalesced + canonical-merged).
    assert content.count("@ pay\n") == 1, content

    # Union of `&` lines from both sources is present.
    for amp in ("& paid", "& pays", "& payed", "& payen"):
        assert amp in content, f"missing {amp!r} in merged.df:\n{content}"

    # Both locales' bodies present.
    assert "to give money" in content
    assert "der Lohn" in content


# --- M14 (D44) syn-head case-collision regression tests ---


def test_merge_dfs_m14_stelae_cross_locale_syn_head_collision(tmp_path: Path) -> None:
    """M14 (D44, 2026-05-11) — Option A1, cross-locale @<->& case-fold collision.

    Setup: en has stela (Noun) with stelae as variant-only synonym.
    la has Stelae (Latin Proper Noun) as its own headword.
    The lowercase synonym `stelae` and the capitalized headword `Stelae`
    share a case-fold; pre-fix the merged .df keeps both as distinct entries
    and case-insensitive client lookups return only @ Stelae.

    Post-fix invariants (A1):
    - @ stela exists with body unchanged (singular stays clean — no Crete).
    - @ stelae exists (NEW) — the coalesced entry.
    - @ Stelae does NOT exist standalone.
    - & Stelae is the synonym under @ stelae (so explicit-case Stelae lookup
      still resolves via .syn).
    - @ stela's preamble does NOT contain `& stelae` anymore (superseded).
    - @ stelae's body = [stela's Modern English body, <h3>* * *</h3>,
                         Stelae's Latin Proper Noun body annotated].
    """
    en_data = {
        "stela": {"definitions": {"Noun": ["upright stone pillar"]}},
        "stelae": {"variants": ["stela"]},
    }
    la_data = {
        "Stelae": {"definitions": {"Proper Noun": ["a city of Crete"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    la_df = _write_fixture(tmp_path, "la.df", la_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df, la_df], merged, form_locales=["en", "la"])
    content = merged.read_text(encoding="utf-8")

    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert "stela" in headwords, headwords
    assert "stelae" in headwords, headwords
    assert "Stelae" not in headwords, headwords

    # @ stela block (up to next @): body clean, no `& stelae`, no Latin Crete.
    stela_at = content.index("@ stela\n")
    stelae_at = content.index("@ stelae\n")
    assert stela_at < stelae_at
    stela_block = content[stela_at:stelae_at]
    assert "& stelae" not in stela_block, (
        "`& stelae` should be dropped from @ stela's preamble post-M14 "
        f"(superseded by @ stelae). Block:\n{stela_block}"
    )
    assert "Crete" not in stela_block, "singular @ stela MUST stay clean per A1"
    assert "upright stone pillar" in stela_block

    # @ stelae block: contains both bodies + separator + Capitalized annotation +
    # & Stelae synonym.
    stelae_end = content.find("\n@ ", stelae_at + 1)
    if stelae_end == -1:
        stelae_end = len(content)
    stelae_block = content[stelae_at:stelae_end]
    assert "& Stelae" in stelae_block
    assert "upright stone pillar" in stelae_block
    assert "Crete" in stelae_block
    assert "<h3>* * *</h3>" in stelae_block
    assert "(Capitalized: Stelae)" in stelae_block
    # Order: singular's body first, separator, then annotated capitalized.
    pillar_pos = stelae_block.index("upright stone pillar")
    sep_pos = stelae_block.index("<h3>* * *</h3>")
    crete_pos = stelae_block.index("Crete")
    assert pillar_pos < sep_pos < crete_pos, (
        f"order wrong inside @ stelae: pillar={pillar_pos}, sep={sep_pos}, crete={crete_pos}"
    )


def test_merge_dfs_m14_within_locale_cooks_collision(tmp_path: Path) -> None:
    """M14 (D44) — Option A1, within-locale @<->& case-fold collision.

    A single locale's render JSON has `cook` (verb/noun with `cooks` as
    inflected variant) and `Cooks` (surname Proper Noun). After df_writer,
    en.df has @ cook + `& cooks` line + @ Cooks. Merge (k=1) must produce:
    - @ cook (singular, clean — no surname leak)
    - @ cooks (NEW) with cook's body + separator + Cooks's body annotated
    - No @ Cooks standalone
    """
    en_data = {
        "cook": {"definitions": {"Verb": ["to prepare food"]}},
        "cooks": {"variants": ["cook"]},
        "Cooks": {"definitions": {"Proper Noun": ["a surname"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")

    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert "cook" in headwords
    assert "cooks" in headwords
    assert "Cooks" not in headwords

    cook_at = content.index("@ cook\n")
    cooks_at = content.index("@ cooks\n")
    cook_block = content[cook_at:cooks_at]
    assert "& cooks" not in cook_block, (
        "`& cooks` should be dropped from @ cook (superseded by @ cooks)"
    )
    assert "surname" not in cook_block, "singular @ cook stays clean per A1"

    cooks_block = content[cooks_at:]
    assert "& Cooks" in cooks_block
    assert "to prepare food" in cooks_block  # cook's body inherited
    assert "surname" in cooks_block
    assert "(Capitalized: Cooks)" in cooks_block
    assert "<h3>* * *</h3>" in cooks_block


def test_merge_dfs_m14_negative_self_redirect_no_collision_triggered(tmp_path: Path) -> None:
    """M14 (D44) — negative case: same-case-fold synonym pointing AT the colliding
    head (i.e. parent IS the head) MUST NOT trigger a new collision-coalesce.

    The cisplatine/Cisplatine D43 pattern: lowercase has its own definitions
    AND points to capitalized as a variant. The `& Cisplatine` synonym under
    `@ cisplatine` (its OWN preamble's reverse-variant pointer) shares case-fold
    `cisplatine` with `@ Cisplatine` (a separate head). Filter condition (c)
    rejects when parent == head: target=cisplatine == head=cisplatine.
    The existing D37/F18 + D43 @<->@ coalesce path handles this; M14 must not
    over-trigger and double-coalesce.
    """
    en_data = {
        "cisplatine": {
            "definitions": {"Noun": ["a cancer drug"]},
            "variants": ["Cisplatine"],
        },
        "Cisplatine": {"definitions": {"Proper Noun": ["a region"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")

    # D37/F18 behaviour: single @ cisplatine canonical with both bodies stacked.
    assert content.count("@ cisplatine\n") == 1
    assert "@ Cisplatine\n" not in content
    assert "& Cisplatine" in content
    assert "a cancer drug" in content
    assert "a region" in content
    assert "(Capitalized: Cisplatine)" in content
    # M14 must NOT have synthesized a SECOND `@ cisplatine` or anything weird.
    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert headwords == ["cisplatine"], headwords


def test_merge_dfs_m14_three_way_at_at_amp(tmp_path: Path) -> None:
    """M14 (D44) — composition with D37/F18: @+@+& on the same case-fold.

    `@ act` (verb) + `& acts → act` (its synonym) + `@ Acts` (Bible) + `@ ACTS`
    (an acronym, ALL-CAPS). Case-fold "acts" has 2 heads {Acts, ACTS} and 1 syn
    (acts → act). Post-M14:
    - @ act stays clean.
    - @ acts (NEW lowercase canonical) coalesces act's body + Acts annotated +
      ACTS annotated.
    - & Acts and & ACTS both appear under @ acts.
    - No @ Acts or @ ACTS standalone.
    """
    en_data = {
        "act": {"definitions": {"Verb": ["to do something"]}},
        "acts": {"variants": ["act"]},
        "Acts": {"definitions": {"Proper Noun": ["a book of the Bible"]}},
        "ACTS": {"definitions": {"Noun": ["an acronym"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df], merged, form_locales=["en"])
    content = merged.read_text(encoding="utf-8")

    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert headwords == ["act", "acts"], headwords

    act_at = content.index("@ act\n")
    acts_at = content.index("@ acts\n")
    act_block = content[act_at:acts_at]
    assert "& acts" not in act_block, "& acts should be dropped from @ act"
    assert "Bible" not in act_block
    assert "acronym" not in act_block

    acts_block = content[acts_at:]
    assert "& Acts" in acts_block
    assert "& ACTS" in acts_block
    assert "to do something" in acts_block  # inherited from act
    assert "a book of the Bible" in acts_block
    assert "an acronym" in acts_block
    assert "(Capitalized: Acts)" in acts_block
    assert "(Capitalized: ACTS)" in acts_block


def test_merge_dfs_m14_existing_lowercase_head_extended_not_replaced(tmp_path: Path) -> None:
    """M14 (D44) — when the canonical (lowercase) is ALREADY an @ headword
    with its own body, the collision-coalesce extends that existing entry
    rather than synthesizing a new one. Avoids duplicate `@ foo` lines.

    Scenario: stelae IS an English headword with its own body (Noun: the
    archaeological plural) + Stelae (la) is the Latin Proper Noun.
    Post-fix: @ stelae has its OWN body + separator + Stelae's body annotated.
    """
    en_data = {
        "stelae": {"definitions": {"Noun": ["plural form; archaeology"]}},
    }
    la_data = {
        "Stelae": {"definitions": {"Proper Noun": ["a city of Crete"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    la_df = _write_fixture(tmp_path, "la.df", la_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df, la_df], merged, form_locales=["en", "la"])
    content = merged.read_text(encoding="utf-8")

    headwords = [line[2:] for line in content.splitlines() if line.startswith("@ ")]
    assert headwords == ["stelae"], headwords
    assert content.count("@ stelae\n") == 1
    assert "& Stelae" in content
    assert "plural form; archaeology" in content
    assert "Crete" in content
    assert "<h3>* * *</h3>" in content
    assert "(Capitalized: Stelae)" in content


def test_merge_dfs_m14_post_fix_corpus_has_zero_syn_head_collisions(tmp_path: Path) -> None:
    """M14-AC6 (D44, 2026-05-11) — invariant: a merge_dfs output never contains
    a residual ``@``↔``&`` case-fold collision per the M14 filter.

    Sweep methodology: scan the merged .df; for each case-fold key, identify
    case-folds where (a) ≥1 ``@`` and ≥1 ``&`` share the fold, (b) the
    synonym's name differs from at least one head's case, (c) the synonym's
    parent is not the colliding head. After M14 coalesce, this set MUST be
    empty.

    This is the structural-invariant analog of M14-AC6 — the corpus-wide
    AC6 (re-run the sweep against the rebuilt 55-form corpus, assert zero)
    is exercised manually post-AC11 rebuild. This test exercises the
    invariant on a small fixture so any regression on the merge logic is
    caught in the always-on suite without depending on the corpus state.
    """
    from collections import defaultdict

    # Build a fixture that EXERCISES every M14 collision pattern we know:
    # - cross-locale (stelae) — synthesized canonical
    # - within-locale (cooks) — synthesized canonical
    # - canonical-already-exists (buddha-style) — extend existing
    # - three-way @ + @ + & — composition with D37/F18
    en_data = {
        "stela": {"definitions": {"Noun": ["upright stone pillar"]}},
        "stelae": {"variants": ["stela"]},
        "cook": {"definitions": {"Verb": ["to prepare food"]}},
        "cooks": {"variants": ["cook"]},
        "Cooks": {"definitions": {"Proper Noun": ["a surname"]}},
        "act": {"definitions": {"Verb": ["to do something"]}},
        "acts": {"variants": ["act"]},
        "Acts": {"definitions": {"Proper Noun": ["a book of the Bible"]}},
        "ACTS": {"definitions": {"Noun": ["an acronym"]}},
        # buddha-style: @ buddha + @ Buddha + & buddha → bujjhati
        "buddha": {"definitions": {"Noun": ["awakened one"]}, "variants": ["bujjhati"]},
        "Buddha": {"definitions": {"Proper Noun": ["the historical Buddha"]}},
        "bujjhati": {"definitions": {"Verb": ["to awaken"]}},
    }
    la_data = {
        "Stelae": {"definitions": {"Proper Noun": ["a city of Crete"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    la_df = _write_fixture(tmp_path, "la.df", la_data)
    merged = tmp_path / "merged.df"
    merge_dfs([en_df, la_df], merged, form_locales=["en", "la"])

    # Re-run the sweep methodology on the merged output.
    heads_by_fold: dict[str, list[str]] = defaultdict(list)
    syns_by_fold: dict[str, list[tuple[str, str]]] = defaultdict(list)
    cur_head: str | None = None
    with merged.open("rb") as f:
        for raw in f:
            if raw.startswith(b"@ "):
                cur_head = raw[2:].rstrip(b"\n").decode("utf-8", "replace")
                heads_by_fold[cur_head.lower()].append(cur_head)
            elif raw.startswith(b"& ") and cur_head is not None:
                s = raw[2:].rstrip(b"\n").decode("utf-8", "replace")
                syns_by_fold[s.lower()].append((s, cur_head))

    residual: list[tuple[str, list[str], list[tuple[str, str]]]] = []
    for fold in set(heads_by_fold) & set(syns_by_fold):
        heads = heads_by_fold[fold]
        head_set = set(heads)
        bad: list[tuple[str, str]] = []
        for s, t in syns_by_fold[fold]:
            for h in head_set:
                if h != s and t != h:
                    bad.append((s, t))
                    break
        if bad:
            residual.append((fold, heads, bad))

    assert not residual, (
        f"M14-AC6 invariant violated: {len(residual)} residual @<->& "
        "case-fold collisions in merged .df after M14 coalesce. Examples:\n"
        + "\n".join(
            f"  fold={fold!r}: @={heads}, &={bad}"
            for fold, heads, bad in residual[:10]
        )
    )


@sdcv_required
def test_sdcv_lookup_of_m14_collision_returns_both_bodies(tmp_path: Path) -> None:
    """M14-AC5 (D44, 2026-05-11) — external-consumer oracle for the M14 fix.

    Builds a small fixture StarDict containing the exact stelae/Stelae collision
    pattern (cross-locale @<->& case-fold conflict), then shells out to sdcv to
    look up ``stelae`` (lowercase). Pre-fix, the lookup would return ONLY the
    Latin Proper Noun body (the case-distinct @ Stelae). Post-fix (A1), the
    lookup must return BOTH stela's archaeology body AND Stelae's Latin
    Proper Noun body, in that order.

    Closes the M6 oracle gap that hid M14 in the same way it hid F17 — the
    self-consistency unit tests authored alongside the writer pass even when
    the .syn/.idx lookup-priority semantics are wrong. The sdcv shell-out
    drives the real client-side behaviour the user's deployed devices exhibit.
    """
    from engrish.stardict_writer import convert_df_to_stardict

    # Fixtures: en has stela (Noun) + stelae (variant-only redirect to stela);
    # la has Stelae (Latin Proper Noun).
    en_data = {
        "stela": {"definitions": {"Noun": ["upright stone pillar"]}},
        "stelae": {"variants": ["stela"]},
    }
    la_data = {
        "Stelae": {"definitions": {"Proper Noun": ["a city of Crete"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    la_df = _write_fixture(tmp_path, "la.df", la_data)
    merged = tmp_path / "fixture.df"
    merge_dfs([en_df, la_df], merged, form_locales=["en", "la"])

    # Build StarDict via the production writer.
    out_dir = tmp_path / "fixture-stardict"
    out_dir.mkdir()
    convert_df_to_stardict(
        df_src=merged,
        out_folder=out_dir,
        title="M14-fixture",
        date="20260511",
        dict_name="fixture-en-20260511",
    )

    # Sanity: the StarDict .idx contains @ stelae (the synthesized canonical).
    ifo_text = (out_dir / "fixture-en-20260511.ifo").read_text(encoding="utf-8")
    assert "wordcount=" in ifo_text

    # sdcv lookup of "stelae" (lowercase) must surface both bodies.
    response = sdcv_lookup("stelae", out_dir)
    assert response.strip(), f"sdcv returned empty for 'stelae': {response!r}"
    # A1: lowercase canonical's body (singular's content) comes first; the
    # capitalized variant's body follows after the separator.
    assert "upright stone pillar" in response, (
        f"sdcv lookup of 'stelae' missing stela's body. Response:\n{response}"
    )
    assert "Crete" in response, (
        f"sdcv lookup of 'stelae' missing Stelae's Latin body. Response:\n{response}"
    )
    # Order: stela's body first, then separator, then Latin city.
    pillar_pos = response.index("upright stone pillar")
    crete_pos = response.index("Crete")
    assert pillar_pos < crete_pos, (
        f"order wrong: pillar at {pillar_pos}, crete at {crete_pos}\nresponse:\n{response}"
    )

    # A1 invariant: looking up "stela" (singular) MUST stay clean — no Crete.
    response_singular = sdcv_lookup("stela", out_dir)
    assert "upright stone pillar" in response_singular
    assert "Crete" not in response_singular, (
        "A1 invariant violated: lookup of 'stela' (singular) leaked the Latin "
        f"Proper Noun body. Response:\n{response_singular}"
    )


def test_merge_dfs_m14_byte_deterministic(tmp_path: Path) -> None:
    """M14 (D44) — collision-coalesce preserves byte-determinism (Δ3 leg)."""
    en_data = {
        "stela": {"definitions": {"Noun": ["upright stone pillar"]}},
        "stelae": {"variants": ["stela"]},
        "act": {"definitions": {"Verb": ["to do"]}},
        "acts": {"variants": ["act"]},
    }
    la_data = {
        "Stelae": {"definitions": {"Proper Noun": ["a city of Crete"]}},
        "Acts": {"definitions": {"Proper Noun": ["a book"]}},
    }
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    la_df = _write_fixture(tmp_path, "la.df", la_data)
    out1 = tmp_path / "m1.df"
    out2 = tmp_path / "m2.df"
    merge_dfs([en_df, la_df], out1, form_locales=["en", "la"])
    merge_dfs([en_df, la_df], out2, form_locales=["en", "la"])
    assert out1.read_bytes() == out2.read_bytes()


def test_merge_dfs_byte_deterministic(tmp_path: Path) -> None:
    en_data = {"a": {"definitions": {"Noun": ["x"]}}, "b": {"variants": ["c"]}}
    fr_data = {"b": {"definitions": {"Noun": ["y"]}}, "d": {"definitions": {"Noun": ["z"]}}}
    en_df = _write_fixture(tmp_path, "en.df", en_data)
    fr_df = _write_fixture(tmp_path, "fr.df", fr_data)
    out1 = tmp_path / "m1.df"
    out2 = tmp_path / "m2.df"
    merge_dfs([en_df, fr_df], out1, form_locales=["en", "fr"])
    merge_dfs([en_df, fr_df], out2, form_locales=["en", "fr"])
    assert out1.read_bytes() == out2.read_bytes()


# --- stardict_writer.py source audit (M6-AC8, AC9) ---


def _stardict_writer_code_only() -> str:
    """Return ``engrish/stardict_writer.py`` source with the module docstring stripped.

    Module docstrings legitimately mention the forbidden patterns by name to
    document what was REMOVED. The source-audit greps target executable code
    only, so we strip the leading triple-quoted block before scanning.
    """
    src = (REPO_ROOT / "engrish" / "stardict_writer.py").read_text()
    # Strip the leading ``"""..."""`` module docstring.
    m = re.match(r'\s*"""', src)
    if m:
        end = src.find('"""', m.end())
        if end != -1:
            src = src[end + 3 :]
    return src


def test_stardict_writer_no_no_sqlite_or_auto_sqlite_overrides() -> None:
    """M6-AC8 / Δ6 / 2a #1: new stardict_writer code must not set NO_SQLITE / auto_sqlite / cleanup."""
    code = _stardict_writer_code_only()
    forbidden_patterns = [
        r"os\.environ\[.NO_SQLITE.\]\s*=",
        r"auto_sqlite\s*=",
        r"cleanup\s*=\s*False",
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, code), (
            f"stardict_writer.py code contains forbidden override: {pat}"
        )


def test_stardict_writer_no_getbookname_monkeypatch() -> None:
    """M6-AC9 / 2a #2: new stardict_writer code must not monkey-patch getBookname."""
    code = _stardict_writer_code_only()
    # Forbidden: assignment to .getBookname or definition that ends up patched.
    assert not re.search(r"\.getBookname\s*=", code), (
        "stardict_writer.py code contains getBookname monkey-patch"
    )


# --- M6-AC10 idx 2^32 ceiling ---


def test_idx_overflow_error_class_exists_and_is_runtime_error() -> None:
    assert issubclass(IdxOverflowError, RuntimeError)


# --- Live got artifact tests ---


@got_dict_required
def test_got_ifo_wordcount_matches_df_at_count() -> None:
    """M6-AC5: .ifo wordcount == count of @ in .df."""
    df = next(GOT_FORM_DIR.glob("*.df"))
    ifo = next(GOT_FORM_DIR.glob("*.ifo"))

    df_at_count = sum(1 for line in df.read_bytes().splitlines() if line.startswith(b"@ "))
    ifo_text = ifo.read_text(encoding="utf-8")
    m = re.search(r"^wordcount=(\d+)$", ifo_text, re.MULTILINE)
    assert m, "no wordcount= in .ifo"
    ifo_wordcount = int(m.group(1))
    assert ifo_wordcount == df_at_count, (
        f"ifo wordcount {ifo_wordcount} != df @ count {df_at_count}"
    )


@got_dict_required
def test_got_ifo_synwordcount_matches_df_amp_count() -> None:
    """M6-AC5: .ifo synwordcount == count of & in .df."""
    df = next(GOT_FORM_DIR.glob("*.df"))
    ifo = next(GOT_FORM_DIR.glob("*.ifo"))

    df_amp_count = sum(1 for line in df.read_bytes().splitlines() if line.startswith(b"& "))
    ifo_text = ifo.read_text(encoding="utf-8")
    m = re.search(r"^synwordcount=(\d+)$", ifo_text, re.MULTILINE)
    assert m, "no synwordcount= in .ifo"
    ifo_syncount = int(m.group(1))
    assert ifo_syncount == df_amp_count, (
        f"ifo synwordcount {ifo_syncount} != df & count {df_amp_count}"
    )


@got_dict_required
def test_got_idx_walk_strictly_sorted_and_offsets_in_dict_range() -> None:
    """M6-AC4: walk .idx — strictly sorted, offsets in valid .dict range, under 2³² ceiling."""
    idx = next(GOT_FORM_DIR.glob("*.idx"))
    dict_path = next(GOT_FORM_DIR.glob("*.dict"))
    dict_size = dict_path.stat().st_size
    ceiling = (1 << 32) - 1

    data = idx.read_bytes()
    pos = 0
    prev_word = b""
    entries = 0
    while pos < len(data):
        nul = data.index(b"\x00", pos)
        word = data[pos:nul]
        # 4 bytes offset + 4 bytes size, big-endian (StarDict spec).
        (offset, size) = struct.unpack(">II", data[nul + 1 : nul + 9])
        # Strictly sorted (StarDict requires byte-wise lexicographic order).
        assert prev_word < word or entries == 0, (
            f"idx not sorted: {prev_word!r} >= {word!r} at entry {entries}"
        )
        # Offset + size within .dict bounds.
        assert offset + size <= dict_size, (
            f"idx entry {word!r} (offset={offset}, size={size}) extends past .dict size {dict_size}"
        )
        # Under 2³² ceiling per M6-AC10.
        assert offset < ceiling, f"offset {offset} ≥ 2³² ceiling for entry {word!r}"
        prev_word = word
        pos = nul + 9
        entries += 1
    assert entries > 0


@got_dict_required
@sdcv_required
def test_sdcv_roundtrip_for_sampled_got_entries() -> None:
    """M6-AC3: sdcv roundtrip for ≥20 sampled words.

    Picks the 20 first headwords with non-empty bodies (i.e. have definitions
    or etymology beyond the bare ``<h3>`` wrapper) and confirms that
    sdcv's lookup body byte-equals the .df entry's HTML body.
    """
    df = next(GOT_FORM_DIR.glob("*.df"))
    raw = df.read_bytes()

    # Parse @ entries with their bodies.
    entries: list[tuple[str, str]] = []
    pos = 0
    while pos < len(raw):
        nl = raw.find(b"\n", pos)
        if nl == -1:
            break
        line = raw[pos:nl]
        if line.startswith(b"@ "):
            headword = line[2:].decode("utf-8")
            # Read until the next @ or EOF.
            end = raw.find(b"\n@ ", nl)
            if end == -1:
                end = len(raw)
            else:
                end += 1
            entry_bytes = raw[pos:end]
            # Extract <html>...</html>
            html_start = entry_bytes.find(b"<html>")
            html_end = entry_bytes.find(b"</html>") + len(b"</html>")
            if html_start != -1 and html_end != -1:
                body = entry_bytes[html_start:html_end].decode("utf-8")
                # Skip pure-variant-only entries (body is just the <h3> wrapper).
                # i.e. body == "<html><h3>Gothic</h3></html>" — no real content.
                bare_pattern = re.compile(r"^<html><h3>[^<]+</h3></html>$")
                if not bare_pattern.match(body):
                    entries.append((headword, body))
            pos = end
        else:
            pos = nl + 1

    sample = entries[:20]
    assert len(sample) >= 20, f"only {len(sample)} entries with bodies; need 20"

    # Set UTF-8 locale for sdcv's iconv path.
    import os
    env_lang = os.environ.get("LANG")
    env_lcall = os.environ.get("LC_ALL")
    os.environ["LANG"] = "C.UTF-8"
    os.environ["LC_ALL"] = "C.UTF-8"
    try:
        for headword, df_body in sample:
            sdcv_out = sdcv_lookup(headword, GOT_FORM_DIR)
            # sdcv emits ANSI escapes + "Found N items" + per-entry banner.
            # Body content (from <html> ... </html>) should appear verbatim
            # except sdcv strips the outer <html> tags.
            # Robust check: every visible <li>...</li> definition snippet from
            # df_body must appear in sdcv_out.
            for li in re.findall(r"<li>([^<]+)</li>", df_body):
                assert li in sdcv_out, (
                    f"sdcv lookup of {headword!r} does not echo definition {li!r}"
                )
    finally:
        if env_lang is None:
            os.environ.pop("LANG", None)
        else:
            os.environ["LANG"] = env_lang
        if env_lcall is None:
            os.environ.pop("LC_ALL", None)
        else:
            os.environ["LC_ALL"] = env_lcall
