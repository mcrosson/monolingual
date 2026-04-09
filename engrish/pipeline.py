"""Wikidict pipeline wrapper and cache management."""

from __future__ import annotations

import gc
import json
import logging
import re
import shutil
import unicodedata
from collections import deque
from pathlib import Path

from .paths import output_dir, parse_source_dir, render_source_dir

log = logging.getLogger(__name__)

# Maximum depth for variant chain resolution (prevents infinite loops)
_MAX_CHAIN_DEPTH = 50


# ---------------------------------------------------------------------------
# Normalization helpers — each returns resolved headword(s) or None
# ---------------------------------------------------------------------------

def _try_strip_combining(
    target: str, headwords: set[str], base_map: dict[str, list[str]]
) -> list[str]:
    """Find headwords that match the target with a subset of its combining marks removed.

    Instead of enumerating 2^N subsets of marks to strip from the target (exponential),
    this inverts the search: use base_map to find headwords that share the target's base
    form and have a proper subset of the target's marks.  O(M) where M is the number of
    headwords that share the same base form — typically very small (1–5).

    Returns all matching headwords (may be more than one if several headwords differ only
    in which subset of marks they preserve).
    """
    nfd = unicodedata.normalize("NFD", target)
    target_marks = frozenset(c for c in nfd if unicodedata.combining(c))
    if not target_marks:
        return []
    base = unicodedata.normalize("NFC", "".join(c for c in nfd if not unicodedata.combining(c)))
    results: list[str] = []
    for hw in base_map.get(base, []):
        if hw == target or hw not in headwords:
            continue
        hw_nfd = unicodedata.normalize("NFD", hw)
        hw_marks = frozenset(c for c in hw_nfd if unicodedata.combining(c))
        if hw_marks < target_marks:  # strict subset — hw has fewer distinct marks
            results.append(hw)
    return results


def _try_strip_anchor(target: str, headwords: set[str]) -> str | None:
    """Strip Wiktionary anchor/fragment references (``#section``, ``//alternate``)."""
    for sep in ("#", "//"):
        if sep in target:
            stripped = target.split(sep)[0]
            if stripped and stripped in headwords:
                return stripped
    return None


def _try_normalize_zwnj(target: str, headwords: set[str]) -> str | None:
    """Normalize zero-width non-joiner (U+200C) — remove or replace with space."""
    if "\u200c" not in target:
        return None
    for candidate in (
        target.replace("\u200c", ""),
        target.replace("\u200c", " "),
        target.rstrip("\u200c"),
    ):
        if candidate != target and candidate in headwords:
            return candidate
    return None


def _try_nfkc(target: str, headwords: set[str]) -> str | None:
    """NFKC normalization — fullwidth, presentation forms, CJK radicals, long-s."""
    nfkc = unicodedata.normalize("NFKC", target)
    if nfkc != target and nfkc in headwords:
        return nfkc
    return None


def _try_case_normalize(target: str, headwords: set[str]) -> str | None:
    """Case normalization — lower, capitalize, decapitalize first char."""
    for candidate in (target.lower(), target.capitalize()):
        if candidate != target and candidate in headwords:
            return candidate
    if len(target) > 1:
        decap = target[0].lower() + target[1:]
        if decap != target and decap in headwords:
            return decap
    return None


def _build_base_form_map(headwords: set[str]) -> dict[str, list[str]]:
    """Map stripped/normalized base forms back to original headwords.

    Used for bidirectional matching: when the target is the *simpler* form
    and the headword is the more decorated one (e.g. Greek grave vs acute
    accent, Russian ё vs е).
    """
    m: dict[str, list[str]] = {}
    for hw in headwords:
        nfd = unicodedata.normalize("NFD", hw)
        base = unicodedata.normalize(
            "NFC", "".join(c for c in nfd if not unicodedata.combining(c))
        )
        if base != hw:
            m.setdefault(base, []).append(hw)
        nfkc = unicodedata.normalize("NFKC", hw)
        if nfkc != hw:
            m.setdefault(nfkc, []).append(hw)
            nfkc_nfd = unicodedata.normalize("NFD", nfkc)
            nfkc_base = unicodedata.normalize(
                "NFC", "".join(c for c in nfkc_nfd if not unicodedata.combining(c))
            )
            if nfkc_base not in (hw, nfkc, base):
                m.setdefault(nfkc_base, []).append(hw)
        low = base.lower()
        if low != base:
            m.setdefault(low, []).append(hw)
    return m


