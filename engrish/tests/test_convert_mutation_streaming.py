"""M1b-AC2 (file #2 convert.py): mutation-detection + streaming-write golden.

Two concerns flagged in the Round-2 wikidict audit:

1. **deepcopy removal defense.** ``wikidict/convert.py:241`` replaced
   ``details = deepcopy(chosen_word)`` with ``details = chosen_word``. Audit
   confirmed no current mutation path touches ``details.X`` in ``handle_word``
   (only ``words[variant].is_variant = True`` at the variants-loop, which never
   targets the own entry thanks to ``variants.discard(word)`` /
   ``variants.discard(current_word)``). This test pins the invariant: calling
   ``handle_word(word, words)`` does NOT mutate ``words[word]`` itself. Other
   entries may still change (that's the intentional variant-marking behaviour).

2. **streaming write byte-parity.** ``DictFileFormat.process`` streams entries
   with ``fh.write(formatted_word)`` in a per-word loop. This test verifies the
   streamed file is byte-identical to what a single batched
   ``Path.write_text(concatenated)`` call would produce, catching encoding or
   newline drift between streaming and batched writes.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from wikidict import convert
from wikidict.stubs import Word, Words


@pytest.fixture
def small_words() -> Words:
    """Fixture exercising both the own-entry and variant-mutation code paths."""
    return {
        "main": Word(
            definitions={"Noun": ["main definition"]},
            variants=["alt"],
        ),
        "alt": Word(
            definitions={"Noun": ["alt definition"]},
        ),
        "solo": Word(
            definitions={"Noun": ["solo definition"]},
        ),
    }


def test_handle_word_does_not_mutate_own_entry(
    small_words: Words, tmp_path: Path
) -> None:
    variants = convert.make_variants(small_words)
    fmt = convert.DictFileFormat("fr", tmp_path, small_words, variants, "20260422")

    for word in list(small_words):
        snapshot = deepcopy(small_words[word])
        list(fmt.handle_word(word, small_words))
        assert small_words[word] == snapshot, (
            f"handle_word({word!r}) mutated words[{word!r}] directly — "
            "deepcopy removal defense has been breached."
        )


def test_streaming_write_matches_batched_write_text(
    small_words: Words, tmp_path: Path
) -> None:
    variants = convert.make_variants(small_words)

    # Streaming path: DictFileFormat.process uses open().write() per word.
    streaming_dir = tmp_path / "streaming"
    streaming_dir.mkdir()
    fmt_stream = convert.DictFileFormat("fr", streaming_dir, small_words, variants, "20260422")
    fmt_stream.process()
    streaming_files = list(streaming_dir.glob("*.df"))
    assert len(streaming_files) == 1, f"expected one .df file, got {streaming_files}"
    streaming_bytes = streaming_files[0].read_bytes()

    # Batched path: concatenate all handle_word output, single Path.write_text.
    batched_dir = tmp_path / "batched"
    batched_dir.mkdir()
    fmt_batch = convert.DictFileFormat("fr", batched_dir, small_words, variants, "20260422")
    batched_content = "".join(
        formatted
        for word in small_words
        for formatted in fmt_batch.handle_word(word, small_words)
    )
    batched_file = batched_dir / streaming_files[0].name
    batched_file.write_text(batched_content, encoding="utf-8")

    assert streaming_bytes == batched_file.read_bytes(), (
        "streaming output differs from batched Path.write_text — encoding or "
        "newline drift between streaming and batched writes."
    )
