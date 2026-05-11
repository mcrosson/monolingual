"""StarDict writer — merged ``.df`` → ``.ifo / .idx / .dict`` via PyGlossary.

Replaces the pre-M6 path in (now-deleted) ``engrish.stardict_legacy.convert_df_to_stardict``
without the Round-3 2a deviations (per plan M6 / Δ6):

- **2a #1** drops ``NO_SQLITE=1`` env var + ``auto_sqlite=False`` +
  ``cleanup=False`` PyGlossary config. Default PyGlossary mode 1 (SQLite-backed)
  is used; it picks SQLite mode automatically when memory pressure warrants.
- **2a #2** drops the ``getBookname`` monkey-patch; ``glos.setInfo("bookname", ...)``
  is the documented public API and PyGlossary honors it verbatim.
- **2a #3** keeps the ``.oft`` KOReader offset-cache variant (``generate_oft_files``).
  Documented as an engrish additive; not an upstream PyGlossary feature.
- **2a #4** asserts the final ``.idx`` size is strictly < 2³² − 1 BEFORE
  the StarDict assembly is considered complete. Overflow would produce
  silently-corrupt lookups (StarDict offsets are 32-bit).
- **2a #5** keeps the ``lang=`` ``.ifo`` key (engrish additive; KOReader /
  sdcv ignore unknown keys).

Post-convert housekeeping (file renames, ``.oft`` generation, ``.ifo``
patching, ``.dict.dz`` decompression) was historically in
``engrish.stardict_legacy``; those helpers (``rename_stardict_files``,
``decompress_dict_dz``, ``generate_oft_files``, ``patch_ifo``, ``ifo_fields``,
plus the private ``generate_oft`` worker) were inlined here at M11-AC4
(2026-05-02). They're pure-data transforms with no engrish-gated behavior.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import struct
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IDX_32BIT_CEILING = (1 << 32) - 1  # StarDict idx offsets are 32-bit unsigned.

# Fixed gzip-header mtime for byte-deterministic .dict.dz (M9-AC1 fix).
# pyglossary.os_utils._idzip embeds the .dict file's wall-clock st_mtime in
# the gzip header, which differs between pipeline runs. We re-compress with
# this constant after PyGlossary returns.
_FIXED_DICTZIP_MTIME = 0

# .oft offset-cache constants (KOReader variant — engrish additive 2a #3)
_OFT_HEADER = b"StarDict's Cache, Version: 0.2"
_OFT_MAGIC = b"\xc1\xd1\xa4\x51"
_OFT_STRIDE = 32


# ---------------------------------------------------------------------------
# Post-convert housekeeping (was engrish.stardict_legacy)
# ---------------------------------------------------------------------------


def rename_stardict_files(folder: Path, dict_name: str) -> None:
    """Rename ``dict-data.*`` files in *folder* to ``{dict_name}.*``."""
    for f in sorted(folder.glob("dict-data*")):
        if f.is_file():
            new_name = dict_name + f.name[len("dict-data"):]
            f.rename(folder / new_name)


def decompress_dict_dz(folder: Path) -> None:
    """For each ``.dict.dz`` in *folder*, write a decompressed ``.dict`` alongside it."""
    for dz_file in folder.glob("*.dict.dz"):
        dict_file = dz_file.with_suffix("")
        if not dict_file.exists():
            with gzip.open(dz_file, "rb") as src, dict_file.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            log.info("Decompressed %s → %s", dz_file.name, dict_file.name)


def _generate_oft(source_file: Path, bytes_after_null: int) -> None:
    """Generate a StarDict offset-cache (.oft) for an .idx or .syn file.

    Private — called by ``generate_oft_files``. KOReader-variant format
    (engrish additive 2a #3): magic + stride-32 offset table + sentinel
    (file size).
    """
    data = source_file.read_bytes()
    offsets: list[int] = []
    pos = 0
    count = 0
    while pos < len(data):
        try:
            null = data.index(b"\x00", pos)
        except ValueError:
            log.warning(
                "_generate_oft: truncated or corrupt file %s (no null byte at pos %d) — skipping",
                source_file, pos,
            )
            return
        if count % _OFT_STRIDE == 0:
            offsets.append(pos)
        pos = null + 1 + bytes_after_null
        count += 1

    offsets.append(len(data))  # sentinel: idx file size, required by KOReader

    oft_path = source_file.with_suffix(source_file.suffix + ".oft")
    with oft_path.open("wb") as fh:
        fh.write(_OFT_HEADER)
        fh.write(_OFT_MAGIC)
        fh.write(struct.pack(f"<{len(offsets)}I", *offsets))
    log.info("Generated %s (%d entries, stride %d)", oft_path.name, len(offsets), _OFT_STRIDE)


def generate_oft_files(folder: Path) -> None:
    """Generate ``.idx.oft`` and ``.syn.oft`` for all StarDict files in *folder*."""
    for idx_file in folder.glob("*.idx"):
        _generate_oft(idx_file, bytes_after_null=8)
    for syn_file in folder.glob("*.syn"):
        _generate_oft(syn_file, bytes_after_null=4)


def patch_ifo(folder: Path, dict_name: str, fields: dict[str, str]) -> None:
    """Update or insert ``key=value`` fields in the ``.ifo`` file inside *folder*."""
    ifo_files = list(folder.glob("*.ifo"))
    if not ifo_files:
        log.warning("No .ifo found in %s — skipping patch", folder)
        return
    ifo_path = ifo_files[0]
    lines = ifo_path.read_text(encoding="utf-8").splitlines()
    updated: dict[str, bool] = {k: False for k in fields}
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key and key in fields:
            new_lines.append(f"{key}={fields[key]}")
            updated[key] = True
        else:
            new_lines.append(line)
    for key, was_updated in updated.items():
        if not was_updated:
            new_lines.append(f"{key}={fields[key]}")
    ifo_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def ifo_fields(form: str, date: str, dict_name: str) -> dict[str, str]:
    """Return the ``.ifo`` fields to set/override for a given form (engrish additives)."""
    return {
        "bookname": f"Engrish: {form}",
        "website": "https://kemonine.info",
        "description": "Generated by engrish from Wiktionary data",
        "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        # "-en" denotes the EN Wiktionary source dump, not the Modern English locale
        "lang": f"{form}-en",
    }


# ---------------------------------------------------------------------------
# Determinism: re-write .dict.dz with mtime=0 (M9-AC1 fix → F13)
# ---------------------------------------------------------------------------


def _recompress_dict_dz_deterministic(folder: Path) -> None:
    """Re-write every .dict.dz in *folder* with a fixed gzip mtime (M9-AC1).

    PyGlossary's ``runDictzip`` -> ``_idzip`` embeds the source ``.dict`` file's
    ``st_mtime`` in the gzip header (see ``pyglossary/os_utils.py``). That makes
    two pipeline runs produce byte-different ``.dict.dz`` files even when the
    underlying ``.dict`` payload is identical. We re-compress in place using
    ``idzip.compressor.compress`` with ``mtime=_FIXED_DICTZIP_MTIME``.

    The accompanying ``.dict`` file (written by ``decompress_dict_dz``) is the
    source for the re-compress, so this must run AFTER ``decompress_dict_dz``.
    """
    from idzip import compressor

    for dz_file in folder.glob("*.dict.dz"):
        dict_file = dz_file.with_suffix("")
        if not dict_file.exists():
            log.warning("_recompress_dict_dz_deterministic: %s missing — skipping %s", dict_file, dz_file)
            continue
        tmp_dz = dz_file.with_name(dz_file.name + ".tmp")
        with dict_file.open("rb") as inp_file, tmp_dz.open("wb") as out_file:
            compressor.compress(
                inp_file,
                dict_file.stat().st_size,
                out_file,
                dict_file.name,
                _FIXED_DICTZIP_MTIME,
            )
        tmp_dz.replace(dz_file)


# ---------------------------------------------------------------------------
# Public API: convert_df_to_stardict
# ---------------------------------------------------------------------------


class IdxOverflowError(RuntimeError):
    """Raised when the final ``.idx`` file would exceed StarDict's 32-bit ceiling."""


def convert_df_to_stardict(
    df_src: Path,
    out_folder: Path,
    title: str,
    date: str,
    dict_name: str,
) -> None:
    """Convert a ``.df`` file to a StarDict dictionary in ``out_folder``.

    Produces four canonical StarDict files + the engrish ``.oft`` additives:
    - ``{dict_name}.ifo``
    - ``{dict_name}.idx`` (asserted < 2³² − 1 bytes)
    - ``{dict_name}.dict`` (and ``.dict.dz`` — decompressed alongside)
    - ``{dict_name}.idx.oft`` + ``{dict_name}.syn.oft`` if syn present

    Per M6-AC5, the ``.ifo`` ``wordcount`` / ``synwordcount`` are written by
    PyGlossary from the input ``.df`` — no post-hoc counting needed.
    """
    from pyglossary.glossary_v2 import ConvertArgs, Glossary

    out_folder.mkdir(parents=True, exist_ok=True)

    Glossary.init()
    glos = Glossary()
    glos.setInfo("bookname", title)  # 2a #2 — public API, no monkey-patch
    glos.setInfo("title", title)
    glos.setInfo("description", "Generated by engrish from Wiktionary data")
    glos.setInfo("date", f"{date[:4]}-{date[4:6]}-{date[6:8]}")
    glos.setInfo("website", "https://kemonine.info")

    glos.convert(
        ConvertArgs(
            inputFilename=str(df_src),
            outputFilename=str(out_folder / "dict-data.ifo"),
            writeOptions={"dictzip": True, "sametypesequence": "h"},
        )
    )

    # File renames + .dict.dz decompress + .oft generation.
    rename_stardict_files(out_folder, dict_name)
    decompress_dict_dz(out_folder)
    _recompress_dict_dz_deterministic(out_folder)
    generate_oft_files(out_folder)

    # 2a #4: assert .idx fits in StarDict's 32-bit offset field BEFORE caller
    # proceeds to publish. Overflow is a silent-corrupt failure mode that only
    # surfaces at lookup time in sdcv / KOReader; fail fast at build time.
    idx_path = out_folder / f"{dict_name}.idx"
    if idx_path.exists():
        idx_size = idx_path.stat().st_size
        if idx_size >= _IDX_32BIT_CEILING:
            raise IdxOverflowError(
                f"{idx_path} size {idx_size:,} bytes exceeds StarDict 32-bit "
                f"ceiling {_IDX_32BIT_CEILING:,}; the dictionary would fail at "
                f"lookup time. Shrink the corpus (drop low-value locales / etymology) "
                f"or split into multiple forms."
            )

    # 2a #5: keep lang= ifo key + M6-AC5 wordcount values (PyGlossary writes these).
    # ifo_fields is a pure-data helper; post-patch is additive (bookname, website,
    # description, date, lang).
    patch_ifo(out_folder, dict_name, ifo_fields(dict_name, date, dict_name))

    log.info(
        "StarDict generated in %s (idx=%s bytes)",
        out_folder, idx_path.stat().st_size if idx_path.exists() else "?",
    )
