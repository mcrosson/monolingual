"""Streaming reader + header-index builder for ``.df`` files.

Per F3 / F4, the reader produces a ``{headword → DfEntry}`` map with
``(byte_offset, byte_size)`` for each ``@`` entry. Entry bodies are NOT
loaded into memory during indexing; EPUB samplers and similar metadata-only
consumers seek + read exactly one entry at a time.

Per M5-AC5, full index build for a 10k-headword fixture uses peak RSS
well under 50 MB (the in-memory state is just the index dict plus a
few bytes of per-line buffering; bodies never enter memory).

Design notes:
- ``@ headword`` starts a new entry; subsequent ``& variant`` lines are
  synonyms pointing to the same body; entry ends at the next ``@ ...``
  or EOF.
- ``read_entry`` returns raw bytes of an entry (including the ``@`` line,
  any ``&`` variant lines, and the HTML body).
- Synonym resolution is exposed via ``build_synonym_map``; callers that
  need both dict + synonym map call ``build_indices``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple


class DfEntry(NamedTuple):
    """Position of a ``@``-rooted entry within a ``.df`` file."""

    headword: str
    byte_offset: int  # offset of the ``@`` line in the file
    byte_size: int    # total bytes of the entry (through just before the next @ or EOF)


def build_header_index(path: Path) -> dict[str, DfEntry]:
    """Return ``{headword → DfEntry}`` for every ``@`` in the file.

    Entry bodies are NOT read. Duplicate ``@`` headwords in the input raise
    ``ValueError`` — writer-side M5-AC2 should prevent this but the reader
    guards against malformed files.
    """
    index: dict[str, DfEntry] = {}
    current_headword: str | None = None
    current_offset: int = 0

    with path.open("rb") as f:
        byte_pos = 0
        while True:
            line = f.readline()
            if not line:
                if current_headword is not None:
                    index[current_headword] = DfEntry(
                        current_headword, current_offset, byte_pos - current_offset
                    )
                break
            if line.startswith(b"@ "):
                if current_headword is not None:
                    index[current_headword] = DfEntry(
                        current_headword, current_offset, byte_pos - current_offset
                    )
                candidate = line[2:].rstrip(b"\r\n").decode("utf-8")
                if candidate in index:
                    raise ValueError(
                        f"duplicate @ headword in {path}: {candidate!r} at byte {byte_pos}"
                    )
                current_headword = candidate
                current_offset = byte_pos
            byte_pos += len(line)

    return index


def build_synonym_map(path: Path) -> dict[str, str]:
    """Return ``{synonym → target_headword}`` for every ``&`` in the file.

    Multiple synonyms per target are fine; the synonym value is the nearest
    preceding ``@`` headword in file order.
    """
    synonyms: dict[str, str] = {}
    current_headword: str | None = None
    with path.open("rb") as f:
        for line in f:
            if line.startswith(b"@ "):
                current_headword = line[2:].rstrip(b"\r\n").decode("utf-8")
            elif line.startswith(b"& ") and current_headword is not None:
                variant = line[2:].rstrip(b"\r\n").decode("utf-8")
                synonyms[variant] = current_headword
    return synonyms


def build_indices(path: Path) -> tuple[dict[str, DfEntry], dict[str, str]]:
    """One-pass build of both headword index and synonym map.

    More efficient than calling ``build_header_index`` + ``build_synonym_map``
    separately when both are needed.
    """
    index: dict[str, DfEntry] = {}
    synonyms: dict[str, str] = {}
    current_headword: str | None = None
    current_offset = 0

    with path.open("rb") as f:
        byte_pos = 0
        while True:
            line = f.readline()
            if not line:
                if current_headword is not None:
                    index[current_headword] = DfEntry(
                        current_headword, current_offset, byte_pos - current_offset
                    )
                break
            if line.startswith(b"@ "):
                if current_headword is not None:
                    index[current_headword] = DfEntry(
                        current_headword, current_offset, byte_pos - current_offset
                    )
                candidate = line[2:].rstrip(b"\r\n").decode("utf-8")
                if candidate in index:
                    raise ValueError(
                        f"duplicate @ headword in {path}: {candidate!r} at byte {byte_pos}"
                    )
                current_headword = candidate
                current_offset = byte_pos
            elif line.startswith(b"& ") and current_headword is not None:
                variant = line[2:].rstrip(b"\r\n").decode("utf-8")
                synonyms[variant] = current_headword
            byte_pos += len(line)

    return index, synonyms


def read_entry(path: Path, entry: DfEntry) -> bytes:
    """Seek to ``entry.byte_offset`` and read exactly ``entry.byte_size`` bytes."""
    with path.open("rb") as f:
        f.seek(entry.byte_offset)
        return f.read(entry.byte_size)


def iter_entries(path: Path) -> Generator[tuple[str, bytes], None, None]:
    """Stream-iterate ``(headword, entry_bytes)`` pairs.

    Low-memory: reads one entry at a time, never holds more than a single
    entry's bytes. Suitable for M7 EPUB chapter builders that walk the whole
    dictionary.
    """
    current_headword: str | None = None
    with path.open("rb") as f:
        buffer: list[bytes] = []
        while True:
            line = f.readline()
            if not line:
                if current_headword is not None:
                    yield current_headword, b"".join(buffer)
                return
            if line.startswith(b"@ "):
                if current_headword is not None:
                    yield current_headword, b"".join(buffer)
                    buffer = []
                current_headword = line[2:].rstrip(b"\r\n").decode("utf-8")
            buffer.append(line)
