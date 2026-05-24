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

**Synonym-headword case-collision coalescing (D44, M14 — added 2026-05-11):**
extends D37/F18 to the sibling case where a ``@ Headword`` shares a case-fold
with a ``& synonym`` line living inside a *different* ``@ entry``'s preamble.
The user-reported example: ``& stelae`` (synonym of ``@ stela``, English
"plural of stela") collided with ``@ Stelae`` (Latin Proper Noun "city of
Crete"); client-side case-insensitive ``.idx`` lookup returned the Latin
Proper Noun and the synonym redirect was ignored. Per D44 Option A1, when
this collision is detected the merger synthesizes a new lowercase canonical
``@ <synonym>`` entry whose body stacks ``[parent's body, separator,
colliding-@'s body annotated]``. The original parent ``@`` keeps its body
unchanged (A1 invariant — singular form stays clean). The colliding ``@`` is
demoted: no standalone entry, only an ``& <Headword>`` synonym under the new
canonical. The ``& <synonym>`` line is dropped from the parent's preamble
(superseded by the new canonical). Implemented via a pre-pass that indexes
all sources' ``@`` heads + ``&`` synonyms, builds a collision plan, then
injects synthetic batch entries during the main merge so the existing
``_merge_case_fold_group`` machinery handles the coalescing uniformly.

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
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from engrish.df_reader import iter_entries

log = logging.getLogger(__name__)


_H3_RE = re.compile(rb"<h3>([^<]+)</h3>")


def _strip_html_envelope(body: bytes) -> bytes:
    """Strip a leading ``<html>`` and trailing ``</html>`` (plus trailing
    whitespace) from a per-locale body produced by ``df_writer.write_df``.

    Per-locale ``.df`` writes each entry's body as
    ``<html><h3>Locale</h3>{body}</html>\\n\\n``. When the merge stacks
    multiple locales (or D37/F18 case-variants, or D44/M14 synthetic bodies)
    under one ``@`` headword, those per-locale envelopes must be unwrapped so
    the merged body can be re-wrapped in a single outer envelope. Otherwise
    the merged entry carries N opens / N closes — strict SAX/XML renderers
    (e.g. some StarDict viewers) abort on the second root and truncate the
    on-screen entry to whatever rendered before the first close tag.
    """
    inner = body.rstrip(b" \t\r\n")
    if inner.startswith(b"<html>"):
        inner = inner[len(b"<html>"):]
    if inner.endswith(b"</html>"):
        inner = inner[:-len(b"</html>")]
    return inner


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
    dropped_syns_for_parent: dict[str, set[str]] | None = None,
) -> bytes:
    """Coalesce all entries sharing a case-fold key into one merged entry.

    ``entries`` is a list of ``(src_idx, headword, entry_bytes)``; all entries
    must share the same ``headword.lower()`` value (the case-fold key).

    ``dropped_syns_for_parent`` (D44 / M14): optional ``{parent_hw: {syn_name,
    ...}}`` filter. When emitting an entry whose headword is in this map, any
    ``& <syn_name>`` line in its preamble whose ``syn_name`` is in the filter
    set is dropped (superseded by a newly-synthesized lowercase canonical
    elsewhere in the merged output).

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

    drop_map = dropped_syns_for_parent or {}

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
        # D44 / M14: per-parent dropped-synonym filter. The collision pre-pass
        # may have marked some `&` lines in this entry's preamble as superseded
        # by a synthesized canonical living at a different case-fold; skip them.
        dropped_for_this = drop_map.get(hw, frozenset())
        for line in pre:
            if line.startswith(b":"):
                if pron_line is None and hw == canonical:
                    pron_line = line
            elif line.startswith(b"& "):
                target_hw = line[2:].rstrip(b"\n").decode("utf-8", errors="replace")
                # D44: drop & lines that the M14 collision pass marked
                # superseded (e.g. `& stelae` in `@ stela`'s preamble when a
                # new `@ stelae` canonical is synthesized for the Stelae
                # cross-locale collision).
                if target_hw in dropped_for_this:
                    continue
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
    # Strip the per-locale ``<html>...</html>`` envelope from every contributing
    # body. The merged entry is re-wrapped in a single outer envelope below so
    # strict SAX/XML renderers see exactly one well-formed root per entry.
    body_parts: list[bytes] = []
    for idx, hw, eb in canonical_sorted:
        _, body = _split_entry(eb)
        body_parts.append(_strip_html_envelope(body))

    if canonical_sorted and other_sorted:
        # Case-variant coalescing separator (D37 Option A — universally
        # supported across HTML, sdcv text-mode, low-end e-ink readers).
        body_parts.append(b"<h3>* * *</h3>")

    for idx, hw, eb in other_sorted:
        _, body = _split_entry(eb)
        annotated = _annotate_h3_with_capitalized_form(
            _strip_html_envelope(body), hw
        )
        body_parts.append(annotated)

    # Single outer envelope per merged entry. Multiple stacked ``<html>...
    # </html>`` blocks break strict SAX parsers (some StarDict viewers
    # truncate to the first close tag); one envelope satisfies the single-
    # root requirement.
    out_parts.append(b"<html>" + b"".join(body_parts) + b"</html>")

    merged = b"".join(out_parts)
    if not merged.endswith(b"\n\n"):
        if merged.endswith(b"\n"):
            merged += b"\n"
        else:
            merged += b"\n\n"
    return merged


def _index_sources_for_syn_head_collisions(
    sources: list[Path],
) -> tuple[dict[str, list[tuple[int, str]]], dict[str, list[tuple[int, str, str]]]]:
    """D44 / M14 pre-pass — index every source's ``@`` heads and ``&`` synonyms.

    Returns ``(heads_by_fold, syns_by_fold)`` where:
    - ``heads_by_fold[fold]`` = list of ``(src_idx, headword)`` for every ``@``
      whose case-fold key is ``fold``.
    - ``syns_by_fold[fold]`` = list of ``(src_idx, synonym_name, parent_headword)``
      for every ``& <synonym_name>`` line under ``@ <parent_headword>`` whose
      case-fold key is ``fold``.

    Streaming: reads each source via ``iter_entries`` but only inspects each
    entry's preamble; entry bodies stay on disk. Memory cost ~O(N_headwords *
    avg_headword_len + N_synonyms * (2 * avg_headword_len)) — for the 12-locale
    form ~3.5M heads + ~13M synonyms ≈ 250 MB peak. Acceptable within the 8 GB
    cap.
    """
    heads_by_fold: dict[str, list[tuple[int, str]]] = defaultdict(list)
    syns_by_fold: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for src_idx, src_path in enumerate(sources):
        for hw, entry_bytes in iter_entries(src_path):
            heads_by_fold[hw.lower()].append((src_idx, hw))
            preamble, _ = _split_entry(entry_bytes)
            for line in preamble:
                if line.startswith(b"& "):
                    syn = line[2:].rstrip(b"\n").decode("utf-8", errors="replace")
                    syns_by_fold[syn.lower()].append((src_idx, syn, hw))
    return heads_by_fold, syns_by_fold


def _build_syn_head_collision_plans(
    heads_by_fold: dict[str, list[tuple[int, str]]],
    syns_by_fold: dict[str, list[tuple[int, str, str]]],
) -> dict[str, dict]:
    """D44 / M14 — identify case-fold groups where ``@`` heads and ``&``
    synonyms collide, and build the coalesce plan.

    A fold ``F`` triggers a collision plan when there exists at least one
    ``& S → T`` synonym at fold ``F`` whose parent ``T`` lives at a *different*
    case-fold (``T.lower() != F``). The same-fold case (``T.lower() == F``) is
    a legitimate variant pointer between case-pair entries (e.g. the D43
    cisplatine pattern); D37/F18's existing ``@``↔``@`` coalesce handles it.

    Returns ``{fold: plan_dict}`` where ``plan_dict`` has:

    - ``canonical`` (str): the lowercase ``@`` headword. Lowercase form
      preferred from across all heads and synonyms participating in the
      collision; else lex-smallest by byte order.
    - ``demoted_heads`` (set[str]): heads in this fold whose case differs from
      the canonical. They emit no standalone ``@`` entry — they become
      ``& <head>`` synonyms under the canonical and contribute annotated
      bodies (via D37/F18's ``_merge_case_fold_group``).
    - ``dropped_syns_from_parent`` (dict[str, set[str]]): per-parent map of
      ``&`` lines to drop from that parent's preamble. Includes every syn at
      this fold whose parent is not the canonical — regardless of the
      synonym's case. The new ``@ <canonical>`` entry aggregates the bodies
      and supersedes every same-fold redirect.
    - ``contributing_parents`` (list[tuple[int, str]]): the ``(src_idx,
      parent_hw)`` pairs whose bodies stack into the canonical entry's body
      block. Deterministic order: input source order, then encounter order.
      Includes every parent referenced by a same-fold synonym (regardless of
      synonym case) so the user sees every body associated with the canonical's
      lookup form.
    - ``canonical_exists_as_head`` (bool): True if the canonical is also an
      existing ``@`` head in some source. Informational — the merge pass
      injects synthetic parent-body entries regardless; when the canonical
      already exists, those bodies stack alongside the real canonical's body.
    """
    plans: dict[str, dict] = {}
    for fold in sorted(set(heads_by_fold) & set(syns_by_fold)):
        head_items = heads_by_fold[fold]
        syn_items = syns_by_fold[fold]
        head_names = {hw for _, hw in head_items}

        # Same-fold variant pointers (parent's case-fold equals this fold) are
        # the D43 cisplatine pattern — handled by D37/F18 @<->@ coalesce. M14
        # only covers cross-fold parents.
        candidate_syns = [(sidx, s, t) for (sidx, s, t) in syn_items
                          if t.lower() != fold]
        if not candidate_syns:
            continue

        # Canonical: lowercase form preferred from across heads and candidate
        # syn names; else lex-smallest.
        participants = head_names | {s for _, s, _ in candidate_syns}
        all_variants = sorted(participants)
        lc_variants = [v for v in all_variants if v == v.lower()]
        canonical = lc_variants[0] if lc_variants else all_variants[0]

        canonical_exists_as_head = canonical in head_names

        # Demote every head whose case differs from the canonical.
        demoted = {hw for hw in head_names if hw != canonical}

        # Drop every & line at this fold whose parent isn't the canonical.
        # Includes both case-distinct syns (e.g. & Mannes → Mann when canonical
        # is mannes) AND same-case-as-canonical syns (e.g. & mannes → mann).
        # The new @ canonical supersedes the redirect; the parent's body is
        # already stacked into the canonical's body.
        dropped: dict[str, set[str]] = defaultdict(set)
        contributing_parents: list[tuple[int, str]] = []
        seen_parents: set[str] = set()
        for (sidx, s, t) in candidate_syns:
            if t == canonical:
                continue
            dropped[t].add(s)
            if t not in seen_parents:
                contributing_parents.append((sidx, t))
                seen_parents.add(t)

        plans[fold] = {
            "canonical": canonical,
            "demoted_heads": demoted,
            "dropped_syns_from_parent": {k: set(v) for k, v in dropped.items()},
            "contributing_parents": contributing_parents,
            "canonical_exists_as_head": canonical_exists_as_head,
        }
    return plans


def _collect_parent_bodies(
    sources: list[Path],
    needed_parents: set[tuple[int, str]],
) -> dict[tuple[int, str], bytes]:
    """D44 / M14 — second selective pass: fetch body bytes for parents whose
    bodies need to be stacked into a synthesized canonical entry.

    Only the bodies for ``needed_parents`` are loaded; everything else is
    streamed past. Returns ``{(src_idx, parent_hw): body_bytes}``.
    """
    parent_bodies: dict[tuple[int, str], bytes] = {}
    if not needed_parents:
        return parent_bodies
    per_src: dict[int, set[str]] = defaultdict(set)
    for sidx, hw in needed_parents:
        per_src[sidx].add(hw)
    for src_idx, src_path in enumerate(sources):
        wanted = per_src.get(src_idx)
        if not wanted:
            continue
        for hw, entry_bytes in iter_entries(src_path):
            if hw in wanted:
                _, body = _split_entry(entry_bytes)
                parent_bodies[(src_idx, hw)] = body
                wanted.discard(hw)
                if not wanted:
                    break
    return parent_bodies


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

    D44 / M14 (2026-05-11): pre-pass identifies ``@``↔``&`` case-fold
    collisions and builds a plan; the main merge loop injects synthetic
    entries (for synthesized canonicals) and filters dropped ``&`` lines from
    affected parents.

    Returns ``(merged_headword_count, contributing_source_entry_count)``.
    """
    if len(sources) != len(form_locales):
        raise ValueError(
            f"sources ({len(sources)}) and form_locales ({len(form_locales)}) length mismatch"
        )

    output.parent.mkdir(parents=True, exist_ok=True)

    # === D44 / M14 pre-pass: index sources for @<->& collision detection ===
    heads_by_fold, syns_by_fold = _index_sources_for_syn_head_collisions(sources)
    plans = _build_syn_head_collision_plans(heads_by_fold, syns_by_fold)

    # Collect (src_idx, parent_hw) pairs whose bodies we need to inject as
    # synthetic canonical bodies. Includes every contributing parent for
    # every plan (regardless of whether the canonical already exists as a
    # head — when it does, the synthetic bodies stack alongside the real
    # canonical's body in the merge output).
    needed_parents: set[tuple[int, str]] = set()
    for plan in plans.values():
        needed_parents.update(plan["contributing_parents"])
    parent_bodies = _collect_parent_bodies(sources, needed_parents)

    # Pre-compute per-fold synthetic batch additions and the global
    # dropped-syns filter passed to _merge_case_fold_group.
    synthetic_for_fold: dict[str, list[tuple[int, str, bytes]]] = {}
    dropped_syns_for_parent: dict[str, set[str]] = defaultdict(set)
    for fold, plan in plans.items():
        canonical = plan["canonical"]
        for parent_hw, syn_names in plan["dropped_syns_from_parent"].items():
            dropped_syns_for_parent[parent_hw].update(syn_names)
        # Synthesize a virtual @ <canonical> entry per contributing parent.
        # Each carries the parent's body verbatim (h3 unchanged — A1 invariant
        # says the parent's body content is what surfaces under the new
        # canonical for the singular/common reading).
        for sidx, parent_hw in plan["contributing_parents"]:
            body = parent_bodies.get((sidx, parent_hw))
            if body is None:
                continue
            virtual_bytes = f"@ {canonical}\n".encode("utf-8") + body
            synthetic_for_fold.setdefault(fold, []).append(
                (sidx, canonical, virtual_bytes)
            )

    # === Main merge pass ===
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

            # D44 / M14: inject synthetic canonical entries for this fold.
            if case_fold in synthetic_for_fold:
                batch.extend(synthetic_for_fold[case_fold])

            merged_bytes = _merge_case_fold_group(
                batch,
                form_locales,
                dropped_syns_for_parent=dropped_syns_for_parent,
            )
            out_fh.write(merged_bytes)
            merged_count += 1

    return merged_count, contributing_count
