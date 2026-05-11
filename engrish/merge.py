"""K-way streaming merge of per-locale ``.df`` files → merged ``.df``.

Consumes pre-sorted per-locale ``.df`` files (produced by
``engrish.df_writer.write_df``; sort key = ``(headword.lower(), headword)``)
and emits a single merged ``.df`` that preserves I1 sort order across the
whole corpus.

Per I8, every merged headword that appears in ≥ 2 locales carries exactly one
``<h3>{FORM_NAMES[locale]}</h3>`` section per contributing locale, stacked in
the order given by the caller (normally form / config order).

**Case-variant coalescing (D37, F18 — added 2026-05-04):** entries sharing a
case-folded headword are coalesced into one merged entry under a single
canonical headword. Canonical preference: the lowercase form if any source
provides one, else the lexicographically-first variant by byte. Bodies for
the canonical headword come first (across locales). Bodies for non-canonical
case-variants come after a ``<h3>* * *</h3>`` separator, with each h3
annotated as ``<h3>{LocaleName} (Capitalized: {variant_form})</h3>``. Each
non-canonical variant is also emitted as a ``& <variant>`` synonym in the
preamble so explicit-case lookups resolve to the coalesced entry via
StarDict's ``.syn`` mechanism. This eliminates the case-insensitive client
ambiguity where uppercase Proper-Noun homonyms (e.g. ``Duck``) clobbered
common-word lookups (e.g. ``duck``).

Algorithm: k-way merge via ``heapq`` over
``(case_fold, headword, source_index)`` tuples — case-fold primary, byte-wise
tiebreak. Each source is a generator of ``(headword, entry_bytes)`` from
``engrish.df_reader.iter_entries``. Entries with the same case-fold across
sources are batched and emitted as one coalesced entry; distinct case-folds
pass through untouched in case-folded sort order.

Byte-determinism (Δ3 leg): the merged output is deterministic given
identical-bytes inputs and a fixed source ordering.
"""

from __future__ import annotations

import heapq
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from engrish.df_reader import iter_entries

log = logging.getLogger(__name__)


_H3_RE = re.compile(rb"<h3>([^<]+)</h3>")


def _split_entry(entry_bytes: bytes) -> tuple[list[bytes], bytes]:
    """Split an entry's raw bytes into ``(preamble_lines, body_bytes)``.

    - ``preamble_lines`` = the ``@ ...`` line + any ``:`` pronunciation line +
      any ``& variant`` lines (every line before ``<html>...``).
    - ``body_bytes`` = everything after the last preamble line (``<html>...``
      through end of entry).
    """
    preamble: list[bytes] = []
    body_start = 0
    pos = 0
    while pos < len(entry_bytes):
        nl = entry_bytes.find(b"\n", pos)
        line_end = len(entry_bytes) if nl == -1 else nl + 1
        line = entry_bytes[pos:line_end]
        if line.startswith(b"@ ") or line.startswith(b":") or line.startswith(b"& "):
            preamble.append(line)
            body_start = line_end
            pos = line_end
        else:
            break
    body = entry_bytes[body_start:]
    return preamble, body


def _annotate_h3_with_capitalized_form(body: bytes, variant_hw: str) -> bytes:
    """Annotate every ``<h3>{name}</h3>`` in ``body`` to
    ``<h3>{name} (Capitalized: {variant_hw})</h3>``.

    Used to label case-variant bodies in the coalesced entry so the user can
    see which variant form a section came from. Per D37 we use "Capitalized"
    rather than "Proper Noun" because ~37% of capitalized headwords are NOT
    proper nouns (e.g. German nouns are all capitalized).
    """
    annotation = f" (Capitalized: {variant_hw})".encode("utf-8")

    def repl(m: re.Match) -> bytes:
        name = m.group(1)
        return b"<h3>" + name + annotation + b"</h3>"

    return _H3_RE.sub(repl, body)


def _pick_canonical(headwords: set[str]) -> str:
    """Pick the canonical form for a case-fold group.

    - If any headword is purely lowercase (``hw == hw.lower()``), pick the
      lexicographically-smallest such headword.
    - Otherwise (no lowercase variant), pick the lexicographically-smallest
      headword by byte order.
    """
    lowercase = sorted(hw for hw in headwords if hw == hw.lower())
    if lowercase:
        return lowercase[0]
    return sorted(headwords)[0]


