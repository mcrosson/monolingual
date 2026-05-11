"""Streaming writer for per-locale ``.df`` files.

Produces ``pgaskin/dictutil``-compatible ``.df`` output from ``wikidict``
render JSON. Mirrors upstream ``wikidict.convert.handle_word`` semantics:

- A ``wikidict`` render-JSON entry's ``variants`` field lists *target*
  headwords this entry is a variant of (forward direction). Per
  ``wikidict.convert.make_variants`` (convert.py:1037), the writer must
  build the *reverse* mapping ``target → {variant_headwords}`` so that each
  ``& variant`` line lands under its ``@ target`` (the entry that has the
  definition).
- Per ``wikidict.convert:239``, variant-only entries (``is_variant`` and no
  own definitions) are *skipped* — they have no body content and exist only
  as redirects, which the ``& variant`` line under ``@ target`` already
  encodes via StarDict's ``.syn`` mechanism.
- Per ``wikidict.convert:266-276``, one redirection level is resolved
  (e.g. ``gastada → gastado → gastar``): when ``gastar`` is rendered, its
  ``& variants`` set picks up both ``gastado`` and ``gastada``.

Per I1, headwords are written in sorted order. Per I8, every entry's HTML
body is wrapped with a ``<h3>{FORM_NAMES[locale]}</h3>`` section header so
the M6 merge stage can stack sections by locale.

Guarantees:
- **M5-AC1** — sorted: callers MUST ensure the input dict is iterated in
  sorted-headword order. ``write_df`` does ``sorted(data.keys())``.
- **M5-AC2** — uniqueness: each ``@`` headword appears exactly once per file;
  each ``(variant, target)`` pair appears exactly once. Duplicates raise
  ``DuplicateEntryError`` (subclass of ``AssertionError``) mid-write.
- **M5-AC3** — byte-determinism: identical input → identical output bytes.
  Achieved by (1) sorted keys, (2) definitions iterated in insertion order
  (wikidict render JSON preserves POS order), (3) explicit ``newline="\\n"``
  to avoid OS-dependent line-ending translation, (4) ``&`` lines per entry
  sorted by ``(len(s), s)`` to mirror ``wikidict.convert:325``.

The per-entry template mirrors ``wikidict.convert.WORD_TPL_DICTFILE`` but
without its Jinja overhead and with the engrish ``<h3>`` prefix on the HTML body.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from engrish.constants import FORM_NAMES


class DuplicateEntryError(AssertionError):
    """Raised when the same ``@`` headword or ``(variant, target)`` is emitted twice."""


def _render_html_body(entry: dict[str, Any]) -> str:
    """Render a per-entry HTML body from a wikidict render-JSON entry.

    Structure (mirrors ``WORD_TPL_DICTFILE``):
    - Optional definitions grouped by POS (``<p><b>{pos}</b></p><ol>...</ol>``).
    - Optional etymology paragraphs (``<p>{etym}</p>...<br/>``).

    Sub-definitions nested via list-style-type switch (alpha → roman).
    """
    parts: list[str] = []

    definitions = entry.get("definitions") or {}
    for pos, defs in definitions.items():
        parts.append(f"<p><b>{html.escape(pos)}</b></p><ol>")
        for d in defs:
            if isinstance(d, str):
                parts.append(f"<li>{d}</li>")
            elif isinstance(d, (list, tuple)):
                parts.append('<ol style="list-style-type:lower-alpha">')
                for sub in d:
                    if isinstance(sub, str):
                        parts.append(f"<li>{sub}</li>")
                    elif isinstance(sub, (list, tuple)):
                        parts.append('<ol style="list-style-type:lower-roman">')
                        for sub_sub in sub:
                            parts.append(f"<li>{sub_sub}</li>")
                        parts.append("</ol>")
                parts.append("</ol>")
        parts.append("</ol>")

    etymology = entry.get("etymology") or []
    if etymology:
        for etym in etymology:
            if isinstance(etym, str):
                parts.append(f"<p>{etym}</p>")
            elif isinstance(etym, (list, tuple)):
                parts.append("<ol>")
                for sub_etym in etym:
                    parts.append(f"<li>{sub_etym}</li>")
                parts.append("</ol>")
        parts.append("<br/>")

    return "".join(parts)


def _build_reverse_variants(
    json_data: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """Reverse-index ``target_headword → {variant_headwords}``.

    Mirrors ``wikidict.convert.make_variants`` (convert.py:1037-1047): for
    every entry ``W`` with ``W.variants = [T1, T2, ...]``, add ``W`` to
    ``reverse[Tn]``. The result is the canonical ``& variant`` set under
    each ``@ target``.
    """
    reverse: dict[str, set[str]] = defaultdict(set)
    for headword, entry in json_data.items():
        for target in entry.get("variants") or []:
            reverse[target].add(headword)
    return reverse


def _build_broken_variants(
    json_data: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Find variants whose target is absent from ``json_data``.

    Returns ``{missing_target → sorted-list-of-variant-headwords}``. These
    pointers come from upstream Wiktionary citing parent forms that aren't
    extracted into our render JSON (typically: target is in a different
    Wiktionary locale, target has a different normalization than the
    citation, or target is an anchor/fragment reference).

    M13-AC8 (2026-05-09): broken pointers are reported via the
    ``broken_variants.txt`` artifact alongside the ``.df``, not silently
    dropped. Per CLAUDE.md verification §2/§3a, broken variant targets must
    be visible — categorization by failure type belongs to the consumer of
    this artifact (M13-AC5 data verification).
    """
    keys = set(json_data.keys())
    broken: dict[str, set[str]] = defaultdict(set)
    for headword, entry in json_data.items():
        for target in entry.get("variants") or []:
            if target not in keys:
                broken[target].add(headword)
    return {tgt: sorted(broken[tgt]) for tgt in sorted(broken)}


