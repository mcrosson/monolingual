"""language-stats: per-language statistics from the EN Wiktionary dump."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import L2_HEADING_PATTERN
from .paths import get_sqlite_path

log = logging.getLogger(__name__)


def load_language_codes(db_path: Path) -> dict[str, str]:
    """Extract language name -> ISO code mapping from Module:languages data."""
    name_to_code: dict[str, str] = {}
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT body FROM pages "
            "WHERE title LIKE 'Module:languages/data%' "
            "OR title LIKE 'Module:etymology languages/data%'"
        )
        for (body,) in cur:
            if body is None:
                continue
            for m in re.finditer(r'm\["([^"]+)"\]\s*=\s*\{\s*"([^"]+)"', body):
                code, name = m.group(1), m.group(2)
                name_to_code[name] = code
    finally:
        con.close()
    return name_to_code


def scan_language_stats(db_path: Path) -> dict[str, dict[str, int]]:
    """Scan all content pages and compute per-language statistics."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    defn_line = re.compile(r"^#+(?![:*])", re.MULTILINE)
    form_of_line = re.compile(r"^#+\s*\{\{[^|}]+ of\|", re.MULTILINE)

    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"entries": 0, "gloss_entries": 0, "gloss_defs": 0, "form_defs": 0, "total_defs": 0}
    )

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM pages WHERE namespace_id = 0")
        total_pages = cur.fetchone()[0]

        cur.execute("SELECT body FROM pages WHERE namespace_id = 0")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Scanning pages", total=total_pages)

            for (body,) in cur:
                progress.advance(task)
                if body is None or "==" not in body:
                    continue

                headings = list(L2_HEADING_PATTERN.finditer(body))
                if not headings:
                    continue

                for i, match in enumerate(headings):
                    lang_name = match.group(1)
                    start = match.end()
                    end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
                    section = body[start:end]

                    total = len(defn_line.findall(section))
                    forms = len(form_of_line.findall(section))
                    glosses = total - forms

                    s = stats[lang_name]
                    s["entries"] += 1
                    s["total_defs"] += total
                    s["form_defs"] += forms
                    s["gloss_defs"] += glosses
                    if glosses > 0:
                        s["gloss_entries"] += 1

            progress.update(
                task,
                completed=total_pages,
                description="[magenta]Scanned pages [green]✓[/green]",
            )
    finally:
        con.close()

    return dict(stats)


def run() -> int:
    """Download EN dump (if needed), parse (if needed), then show per-language statistics."""
    from .pipeline import ensure_wikidict_parsed

    db_path = ensure_wikidict_parsed()
    log.info("Using database: %s", db_path)

    name_to_code = load_language_codes(db_path)
    log.info("Loaded %d language code mappings", len(name_to_code))

    stats = scan_language_stats(db_path)

    sorted_langs = sorted(stats.items(), key=lambda x: x[1]["gloss_defs"], reverse=True)

    hdr = (
        f"{'Language':<35} {'Code':<6} {'Gloss Defs':>12} {'Entries':>10} "
        f"{'Gloss Entries':>14} {'Form Defs':>10} {'Total Defs':>12}"
    )
    print(f"\n{hdr}")
    print("-" * len(hdr))
    for lang_name, s in sorted_langs:
        code = name_to_code.get(lang_name, "?")
        print(
            f"{lang_name:<35} {code:<6} {s['gloss_defs']:>12,} {s['entries']:>10,} "
            f"{s['gloss_entries']:>14,} {s['form_defs']:>10,} {s['total_defs']:>12,}"
        )

    print(f"\nTotal languages: {len(stats):,}")
    total_gloss = sum(s["gloss_defs"] for s in stats.values())
    total_all = sum(s["total_defs"] for s in stats.values())
    print(f"Total gloss definitions: {total_gloss:,}")
    print(f"Total definitions: {total_all:,}")

    return 0
