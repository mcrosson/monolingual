"""Wikidict pipeline wrapper and cache management."""

from __future__ import annotations

import json
import logging
import shutil
import unicodedata
from pathlib import Path

from .paths import output_dir, parse_source_dir, render_source_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalization helpers — each returns resolved headword(s) or None
# ---------------------------------------------------------------------------

def _try_strip_combining(target: str, headwords: set[str]) -> str | None:
    """Try stripping combining characters from target to find a headword match.

    Decomposes to NFD, identifies every distinct combining character present,
    then tries removing progressively larger subsets — single marks first, then
    pairs, triples, etc. — until a candidate lands on a headword.  Trying
    smallest subsets first preserves as much of the original spelling as possible
    (e.g. keeping Greek accents while stripping only vowel-length annotations).
    """
    from itertools import combinations

    nfd = unicodedata.normalize("NFD", target)
    marks = sorted({c for c in nfd if unicodedata.combining(c)})
    if not marks:
        return None

    for r in range(1, len(marks) + 1):
        for subset in combinations(marks, r):
            drop = set(subset)
            candidate = unicodedata.normalize("NFC", "".join(c for c in nfd if c not in drop))
            if candidate != target and candidate in headwords:
                return candidate

    return None


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
) -> str | None:
    """Bidirectional common-base matching — strip both sides, match."""
    nfd = unicodedata.normalize("NFD", target)
    base = unicodedata.normalize(
        "NFC", "".join(c for c in nfd if not unicodedata.combining(c))
    )
    if base in base_map:
        return base_map[base][0]
    nfkc = unicodedata.normalize("NFKC", target)
    nfkc_nfd = unicodedata.normalize("NFD", nfkc)
    nfkc_base = unicodedata.normalize(
        "NFC", "".join(c for c in nfkc_nfd if not unicodedata.combining(c))
    )
    if nfkc_base != base and nfkc_base in base_map:
        return base_map[nfkc_base][0]
    low_base = base.lower()
    if low_base != base and low_base in base_map:
        return base_map[low_base][0]
    return None


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
    _add(_try_strip_combining(target, headwords))
    _add(_try_case_normalize(target, headwords))
    _add(_try_bidi_normalize(target, headwords, base_map))
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
    queue = [target]
    while queue:
        current = queue.pop(0)
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
        if len(seen) > 50:
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
            resolved = _try_all_normalizations(target, headwords, base_map)
            for r in resolved:
                if r not in new_variants:
                    new_variants.append(r)
                    normalized += 1
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
        odir = rdir / "output"
        if odir.exists():
            log.info("Removing %s", odir)
            shutil.rmtree(odir)


def run_wikidict(locales: list[str]) -> None:
    """Run the full wikidict pipeline for all locales.

    Groups locales by source dump so download+parse happens once per dump
    (parse.main deletes the XML after parsing, so all locales sharing a
    dump must be parsed before the XML is removed).  Then render+convert
    each locale individually.
    """
    from wikidict import convert as wikidict_convert
    from wikidict import download, parse, render, utils

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for loc in locales:
        if loc not in seen:
            seen.add(loc)
            unique.append(loc)

    # Group by source dump
    by_source: dict[str, list[str]] = {}
    for locale in unique:
        src, _ = utils.guess_locales(locale, use_log=False)
        by_source.setdefault(src, []).append(locale)

    # Phase 1: download + parse (grouped by source dump)
    # parse.main deletes the XML after parsing, so skip download entirely
    # if the .sqlite already exists — otherwise we waste ~3 min decompressing
    # a 12GB XML that won't be used.
    for src, group in by_source.items():
        src_dir = parse_source_dir()
        has_sqlite = bool(list(src_dir.glob("pages-*.sqlite")))

        if not has_sqlite:
            log.info("=== [%s] download ===", group[0])
            download.main(group[0])

            for locale in group:
                log.info("=== [%s] parse ===", locale)
                parse.main(locale)
        else:
            log.info("[%s] Already parsed — skipping download+parse", src)

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