def _merge_case_fold_group(
    entries: list[tuple[int, str, bytes]],
    form_locales: list[str],
) -> bytes:
    """Coalesce all entries sharing a case-fold key into one merged entry.

    ``entries`` is a list of ``(src_idx, headword, entry_bytes)``; all entries
    must share the same ``headword.lower()`` value (the case-fold key).

    Preamble:
    - ``@ <canonical>`` line.
    - ``:`` pronunciation: from the canonical's first source (form-locale order).
    - ``&`` lines: union of all sources' ``&`` lines, plus a ``& <variant>``
      line for every non-canonical case-variant headword in the group.
      Sorted by ``(len, byte-string)`` per upstream
      ``wikidict.convert.handle_word:325``.

    Body:
    - Bodies for entries whose headword == canonical, in form-locales order.
    - If the group also has non-canonical case-variants, a ``<h3>* * *</h3>``
      separator paragraph (D37 — Option A: classical typographic dinkus,
      ASCII, font-independent, every consumer renders it; chosen 2026-05-04
      after a Crosspoint reader was found to ignore ``<hr/>``).
    - Bodies for non-canonical case-variants (in form-locales order, then
      headword byte order), each with h3 annotated via
      ``_annotate_h3_with_capitalized_form``.
    """
    if not entries:
        raise ValueError("_merge_case_fold_group called with empty entries")

    distinct_hws = {hw for _, hw, _ in entries}
    canonical = _pick_canonical(distinct_hws)

    canonical_entries = [(idx, hw, eb) for idx, hw, eb in entries if hw == canonical]
    other_entries = [(idx, hw, eb) for idx, hw, eb in entries if hw != canonical]

    def _rank(src_idx: int) -> int:
        return src_idx if 0 <= src_idx < len(form_locales) else len(form_locales)

    canonical_sorted = sorted(canonical_entries, key=lambda x: (_rank(x[0]), x[1]))
    other_sorted = sorted(other_entries, key=lambda x: (_rank(x[0]), x[1]))

    # ---- Preamble ----
    pron_line: bytes | None = None
    seen_amp_lines: dict[bytes, None] = {}

    # Walk canonical sources first so the pronunciation comes from the canonical's
    # first contributing locale.
    for idx, hw, eb in canonical_sorted + other_sorted:
        pre, _ = _split_entry(eb)
        for line in pre:
            if line.startswith(b":"):
                if pron_line is None and hw == canonical:
                    pron_line = line
            elif line.startswith(b"& "):
                target_hw = line[2:].rstrip(b"\n").decode("utf-8", errors="replace")
                # Drop any & line that points to a non-canonical case-variant
                # in this same group — we synthesize fresh aliases for those
                # below to ensure deterministic, complete coverage.
                if target_hw in distinct_hws and target_hw != canonical:
                    continue
                # Drop any & line that points to the canonical itself —
                # that's a self-redirect on the merged @ entry. Happens when a
                # case-variant entry's per-locale .df contained `& canonical`
                # (e.g. proper-noun ``Cisplatine`` had `& cisplatine` because
                # cisplatine.variants includes ``Cisplatine`` → reverse-map
                # gave Cisplatine a ``cisplatine`` pointer); after coalescing
                # under ``@ cisplatine`` this would emit `& cisplatine`. Drop.
                if target_hw == canonical:
                    continue
                if line not in seen_amp_lines:
                    seen_amp_lines[line] = None

    # Add aliases for every non-canonical case-variant headword.
    for hw in sorted({hw for _, hw, _ in other_sorted}):
        alias = f"& {hw}\n".encode("utf-8")
        if alias not in seen_amp_lines:
            seen_amp_lines[alias] = None

    out_parts: list[bytes] = [f"@ {canonical}\n".encode("utf-8")]
    if pron_line is not None:
        out_parts.append(pron_line)

    # Sort & lines per upstream ``wikidict.convert:325`` ordering: shorter
    # variants first, then byte-wise.
    sorted_amps = sorted(seen_amp_lines.keys(), key=lambda s: (len(s), s))
    out_parts.extend(sorted_amps)

    # ---- Body ----
    body_parts: list[bytes] = []
    for idx, hw, eb in canonical_sorted:
        _, body = _split_entry(eb)
        body_parts.append(body)

    if canonical_sorted and other_sorted:
        # Case-variant coalescing separator (D37 Option A — universally
        # supported across HTML, sdcv text-mode, low-end e-ink readers).
        body_parts.append(b"<h3>* * *</h3>")

    for idx, hw, eb in other_sorted:
        _, body = _split_entry(eb)
        annotated = _annotate_h3_with_capitalized_form(body, hw)
        body_parts.append(annotated)

    out_parts.extend(body_parts)

    merged = b"".join(out_parts)
    if not merged.endswith(b"\n\n"):
        if merged.endswith(b"\n"):
            merged += b"\n"
        else:
            merged += b"\n\n"
    return merged