def _try_bidi_normalize(
    target: str, headwords: set[str], base_map: dict[str, list[str]]
) -> list[str]:
    """Bidirectional common-base matching — strip both sides, return ALL matching headwords.

    Returns every headword that shares the same stripped base form as the target.
    Returning all candidates avoids the arbitrary [0] choice when multiple headwords
    decompose to the same base (e.g. Greek ψιλοί vs ψιλοὶ both stripping to ψιλοι).
    """
    results: list[str] = []

    def _add_all(candidates: list[str]) -> None:
        for hw in candidates:
            if hw != target and hw in headwords and hw not in results:
                results.append(hw)

    nfd = unicodedata.normalize("NFD", target)
    base = unicodedata.normalize(
        "NFC", "".join(c for c in nfd if not unicodedata.combining(c))
    )
    if base in base_map:
        _add_all(base_map[base])
    nfkc = unicodedata.normalize("NFKC", target)
    nfkc_nfd = unicodedata.normalize("NFD", nfkc)
    nfkc_base = unicodedata.normalize(
        "NFC", "".join(c for c in nfkc_nfd if not unicodedata.combining(c))
    )
    if nfkc_base != base and nfkc_base in base_map:
        _add_all(base_map[nfkc_base])
    low_base = base.lower()
    if low_base != base and low_base in base_map:
        _add_all(base_map[low_base])
    return results


def _try_punct_normalize(target: str, headwords: set[str]) -> list[str]:
    """Punctuation normalization — returns ALL matching forms."""
    results: list[str] = []
    # Smart/curly quotes → straight
    t = target.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    if t != target and t in headwords:
        results.append(t)
    # Dot stripping (E.U. → EU)
    t = target.replace(".", "")
    if t != target and t in headwords:
        results.append(t)
    t = target.replace(". ", "").rstrip(".")
    if t != target and t not in results and t in headwords:
        results.append(t)
    # Interpunct / middle dot normalization
    for mdot in ("·", "\u2E31", "\u2027", "\u2022"):
        for repl in ("", "-", " "):
            t = target.replace(mdot, repl)
            if t != target and t not in results and t in headwords:
                results.append(t)
    # Dash normalization (en-dash, em-dash, etc. → hyphen)
    for dash in ("\u2013", "\u2014", "\u2010", "\u2011", "\u2212"):
        t = target.replace(dash, "-")
        if t != target and t not in results and t in headwords:
            results.append(t)
    # Apostrophe variants → ASCII apostrophe
    for apo in ("\u02BC", "\u02BB", "\u02BE", "\u02BF", "\u2018", "\u2019"):
        t = target.replace(apo, "'")
        if t != target and t not in results and t in headwords:
            results.append(t)
    # Tatweel stripping
    t = target.replace("\u0640", "")
    if t != target and t not in results and t in headwords:
        results.append(t)
    return results


def _try_spacing_normalize(target: str, headwords: set[str]) -> list[str]:
    """Spacing normalization — hyphen↔space, join/split. Returns ALL matches."""
    results: list[str] = []
    for t in (
        target.replace("-", " "),
        target.replace(" ", "-"),
        target.replace("-", ""),
        target.replace(" ", ""),
    ):
        if t != target and t not in results and t in headwords:
            results.append(t)
    return results


