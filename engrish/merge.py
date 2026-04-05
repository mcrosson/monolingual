"""DictFile (.df) parsing, URL rewriting, and multi-locale merging."""

from __future__ import annotations

import logging
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from .config import FORM_NAMES
from .paths import df_path, stardict_zip_path

log = logging.getLogger(__name__)


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

    def _rewrite_res(self, value: str) -> str:
        rel = value[4:]  # strip leading "res/"
        return f"res/{normalize_res_filename(rel, self._locale)}"

    def _build_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        out = ""
        for name, value in attrs:
            if value is None:
                out += f" {name}"
            else:
                if name in ("src", "href") and value.startswith("res/"):
                    value = self._rewrite_res(value)
                out += f' {name}="{value.replace(chr(34), "&quot;")}"'
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


def merge_dfs(
    locales: list[str], noetym: bool = False
) -> dict[str, tuple[list[str], str]]:
    """Merge .df files from multiple locales, preserving caller's locale order."""
    per_locale: dict[str, dict[str, tuple[list[str], str]]] = {}
    active = list(locales)

    for locale in active:
        path = df_path(locale, noetym=noetym)
        if not path.exists():
            log.warning("Missing .df file: %s — skipping", path)
            continue
        per_locale[locale] = parse_df(path)

    all_words: set[str] = set()
    for entries in per_locale.values():
        all_words.update(entries.keys())

    merged: dict[str, tuple[list[str], str]] = {}
    for word in sorted(all_words):
        present = [(loc, per_locale[loc][word]) for loc in active if word in per_locale.get(loc, {})]
        if len(present) == 1:
            loc, (syns, html) = present[0]
            merged[word] = (syns, prefix_res_urls(html, loc))
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
            merged[word] = (all_syns, combined)

    return merged


def write_merged_df(
    merged: dict[str, tuple[list[str], str]], dest: Path
) -> None:
    """Write a merged DictFile (.df)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for word, (syns, html) in merged.items():
            fh.write(f"@ {word}\n")
            for syn in syns:
                fh.write(f"& {syn}\n")
            fh.write(f"{html}\n\n")
