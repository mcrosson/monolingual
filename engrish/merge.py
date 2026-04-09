"""DictFile (.df) parsing, URL rewriting, and multi-locale merging."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path

from .config import FORM_NAMES
from .paths import df_path, render_source_dir, stardict_zip_path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .df file cache — avoid re-parsing the same file multiple times
# ---------------------------------------------------------------------------

_loaded_df_cache: dict[tuple[str, bool], dict[str, tuple[list[str], str]]] = {}


def _get_cached_df(locale: str, noetym: bool) -> dict[str, tuple[list[str], str]]:
    """Return cached .df data for a locale, parsing if not already cached."""
    key = (locale, noetym)
    if key not in _loaded_df_cache:
        path = df_path(locale, noetym=noetym)
        _loaded_df_cache[key] = parse_df(path) if path.exists() else {}
    return _loaded_df_cache[key]


def clear_df_cache() -> None:
    """Clear the .df cache to free memory after processing."""
    _loaded_df_cache.clear()


# ---------------------------------------------------------------------------
# .df file parser
# ---------------------------------------------------------------------------


def parse_df(path: Path) -> dict[str, tuple[list[str], str]]:
    """Parse a DictFile (.df) into {word: (synonyms, html)}.

    .df format:
        @ headword
        :pronunciation  (optional, ignored)
        & synonym       (0 or more)
        <html>...</html>
        (blank line)
    """
    entries: dict[str, tuple[list[str], str]] = {}
    current_word: str | None = None
    current_syns: list[str] = []
    current_html_lines: list[str] = []

    def flush() -> None:
        if current_word is not None:
            entries[current_word] = (current_syns, "".join(current_html_lines).strip())

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("@ "):
                flush()
                current_word = line[2:].rstrip()
                current_syns = []
                current_html_lines = []
            elif line.startswith("& ") and current_word is not None:
                current_syns.append(line[2:].rstrip())
            elif line.startswith(":") and current_word is not None:
                pass  # pronunciation line — skip
            elif current_word is not None:
                current_html_lines.append(line)

    flush()
    return entries


def stream_df(path: Path) -> Iterator[tuple[str, list[str], str]]:
    """Stream a .df file, yielding (word, synonyms, html) tuples one at a time.

    Memory-efficient alternative to parse_df - O(1) memory per entry vs O(n).
    Requires the .df file to be sorted alphabetically for merge operations.
    """
    current_word: str | None = None
    current_syns: list[str] = []
    current_html_lines: list[str] = []

    def flush() -> tuple[str, list[str], str] | None:
        if current_word is not None:
            return (current_word, current_syns, "".join(current_html_lines).strip())
        return None

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("@ "):
                result = flush()
                if result:
                    yield result
                current_word = line[2:].rstrip()
                current_syns = []
                current_html_lines = []
            elif line.startswith("& ") and current_word is not None:
                current_syns.append(line[2:].rstrip())
            elif line.startswith(":") and current_word is not None:
                pass  # pronunciation line — skip
            elif current_word is not None:
                current_html_lines.append(line)

    result = flush()
    if result:
        yield result


# ---------------------------------------------------------------------------
# res/ URL rewriting
# ---------------------------------------------------------------------------


def normalize_res_filename(rel_path: str, locale: str) -> str:
    """Canonical res/ filename: flatten subdirs to underscores, prefix with locale."""
    return f"{locale}_{rel_path.replace('/', '_')}"


class _ResUrlRewriter(HTMLParser):
    """Rebuild HTML, rewriting src/href attributes that start with 'res/' to
    include a locale prefix (e.g. res/foo.jpg -> res/en_foo.jpg).

    Subdirectory separators within the res path are flattened to underscores
    so the merged res/ folder stays flat and filenames remain unique across locales.
    """

    def __init__(self, locale: str) -> None:
        super().__init__(convert_charrefs=False)
        self._locale = locale
        self._parts: list[str] = []

    def _rewrite_rel(self, rel: str) -> str:
        return f"res/{normalize_res_filename(rel, self._locale)}"

    def _build_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        out = ""
        for name, value in attrs:
            if value is None:
                out += f" {name}"
            else:
                if name in ("src", "href"):
                    if value.startswith("res/"):
                        value = self._rewrite_rel(value[4:])  # strip "res/"
                    elif value.startswith("./"):
                        value = self._rewrite_rel(value[2:])  # strip "./"
                # Properly escape HTML attribute values
                out += f' {name}="{html_escape(value, quote=True)}"'
        return out

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._parts.append(f"<{tag}{self._build_attrs(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._parts.append(f"<{tag}{self._build_attrs(attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._parts.append(f"<!--{data}-->")

    def result(self) -> str:
        return "".join(self._parts)


def prefix_res_urls(html: str, locale: str) -> str:
    """Return html with all res/ paths prefixed with the locale code."""
    rewriter = _ResUrlRewriter(locale)
    rewriter.feed(html)
    rewriter.close()
    return rewriter.result()


def collect_locale_res(locale: str, noetym: bool) -> dict[str, bytes]:
    """Return {prefixed_filename: bytes} for every file inside res/ in the locale's StarDict ZIP."""
    zip_path = stardict_zip_path(locale, noetym=noetym)
    result: dict[str, bytes] = {}
    if not zip_path.exists():
        return result
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename
            idx = name.find("res/")
            if idx < 0 or name.endswith("/"):
                continue
            rel = name[idx + 4:]
            if not rel:
                continue
            prefixed = normalize_res_filename(rel, locale)
            with zf.open(member) as fh:
                result[prefixed] = fh.read()
    return result


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _load_per_locale(
    locales: list[str], noetym: bool
) -> tuple[dict[str, dict[str, tuple[list[str], str]]], list[str]]:
    """Load per-locale .df data and return (per_locale, active_locales).

    Uses the module-level cache to avoid re-parsing the same .df files.
    """
    per_locale: dict[str, dict[str, tuple[list[str], str]]] = {}
    for locale in locales:
        path = df_path(locale, noetym=noetym)
        if not path.exists():
            log.warning("Missing .df file: %s — skipping", path)
            continue
        per_locale[locale] = _get_cached_df(locale, noetym)
    active = [loc for loc in locales if loc in per_locale]
    return per_locale, active