def write_broken_variants_report(
    json_data: dict[str, dict[str, Any]],
    output_path: Path,
) -> int:
    """Emit a TSV-like report of broken variant pointers next to the ``.df``.

    Format: one line per broken target, sorted lexicographically:
    ``<missing_target>\t<variant_1>\t<variant_2>\t...``

    Returns the count of broken targets. Per M13-AC8, this artifact lands at
    ``<output_dir>/broken_variants.txt`` so consumers (sdcv, KOreader, etc.)
    can be audited against it.
    """
    broken = _build_broken_variants(json_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for target, variants in broken.items():
            f.write(target + "\t" + "\t".join(variants) + "\n")
    return len(broken)


def write_df(
    json_data: dict[str, dict[str, Any]],
    output_path: Path,
    locale: str,
) -> tuple[int, int]:
    """Write a per-locale ``.df`` from render-JSON data.

    Returns ``(headword_count, synonym_count)``. The caller is responsible for
    providing already-rendered entries; this function performs the sort and
    the ``.df`` serialization only.
    """
    form_name = FORM_NAMES.get(locale, locale)
    form_name_escaped = html.escape(form_name)
    seen_headwords: set[str] = set()
    seen_synonym_pairs: set[tuple[str, str]] = set()
    headword_count = 0
    synonym_count = 0

    reverse_variants = _build_reverse_variants(json_data)

    def _is_variant_only(entry: dict[str, Any]) -> bool:
        return bool(entry.get("variants")) and not entry.get("definitions")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        # Case-folded sort (StarDict ``g_strcasecmp`` order: case-insensitive
        # primary, byte-wise tiebreak). Required because ``engrish/merge.py``
        # streams k-way by case-fold key for cross-locale case-variant
        # coalescing (D37: Capitalized-form coalescing).
        for headword in sorted(json_data.keys(), key=lambda s: (s.lower(), s)):
            entry = json_data[headword]

            # Skip variant-only entries — they have no body content. The
            # ``& <headword>`` line emitted under their target's ``@`` already
            # encodes the redirect via StarDict's .syn mechanism. Mirrors
            # wikidict.convert.handle_word:239.
            if _is_variant_only(entry):
                continue

            if headword in seen_headwords:
                raise DuplicateEntryError(f"duplicate @ headword: {headword!r}")
            seen_headwords.add(headword)
            headword_count += 1

            f.write(f"@ {headword}\n")

            pronunciations = entry.get("pronunciations") or []
            gender = entry.get("gender") or ""
            if pronunciations or gender:
                pron = pronunciations[0] if pronunciations else ""
                f.write(f":{pron}{gender}\n")

            # Resolve & set: variants pointing to this headword, plus a single
            # level of chained variants (``A → B → C`` becomes ``& A``, ``& B``
            # under ``@ C`` when B has no definitions). Mirrors
            # wikidict.convert.handle_word:266-276.
            variants: set[str] = set(reverse_variants.get(headword, ()))
            for v in list(variants):
                v_entry = json_data.get(v) or {}
                if not v_entry.get("definitions"):
                    variants.update(reverse_variants.get(v, ()))
            variants.discard(headword)

            for variant in sorted(variants, key=lambda s: (len(s), s)):
                pair = (variant, headword)
                if pair in seen_synonym_pairs:
                    raise DuplicateEntryError(
                        f"duplicate & synonym: {variant!r} → {headword!r}"
                    )
                seen_synonym_pairs.add(pair)
                f.write(f"& {variant}\n")
                synonym_count += 1

            body = _render_html_body(entry)
            f.write(f"<html><h3>{form_name_escaped}</h3>{body}</html>\n\n")

    return headword_count, synonym_count


def write_df_from_json(
    json_path: Path,
    output_path: Path,
    locale: str,
) -> tuple[int, int]:
    """Convenience wrapper: ``json.loads`` + ``write_df``."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return write_df(data, output_path, locale)
