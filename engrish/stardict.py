"""StarDict extraction, generation, and post-processing helpers."""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import struct
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Offset-cache (.oft) generation
# ---------------------------------------------------------------------------

_OFT_HEADER = b"StarDict's Cache, Version: 0.2"
_OFT_MAGIC = b"\xc1\xd1\xa4\x51"
_OFT_STRIDE = 32


def generate_oft(source_file: Path, bytes_after_null: int) -> None:
    """Generate a StarDict offset-cache (.oft) file for an .idx or .syn file."""
    data = source_file.read_bytes()
    offsets: list[int] = []
    pos = 0
    count = 0
    while pos < len(data):
        null = data.index(b"\x00", pos)
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
    """Generate .idx.oft and .syn.oft for all StarDict files in folder."""
    for idx_file in folder.glob("*.idx"):
        generate_oft(idx_file, bytes_after_null=8)
    for syn_file in folder.glob("*.syn"):
        generate_oft(syn_file, bytes_after_null=4)


# ---------------------------------------------------------------------------
# .ifo patching and file renaming
# ---------------------------------------------------------------------------


def patch_ifo(folder: Path, dict_name: str, fields: dict[str, str]) -> None:
    """Update or insert key=value fields in the .ifo file inside folder."""
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


def rename_stardict_files(folder: Path, dict_name: str) -> None:
    """Rename dict-data.* files in folder to {dict_name}.*"""
    for f in sorted(folder.glob("dict-data*")):
        if f.is_file():
            new_name = dict_name + f.name[len("dict-data"):]
            f.rename(folder / new_name)


def decompress_dict_dz(folder: Path) -> None:
    """For each .dict.dz in folder, write a decompressed .dict alongside it."""
    for dz_file in folder.glob("*.dict.dz"):
        dict_file = dz_file.with_suffix("")
        if not dict_file.exists():
            with gzip.open(dz_file, "rb") as src, dict_file.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            log.info("Decompressed %s → %s", dz_file.name, dict_file.name)


# ---------------------------------------------------------------------------
# Extraction and conversion
# ---------------------------------------------------------------------------


def extract_stardict_zip(zip_path: Path, dest_folder: Path, dict_name: str) -> None:
    """Extract a StarDict zip into dest_folder, rename files to dict_name, then post-process."""
    if not zip_path.exists():
        log.warning("StarDict zip not found: %s — skipping", zip_path)
        return
    dest_folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            parts = Path(member.filename).parts
            if not parts or parts[-1] == "":
                continue
            if len(parts) >= 2 and parts[-2] == "res":
                rel = Path("res") / parts[-1]
            else:
                rel = Path(parts[-1])
            target = dest_folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    rename_stardict_files(dest_folder, dict_name)
    decompress_dict_dz(dest_folder)
    generate_oft_files(dest_folder)
    log.info("Extracted %s → %s", zip_path.name, dest_folder)


def convert_df_to_stardict(df_src: Path, out_folder: Path, title: str, date: str, dict_name: str) -> None:
    """Convert a .df file to a StarDict dictionary in out_folder, named dict_name."""
    import gc

    from pyglossary.glossary_v2 import ConvertArgs, Glossary

    out_folder.mkdir(parents=True, exist_ok=True)

    original_gc_collect = gc.collect
    gc.collect = lambda *_: None  # type: ignore[assignment]

    try:
        os.environ["NO_SQLITE"] = "1"
        Glossary.init()
        glos = Glossary()
        glos.config = {"auto_sqlite": False, "cleanup": False}

        writer_cls = glos.plugins["Stardict"].writerClass

        def get_bookname(cls) -> str:  # type: ignore[no-untyped-def]
            return title

        writer_cls.getBookname = get_bookname

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
    finally:
        gc.collect = original_gc_collect  # type: ignore[assignment]

    rename_stardict_files(out_folder, dict_name)
    decompress_dict_dz(out_folder)
    generate_oft_files(out_folder)
    log.info("StarDict generated in %s", out_folder)


def ifo_fields(form: str, date: str, dict_name: str) -> dict[str, str]:
    """Return the .ifo fields to set/override for a given form."""
    return {
        "bookname": f"Engrish: {form}",
        "website": "https://kemonine.info",
        "description": "Generated by engrish from Wiktionary data",
        "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
        "lang": f"{form}-en",
    }