def iter_merged_dfs(
    locales: list[str], noetym: bool = False
) -> Iterator[tuple[str, list[str], str]]:
    """Yield (word, syns, html) for every merged entry in sorted order.

    Loads all locale data into per_locale (one copy in RAM), then yields
    entries one at a time without building a full merged dict. This eliminates
    the second full copy of all locale data that the old merge_dfs approach
    required when combined with write_merged_df.
    """
    per_locale, active = _load_per_locale(locales, noetym)

    all_words: set[str] = set()
    for entries in per_locale.values():
        all_words.update(entries.keys())

    for word in sorted(all_words):
        present = [(loc, per_locale[loc][word]) for loc in active if word in per_locale.get(loc, {})]
        if len(present) == 1:
            loc, (syns, html) = present[0]
            yield word, syns, prefix_res_urls(html, loc)
        else:
            seen_syns: set[str] = set()
            all_syns: list[str] = []
            combined = ""
            for loc, (syns, html) in present:
                for s in syns:
                    if s not in seen_syns:
                        seen_syns.add(s)
                        all_syns.append(s)
                combined += f"<h3>{FORM_NAMES[loc]}</h3>{prefix_res_urls(html, loc)}"
            yield word, all_syns, combined


def iter_merged_dfs_streaming(
    locales: list[str], noetym: bool = False
) -> Iterator[tuple[str, list[str], str]]:
    """Yield (word, syns, html) using streaming merge-sort for bounded memory.

    Uses a classic k-way merge-sort directly on sorted .df files:
    - Opens streaming iterators for each locale's .df file
    - At each step, yields the smallest headword across all iterators
    - Entries for the same word across locales are combined

    Memory: O(num_locales) instead of O(total_entries).
    Requires .df files to be sorted alphabetically (wikidict guarantees this).
    """
    active_locales: list[str] = []
    df_iters: dict[str, Iterator[tuple[str, list[str], str]]] = {}
    df_current: dict[str, tuple[str, list[str], str] | None] = {}

    for locale in locales:
        df = df_path(locale, noetym=noetym)
        if not df.exists():
            log.warning("Missing .df file: %s — skipping", df)
            continue
        active_locales.append(locale)
        df_iters[locale] = stream_df(df)
        df_current[locale] = next(df_iters[locale], None)

    if not active_locales:
        return

    # K-way merge: repeatedly yield the smallest headword
    while True:
        # Find the smallest current headword across all locales
        min_word: str | None = None
        for locale in active_locales:
            entry = df_current[locale]
            if entry is not None:
                word = entry[0]
                if min_word is None or word < min_word:
                    min_word = word

        if min_word is None:
            # All iterators exhausted
            break

        # Collect all locales that have this headword
        present: list[tuple[str, list[str], str]] = []
        for locale in active_locales:
            entry = df_current[locale]
            if entry is not None and entry[0] == min_word:
                _, syns, html = entry
                present.append((locale, syns, prefix_res_urls(html, locale)))
                df_current[locale] = next(df_iters[locale], None)

        if len(present) == 1:
            locale, syns, html = present[0]
            yield min_word, syns, html
        else:
            # Multi-locale merge
            seen_syns: set[str] = set()
            all_syns: list[str] = []
            combined = ""
            for locale, syns, html in present:
                for s in syns:
                    if s not in seen_syns:
                        seen_syns.add(s)
                        all_syns.append(s)
                combined += f"<h3>{FORM_NAMES[locale]}</h3>{html}"
            yield min_word, all_syns, combined


def merge_dfs(
    locales: list[str], noetym: bool = False
) -> dict[str, tuple[list[str], str]]:
    """Merge .df files from multiple locales, preserving caller's locale order.

    Returns a full dict — used by tests that need random access.
    Production code should use iter_merged_dfs + write_merged_df_streaming
    to avoid building this second copy.
    """
    return {word: (syns, html) for word, syns, html in iter_merged_dfs(locales, noetym)}


def write_merged_df(
    dest: Path,
    entries: Iterator[tuple[str, list[str], str]] | dict[str, tuple[list[str], str]],
) -> None:
    """Write a merged DictFile (.df) from an iterator or dict of entries."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(entries, dict):
        items = ((w, s, h) for w, (s, h) in entries.items())
    else:
        items = entries
    with dest.open("w", encoding="utf-8") as fh:
        for word, syns, html in items:
            fh.write(f"@ {word}\n")
            for syn in syns:
                fh.write(f"& {syn}\n")
            fh.write(f"{html}\n\n")