def _try_article_normalize(target: str, headwords: set[str]) -> str | None:
    """Strip or add leading articles (the/a/an)."""
    for article in ("the ", "The ", "a ", "A ", "an ", "An "):
        if target.startswith(article):
            stripped = target[len(article):]
            if stripped in headwords:
                return stripped
        else:
            prefixed = article + target
            if prefixed in headwords:
                return prefixed
    return None


def _try_reflexive_normalize(target: str, headwords: set[str]) -> str | None:
    """Strip reflexive prefixes (French se/s', German sich, etc.)."""
    for prefix in ("s\u2019", "s\u2018", "s'", "se ", "si ", "sich "):
        if target.startswith(prefix):
            rest = target[len(prefix):]
            if rest in headwords:
                return rest
    return None


def _try_suru_normalize(target: str, headwords: set[str]) -> str | None:
    """Strip Japanese する suffix from verb forms."""
    if target.endswith("\u3059\u308B") and len(target) > 2:
        stem = target[:-2]
        if stem in headwords:
            return stem
    return None


def _try_comma_split(target: str, headwords: set[str]) -> list[str]:
    """Split comma-separated targets into individual headwords."""
    if "," not in target:
        return []
    parts = [p.strip() for p in target.split(",")]
    return [p for p in parts if p and p in headwords]


def _try_all_normalizations(
    target: str,
    headwords: set[str],
    base_map: dict[str, list[str]],
) -> list[str]:
    """Run all normalization strategies on a target.

    Returns a list of ALL resolved headwords (may be empty).
    Does NOT include the original target — caller decides whether to keep it.
    """
    results: list[str] = []

    def _add(resolved: str | None) -> None:
        if resolved and resolved not in results:
            results.append(resolved)

    def _add_many(resolved: list[str]) -> None:
        for r in resolved:
            if r not in results:
                results.append(r)

    _add(_try_strip_anchor(target, headwords))
    _add(_try_normalize_zwnj(target, headwords))
    _add(_try_nfkc(target, headwords))
    _add_many(_try_strip_combining(target, headwords, base_map))
    _add(_try_case_normalize(target, headwords))
    _add_many(_try_bidi_normalize(target, headwords, base_map))
    _add_many(_try_punct_normalize(target, headwords))
    _add_many(_try_spacing_normalize(target, headwords))
    _add(_try_article_normalize(target, headwords))
    _add(_try_reflexive_normalize(target, headwords))
    _add(_try_suru_normalize(target, headwords))
    _add_many(_try_comma_split(target, headwords))

    return results


def _resolve_variant_chain(
    target: str,
    data: dict,
    headwords_with_defs: set[str],
    headwords: set[str],
    base_map: dict[str, list[str]],
) -> str | None:
    """Follow variant chains via BFS, applying normalization at each step.

    Tries all variant targets at each node (not just the first).
    Applies normalization to resolve intermediate nodes that don't
    directly match a headword.
    """
    seen: set[str] = set()
    queue: deque[str] = deque([target])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        # Try to normalize current to a headword
        if current in data:
            actual = current
        else:
            resolved = _try_all_normalizations(current, headwords, base_map)
            actual = next((r for r in resolved if r in data), None)
            if actual is None:
                continue
        entry = data[actual]
        if entry.get("definitions"):
            return actual
        for t in entry.get("variants", []):
            if t not in seen:
                queue.append(t)
        if len(seen) > _MAX_CHAIN_DEPTH:
            break
    return None