def _merge_entries(
    sources: list[tuple[int, bytes]],
    form_locales: list[str],
) -> bytes:
    """Backwards-compatible wrapper around ``_merge_case_fold_group``.

    Older callers (incl. the M6 test suite) pass ``(src_idx, entry_bytes)``
    without the headword. This wrapper extracts the headword from each
    entry's ``@`` line and delegates. For single-headword merges (the only
    case the wrapper is exercised with), behavior matches the new coalescer
    exactly: no case-variants, no separator, no annotation.
    """
    enriched: list[tuple[int, str, bytes]] = []
    for src_idx, entry_bytes in sources:
        pre, _ = _split_entry(entry_bytes)
        if not pre or not pre[0].startswith(b"@ "):
            raise ValueError(f"entry from src {src_idx} has no @ headword line")
        hw = pre[0][2:].rstrip(b"\n").decode("utf-8")
        enriched.append((src_idx, hw, entry_bytes))
    return _merge_case_fold_group(enriched, form_locales)


def merge_dfs(
    sources: list[Path],
    output: Path,
    form_locales: list[str],
) -> tuple[int, int]:
    """K-way merge ``sources[i]`` (per-locale ``.df``) → ``output`` (merged ``.df``).

    ``form_locales[i]`` must be the locale code corresponding to ``sources[i]``;
    lengths must match. Section-stack order on multi-locale headwords follows
    ``form_locales``.

    Heap key: ``(case_fold, headword, src_idx)`` — case-fold primary, byte
    tiebreak. Per-locale ``.df`` files MUST be written with the matching
    sort key (``engrish.df_writer.write_df`` does this since 2026-05-04).

    Returns ``(merged_headword_count, contributing_source_entry_count)``.
    """
    if len(sources) != len(form_locales):
        raise ValueError(
            f"sources ({len(sources)}) and form_locales ({len(form_locales)}) length mismatch"
        )

    output.parent.mkdir(parents=True, exist_ok=True)

    iters: list[Iterator[tuple[str, bytes]]] = [iter_entries(path) for path in sources]
    heap: list[tuple[str, str, int, tuple[str, bytes]]] = []
    for src_idx, it in enumerate(iters):
        try:
            first = next(it)
        except StopIteration:
            continue
        heap.append((first[0].lower(), first[0], src_idx, first))
    heapq.heapify(heap)

    merged_count = 0
    contributing_count = 0

    with output.open("wb") as out_fh:
        while heap:
            case_fold = heap[0][0]
            batch: list[tuple[int, str, bytes]] = []
            while heap and heap[0][0] == case_fold:
                _, hw, src_idx, (_, entry_bytes) = heapq.heappop(heap)
                batch.append((src_idx, hw, entry_bytes))
                contributing_count += 1
                try:
                    nxt = next(iters[src_idx])
                    heapq.heappush(heap, (nxt[0].lower(), nxt[0], src_idx, nxt))
                except StopIteration:
                    pass

            merged_bytes = _merge_case_fold_group(batch, form_locales)
            out_fh.write(merged_bytes)
            merged_count += 1

    return merged_count, contributing_count
