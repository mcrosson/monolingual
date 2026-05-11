"""M9 determinism / robustness fixes.

P1 (`.dict.dz` byte determinism): pyglossary's runDictzip embeds the
source `.dict` file's wall-clock st_mtime in the gzip header. Two pipeline
runs therefore produce byte-different `.dict.dz` files even when the
underlying `.dict` payload is identical. `engrish.stardict_writer.
_recompress_dict_dz_deterministic` re-compresses with mtime=0.

P2 (font merge crashes on MATH/STAT/etc tables): fontTools' default
Merger has no `mergeMap` for several advanced typography tables. Live
M9 audit showed ru (NotoSansMath -> MATH) and grc (NotoSansCypriot +
NotoSansSymbols2 -> assertion on differing schemas) crash mid-merge.
`engrish.font._strip_unmergeable_tables` drops them before merge;
`pairwise_merge` additionally falls back to dropping the failing source
on any residual exception.
"""
from __future__ import annotations

import gzip
import struct
from pathlib import Path

import pytest

from engrish import font as engrish_font
from engrish import stardict_writer


# ---------------------------------------------------------------------------
# P1 — dict.dz determinism
# ---------------------------------------------------------------------------


def _write_idzip(folder: Path, name: str, payload: bytes, mtime: int) -> Path:
    """Write a raw .dict.dz file via idzip with a chosen mtime."""
    from idzip import compressor

    dict_path = folder / name
    dict_path.write_bytes(payload)
    dz_path = folder / (name + ".dz")
    with dict_path.open("rb") as inp, dz_path.open("wb") as out:
        compressor.compress(inp, len(payload), out, name, mtime)
    return dz_path


def _gzip_header_mtime(dz_bytes: bytes) -> int:
    """Extract the 4-byte mtime field from the gzip member header."""
    assert dz_bytes[:2] == b"\x1f\x8b", "not a gzip stream"
    return struct.unpack("<I", dz_bytes[4:8])[0]


def test_recompress_dict_dz_deterministic_pins_mtime_to_zero(tmp_path: Path) -> None:
    """Re-compressing a freshly-built .dict.dz should reset the gzip mtime to 0."""
    payload = b"deterministic payload " * 100
    dict_path = tmp_path / "x.dict"
    dict_path.write_bytes(payload)

    # Simulate PyGlossary producing a .dict.dz with a wall-clock-ish mtime.
    _write_idzip(tmp_path, "x.dict", payload, mtime=1700000000)
    pre_mtime = _gzip_header_mtime((tmp_path / "x.dict.dz").read_bytes())
    assert pre_mtime == 1700000000

    stardict_writer._recompress_dict_dz_deterministic(tmp_path)

    post_mtime = _gzip_header_mtime((tmp_path / "x.dict.dz").read_bytes())
    assert post_mtime == 0


def test_recompress_dict_dz_two_runs_byte_identical(tmp_path: Path) -> None:
    """Re-compressing twice from identical .dict input must produce identical .dz output."""
    payload = b"identical payload " * 200
    dict_path = tmp_path / "y.dict"
    dict_path.write_bytes(payload)

    _write_idzip(tmp_path, "y.dict", payload, mtime=1700000000)
    stardict_writer._recompress_dict_dz_deterministic(tmp_path)
    bytes_a = (tmp_path / "y.dict.dz").read_bytes()

    # Re-create the bare .dict.dz with a different "wall-clock" mtime, then
    # re-pin: should match bytes_a exactly.
    _write_idzip(tmp_path, "y.dict", payload, mtime=1800000000)
    stardict_writer._recompress_dict_dz_deterministic(tmp_path)
    bytes_b = (tmp_path / "y.dict.dz").read_bytes()

    assert bytes_a == bytes_b


def test_recompress_dict_dz_round_trips_payload(tmp_path: Path) -> None:
    """The re-compressed .dict.dz must decompress back to the exact .dict payload."""
    payload = b"\x00\x01\x02 round-trip " * 500
    dict_path = tmp_path / "z.dict"
    dict_path.write_bytes(payload)
    _write_idzip(tmp_path, "z.dict", payload, mtime=1700000000)
    stardict_writer._recompress_dict_dz_deterministic(tmp_path)

    with gzip.open(tmp_path / "z.dict.dz", "rb") as src:
        decoded = src.read()
    assert decoded == payload


# ---------------------------------------------------------------------------
# P2 — font merge robustness
# ---------------------------------------------------------------------------


def test_strip_unmergeable_tables_drops_known_set() -> None:
    """All tables in _UNMERGEABLE_TABLES are removed when present."""
    from fontTools.ttLib import TTFont

    font = TTFont()
    for tag in engrish_font._UNMERGEABLE_TABLES:
        font[tag] = type("Stub", (), {})()
    dropped = engrish_font._strip_unmergeable_tables(font)
    assert sorted(dropped) == sorted(engrish_font._UNMERGEABLE_TABLES)
    for tag in engrish_font._UNMERGEABLE_TABLES:
        assert tag not in font


def test_strip_unmergeable_tables_noop_when_absent() -> None:
    """Missing tables are simply not in the dropped list."""
    from fontTools.ttLib import TTFont

    font = TTFont()
    dropped = engrish_font._strip_unmergeable_tables(font)
    assert dropped == []


def test_unmergeable_tables_includes_known_problem_tags() -> None:
    """Spec test: the tables observed crashing live (ru, grc) must be in the strip set."""
    # ru / grc crash root causes:
    # - MATH (MathGlyphInfo / MathVariants on NotoSansMath)
    # - Schema-mismatch on advanced layout (BASE / STAT / JSTF / variation-related)
    # - Schema-mismatch on vertical metrics (vhea/vmtx — present in
    #   NotoSansSymbols2, absent in NotoSans + NotoSansCypriot — observed live in grc)
    for required in ("MATH", "BASE", "JSTF", "STAT", "HVAR", "VVAR", "MVAR", "vhea", "vmtx", "VORG"):
        assert required in engrish_font._UNMERGEABLE_TABLES, required


def test_pairwise_merge_strips_unmergeable_before_merge() -> None:
    """A real two-source merge where one source has MATH must not crash.

    Uses NotoSans (no MATH) + NotoSansMath (has MATH) — the live ru failure pattern.
    Skipped if either source is not available locally.
    """
    fonts_dir = Path("/home/agent/workspace/fonts/Noto")
    candidates = [
        fonts_dir / "NotoSans[wght].ttf",
        fonts_dir / "NotoSansMath-Regular.ttf",
    ]
    if not all(p.exists() for p in candidates):
        pytest.skip(f"source fonts not available locally: {candidates}")

    from fontTools.ttLib import TTFont
    from fontTools.subset import Options, Subsetter

    fonts: list = []
    for path in candidates:
        font = TTFont(str(path))
        # Subset to a tiny shared codepoint set so the merge has actual work
        # to do but stays cheap. ASCII a-z covers both source fonts.
        opts = Options(notdef_outline=True, layout_features=[], hinting=False)
        subsetter = Subsetter(options=opts)
        subsetter.populate(unicodes=list(range(ord("a"), ord("z") + 1)))
        subsetter.subset(font)
        fonts.append(font)

    # The pre-fix code raises AttributeError here. Post-fix: merge succeeds.
    merged = engrish_font.pairwise_merge(fonts)
    # MATH stripped from the merged result.
    assert "MATH" not in merged
    # cmap survived (the actual coverage is what we care about).
    assert "cmap" in merged