def normalize_variant_targets(locale: str) -> None:
    """Normalize variant targets in a locale's data-*.json.

    For each broken variant target (one that doesn't match a headword),
    applies normalization to find matching headwords.  The original target
    is always kept; resolved normalized forms are added alongside it.
    Only exact-duplicate strings are collapsed.

    Passes (in order):
     1. Anchor/fragment stripping (#, //)
     2. ZWNJ normalization (remove / replace with space)
     3. NFKC + combining mark stripping
     4. Case normalization (lower, capitalize, decap)
     5. Bidirectional common-base matching (strip both sides)
     6. Punctuation normalization (interpunct, dots, dashes, quotes, tatweel)
     7. Spacing normalization (hyphen↔space, join/split)
     8. Article normalization (strip/add the/a/an)
     9. Reflexive normalization (strip se/s'/sich)
    10. Japanese する normalization
    11. Comma splitting (split to multiple variants)
    12. Chain resolution (multi-target BFS with normalization at each step)
    13. Cleanup: drop unresolvable, dedup exact-only, remove empty variant keys
    """
    render_dir = render_source_dir(locale)
    jsons = sorted(render_dir.glob("data-*.json"))
    if not jsons:
        return

    data_file = jsons[-1]
    data: dict = json.loads(data_file.read_text("utf-8"))
    headwords = set(data.keys())
    base_map = _build_base_form_map(headwords)
    headwords_with_defs = {w for w, e in data.items() if e.get("definitions")}

    normalized = 0
    chains_resolved = 0
    dangling_dropped = 0
    cleanup_modified = 0

    # Stats collection for epub cleanup chapter
    _MAX_STATS_EXAMPLES = 3
    _NORMALIZER_NAMES = [
        ("anchor", _try_strip_anchor),
        ("zwnj", _try_normalize_zwnj),
        ("nfkc", _try_nfkc),
        ("combining", _try_strip_combining),
        ("case", _try_case_normalize),
        ("punct", _try_punct_normalize),
        ("spacing", _try_spacing_normalize),
        ("article", _try_article_normalize),
        ("reflexive", _try_reflexive_normalize),
        ("suru", _try_suru_normalize),
        ("comma", _try_comma_split),
    ]
    stats_counts: dict[str, int] = {name: 0 for name, _ in _NORMALIZER_NAMES}
    stats_counts["bidi"] = 0
    stats_counts["dangling"] = 0
    stats_examples: dict[str, list[dict]] = {k: [] for k in stats_counts}

    def _record_stat(cat: str, headword: str, original: str, resolved: str) -> None:
        stats_counts[cat] += 1
        exs = stats_examples[cat]
        if len(exs) < _MAX_STATS_EXAMPLES:
            exs.append({"headword": headword, "original": original, "normalized": resolved})

    # Passes 1–11: direct normalization — keep originals, add resolved forms
    for word, entry in data.items():
        variants = entry.get("variants")
        if not variants:
            continue
        new_variants: list[str] = []
        for target in variants:
            new_variants.append(target)
            if target in headwords:
                continue
            # Run all normalizations (for the variant list)
            resolved = _try_all_normalizations(target, headwords, base_map)
            for r in resolved:
                if r not in new_variants:
                    new_variants.append(r)
                    normalized += 1
            # Categorize for stats: run individually, first match wins
            if resolved:
                categorized = False
                for cat, normalizer in _NORMALIZER_NAMES:
                    if cat in ("punct", "spacing", "comma"):
                        result = normalizer(target, headwords)
                        if result:
                            _record_stat(cat, word, target, result[0])
                            categorized = True
                            break
                    elif cat == "combining":
                        result = _try_strip_combining(target, headwords, base_map)
                        if result:
                            _record_stat(cat, word, target, result[0])
                            categorized = True
                            break
                    else:
                        result = normalizer(target, headwords)
                        if result:
                            _record_stat(cat, word, target, result)
                            categorized = True
                            break
                if not categorized:
                    result = _try_bidi_normalize(target, headwords, base_map)
                    if result:
                        _record_stat("bidi", word, target, result[0])
            else:
                _record_stat("dangling", word, target, "\u2014")
        entry["variants"] = new_variants

    # Pass 12: chain resolution — for targets not pointing to a defined entry,
    # follow chains (with normalization at each step) to find one.
    for word, entry in data.items():
        variants = entry.get("variants")
        if not variants:
            continue
        additions: list[str] = []
        for target in variants:
            if target in headwords_with_defs:
                continue
            resolved = _resolve_variant_chain(
                target, data, headwords_with_defs, headwords, base_map
            )
            if resolved and resolved != target and resolved not in variants and resolved not in additions:
                additions.append(resolved)
                chains_resolved += 1
        if additions:
            entry["variants"] = variants + additions

    # Pass 13: cleanup — drop unresolvable, dedup exact-only
    dead_entries: list[str] = []
    for word, entry in data.items():
        variants = entry.get("variants")
        if not variants:
            continue
        clean: list[str] = []
        seen_exact: set[str] = set()
        for target in variants:
            # Exact dedup
            if target in seen_exact:
                continue
            seen_exact.add(target)
            # Keep if target is a headword (it can be looked up)
            if target in headwords:
                clean.append(target)
                continue
            # Drop: target doesn't exist as a headword
            dangling_dropped += 1
        # Self-reference removal
        clean = [t for t in clean if t != word]
        if clean != variants:
            cleanup_modified += 1
        if clean:
            entry["variants"] = clean
        else:
            entry.pop("variants", None)
            # Entry with no definitions and no variants is dead — remove it
            if not entry.get("definitions"):
                dead_entries.append(word)
    for word in dead_entries:
        del data[word]
    if dead_entries:
        log.info("[%s] Removed %s dead entries (no definitions, no variants)", locale, f"{len(dead_entries):,}")

    total = normalized + chains_resolved
    if total or dangling_dropped or cleanup_modified:
        if normalized:
            log.info("[%s] Added %s normalized variant forms", locale, f"{normalized:,}")
        if chains_resolved:
            log.info("[%s] Resolved %s variant chains", locale, f"{chains_resolved:,}")
        if dangling_dropped:
            log.info("[%s] Dropped %s dangling variant targets", locale, f"{dangling_dropped:,}")
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        # Invalidate convert output so it re-runs with the updated data
        out = output_dir(locale)
        if out.exists():
            shutil.rmtree(out)
            log.info("[%s] Cleared stale convert output", locale)

    # Write normalization stats sidecar for epub cleanup chapter.
    # Always written (even when empty) so stale files from prior runs are overwritten.
    stats_data = {}
    for cat in stats_counts:
        if stats_counts[cat] > 0:
            stats_data[cat] = {
                "count": stats_counts[cat],
                "examples": stats_examples[cat],
            }
    stats_path = render_dir / "normalize-stats.json"
    stats_path.write_text(json.dumps(stats_data, ensure_ascii=False, indent=2), "utf-8")
    if stats_data:
        log.info("[%s] Wrote normalization stats: %s", locale, stats_path)

    # Collect locale metadata for streaming merge/epub
    # IPA pattern for pronunciation detection in raw JSON definitions
    _ipa_pattern = re.compile(r"[/\\]([^/\\<>]{1,50})[/\\]")
    _ipa_chars = re.compile(r"[\u0250-\u02FF\u0300-\u036F\u00C0-\u024F]")

    entry_sizes: list[tuple[str, int]] = []
    words_with_alternates: list[str] = []
    words_with_pronunciation: list[str] = []
    total_synonyms = 0

    for word, entry in data.items():
        # Estimate size from JSON-serialized definitions (rough approximation of HTML size)
        defs = entry.get("definitions", [])
        size_estimate = len(json.dumps(defs, ensure_ascii=False)) if defs else 0
        entry_sizes.append((word, size_estimate))

        # Track words with alternates (variants)
        variants = entry.get("variants", [])
        total_synonyms += len(variants)
        if variants:
            words_with_alternates.append(word)

        # Detect pronunciation in definitions (search for IPA patterns)
        defs_text = json.dumps(defs, ensure_ascii=False) if defs else ""
        pron_matches = _ipa_pattern.findall(defs_text)
        if any(_ipa_chars.search(p) for p in pron_matches):
            words_with_pronunciation.append(word)

    # Sort and prepare metadata
    sorted_headwords = sorted(data.keys())
    entry_sizes.sort(key=lambda x: x[1], reverse=True)

    locale_meta = {
        "entry_count": len(data),
        "total_synonyms": total_synonyms,
        "headwords": sorted_headwords,
        "largest_entries": [
            {"word": w, "size_bytes": s} for w, s in entry_sizes[:20]
        ],
        "words_with_alternates": sorted(words_with_alternates),
        "words_with_pronunciation": sorted(words_with_pronunciation),
    }

    meta_path = render_dir / "locale-meta.json"
    meta_path.write_text(json.dumps(locale_meta, ensure_ascii=False), "utf-8")
    log.info("[%s] Wrote locale metadata: %s (%d entries)", locale, meta_path, len(data))

    # Explicit cleanup to free memory after processing large locale data
    del data, headwords, base_map, headwords_with_defs, locale_meta, sorted_headwords
    gc.collect()


def delete_cache(locales: list[str]) -> None:
    """Delete cached downloads, parse DB, render JSON, and convert output."""
    src = parse_source_dir()

    # Shared: downloads + parse DB
    for pattern in (
        "pages-*.xml.bz2",
        "pages-*.xml",
        "pages-*.sqlite",
        "pages-*.sqlite-shm",
        "pages-*.sqlite-wal",
    ):
        for f in src.glob(pattern):
            log.info("Removing %s", f)
            f.unlink(missing_ok=True)

    # Per-locale: render JSON + output dir
    seen: set[Path] = set()
    for locale in locales:
        rdir = render_source_dir(locale)
        if rdir in seen:
            continue
        seen.add(rdir)
        for f in rdir.glob("data-*.json"):
            log.info("Removing %s", f)
            f.unlink(missing_ok=True)
        odir = output_dir(locale)
        if odir.exists():
            log.info("Removing %s", odir)
            shutil.rmtree(odir)


def ensure_wikidict_parsed() -> Path:
    """Ensure EN Wiktionary dump is downloaded and parsed. Returns DB path.

    Used by stats and add-language commands that need the parsed dump
    but don't need to run render/convert.
    """
    from wikidict import download, parse

    from .paths import get_sqlite_path, parse_source_dir

    src_dir = parse_source_dir()
    has_sqlite = bool(list(src_dir.glob("pages-*.sqlite")))
    if not has_sqlite:
        log.info("Ensuring EN Wiktionary dump is downloaded and parsed...")
        download.main("en")
        parse.main("en")
    return get_sqlite_path()


def run_wikidict(locales: list[str]) -> None:
    """Run the full wikidict pipeline for all locales.

    All engrish locales use the EN Wiktionary as their source dump.
    Download+parse runs once if the SQLite DB is not already present.
    Then render+convert runs per locale individually.
    """
    from wikidict import convert as wikidict_convert
    from wikidict import download, parse, render

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for loc in locales:
        if loc not in seen:
            seen.add(loc)
            unique.append(loc)

    # Phase 1: download + parse (once, shared across all locales)
    # Skip download if the SQLite DB already exists — avoids decompressing
    # the full XML dump unnecessarily.
    src_dir = parse_source_dir()
    has_sqlite = bool(list(src_dir.glob("pages-*.sqlite")))
    if not has_sqlite:
        log.info("=== download ===")
        download.main(unique[0])
        for locale in unique:
            log.info("=== [%s] parse ===", locale)
            parse.main(locale)
    else:
        log.info("Already parsed — skipping download+parse")

    # Phase 2: render + convert (per locale)
    for locale in unique:
        render_dir = render_source_dir(locale)
        if list(render_dir.glob("data-*.json")):
            log.info("[%s] Already rendered — skipping", locale)
        else:
            log.info("=== [%s] render ===", locale)
            if render.main(locale):
                raise RuntimeError(f"Render failed for locale '{locale}'")

        normalize_variant_targets(locale)

        out = output_dir(locale)
        if out.exists() and any(out.glob("dict-*.df")):
            log.info("[%s] Already converted — skipping", locale)
        else:
            log.info("=== [%s] convert ===", locale)
            if wikidict_convert.main(locale):
                raise RuntimeError(f"Convert failed for locale '{locale}'")
