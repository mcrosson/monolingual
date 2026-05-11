# engrish — Quick Start

Build StarDict dictionaries with Modern English definitions from Wiktionary.

## Warnings & Considerations

- **Do not run multiple instances at the same time.** The tool and tests share the same `data/` directory for intermediate files (downloads, parse databases, render output). Running multiple instances simultaneously — or running tests while a generate is in progress — will corrupt data.
- **Large languages take a long time.** Languages with many entries (e.g. English at ~1.4M, Spanish at ~640K, Italian at ~560K) can take 20+ minutes to render and convert depending on hardware. Smaller languages (under 10K entries) typically finish in under a minute.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
```

## Usage

The CLI is per-form and per-stage: each subcommand handles exactly one stage for exactly one form. Multi-form orchestration lives in `engrish.sh`. Stages chain via D29 disk-at-boundaries: when a downstream subcommand needs an upstream artifact and it's missing, the upstream stage is invoked recursively.

### See per-language statistics

```bash
./venv/bin/python engrish.py language-stats --locale en
```

Per-locale headword/definition/variant counts from the render JSON. Recurses into `render` (and `prepare`) internally if the render JSON is missing.

### Add languages to the config

```bash
./venv/bin/python engrish.py add-language --lang fr --lang de --lang es
./venv/bin/python engrish.py add-language --all
```

Looks up language codes in the Wiktionary dump and adds them to `engrish.json`. `--lang` may be specified multiple times. `--all` adds every language present in the dump.

### Prepare the Wiktionary dump

```bash
./venv/bin/python engrish.py prepare
./venv/bin/python engrish.py --keep-xml prepare
```

Downloads the EN Wiktionary dump (idempotent if present). Use this to get the slow download out of the way before running other subcommands. The dump is shared across all engrish locales.

### Generate a StarDict dictionary for a form

```bash
./venv/bin/python engrish.py generate --form ang
./venv/bin/python engrish.py generate --form ang+en
./venv/bin/python engrish.py generate --form en+enm+ang+es+fr+de+ru+it+el+la+fro+grc
```

Generates the merged StarDict dictionary for one `+`-separated form. The recursion chain is `generate ← merge ← per-locale .df ← render JSON ← parse ← prepare`; missing upstream artifacts are produced automatically.

For multi-locale forms, the order of locale codes in `--form` controls section order in merged headwords — `ang+en` shows Old English `<h3>` section before Modern English; `en+ang` does the reverse.

### Generate a sampler EPUB

```bash
./venv/bin/python engrish.py epub --form ang+en
```

Generates the EPUB 2 sampler for one form (cover, summary, largest entries, cross-language shared entries, per-locale spot checks, missing-words report). Requires `data/engrish/<form>/` to contain the merged `.df` — recurses into `generate` if missing.

The sampler is for smoke-testing dictionary rendering on your eBook reader before deploying the StarDict files. Verifies headwords, alternates, and pronunciations render correctly with the configured fonts.

EPUBs **never embed fonts** (per project decision D21). Install the engrish-generated TTFs into your reader environment if you want full glyph coverage.

### Generate minimized fonts for a form

```bash
./venv/bin/python engrish.py font --form ang+en
```

Generates 4 minimized TTFs containing only the codepoints present in the merged StarDict for one form:

- `Regular.ttf` — all scripts, regular weight
- `Bold.ttf` — all scripts, bold weight
- `Italic.ttf` — scripts with italic variants only (mainly Latin/Greek/Cyrillic)
- `BoldItalic.ttf` — scripts with italic variants only, bold weight

Output goes to `data/engrish/<form>/fonts/`. A `coverage_gaps.txt` artifact lists any codepoints in the StarDict not covered by any source font (per-codepoint warnings); the build does NOT fail on these — install or configure additional source fonts if coverage is required.

Scripts without published italic font files (Arabic, Devanagari, CJK, etc.) are absent from italic files — the reader falls back to its default rendering for those scripts.

### Update source fonts

```bash
./venv/bin/python engrish.py update-fonts
```

Downloads any Noto Sans fonts referenced in `engrish.json` (per-language `fonts` lists + top-level `seed_fonts`) that aren't already in the local fonts tree. Idempotent — only fetches what's missing.

### `--keep-xml`

Global flag — can be used with any subcommand. Keeps the decompressed Wiktionary XML dump after parsing instead of deleting it. Saves ~3 minutes on subsequent runs by skipping decompression. Sets `KEEP_XML=1` env var consumed by `wikidict.parse`.

### Wiping cached data

To force a full reprocess, delete the `data/` directory (or its `data/<locale>/` subdirectory for one locale):

```bash
rm -r data/
```

Per D29, the pipeline does not check timestamps or hashes — presence on disk is treated as valid. To re-run any stage, delete its output and the next invocation will regenerate it (and recurse to upstream stages as needed).

## Multi-form orchestration: `engrish.sh`

`engrish.sh` is the top-level shell orchestrator (per D27). It wraps the per-form Python CLI in loops to build the project's full 20-form dictionary set, then performs humanized-name and category post-processing on the StarDict output directories.

```bash
./engrish.sh                  # full pipeline: build all 20 forms + post-processing
./engrish.sh --prepare        # download/decompress dump only (fast pre-step)
./engrish.sh --delete-data    # wipe data/ before run
./engrish.sh --run-tests      # run the test suite (engrish/tests/)
./engrish.sh --only ang+en    # build a single form end-to-end (for testing or ad-hoc rebuild); skips singles loop and post-processing
```

For each form, `engrish.sh` calls the three subcommands in sequence: `generate --form X`, `epub --form X`, `font --form X`.

## Output

Per-form artifacts are written to `data/engrish/<form>/` where `<form>` is the locale string with `+` replaced by `-` (e.g. invoking `--form ang+en` writes to `data/engrish/ang-en/`). Each form directory contains:

- StarDict files: `.ifo`, `.idx`, `.dict`, `.dict.dz`, `.syn`, `.idx.oft`, `.syn.oft` — ready to copy to an eBook reader
- EPUB sampler: `<form>.epub` — for smoke-testing in KOreader or other EPUB 2 readers
- Fonts: `fonts/Regular.ttf`, `fonts/Bold.ttf`, `fonts/Italic.ttf`, `fonts/BoldItalic.ttf`, `fonts/coverage_gaps.txt`, `fonts/build.log`

After `engrish.sh` post-processing, StarDict directories are renamed to humanized form names organized by category (historical/diachronic, classical-foundational, religious-texts, etc.).

## Config

`engrish/engrish.json` defines which languages to extract from the English Wiktionary. The `languages` key maps ISO language codes to their Wiktionary section heading, display name, and required fonts. Use `add-language` to populate it.

The `seed_fonts` key lists fonts that are always downloaded and checked first during font detection. Seed fonts resolve characters (Latin, Greek, Cyrillic, common symbols, etc.) that can't be mapped to a script-specific font via the name-prefix heuristic. If `add-language` reports unmatched characters that should be covered by a general-purpose font, adding that font to `seed_fonts` is the correct fix.

The `epub_base_fonts` key lists fonts that are always available to EPUB rendering when a reader has them installed locally. (EPUBs do not embed fonts per D21; this list documents what to install on the reader for full coverage.)

When multiple fonts cover the same characters, font detection uses a deterministic tiebreak: Sans over Serif, then smaller cmap (more targeted font) over larger, then alphabetical. This means a script-specific font like NotoSansArabic will always win over a general font like NotoSans for Arabic characters, and NotoSans will win over NotoSansMath for characters both cover since NotoSans has the smaller cmap.

## Testing

Only needed if you are modifying the engrish source code. The test tree at `engrish/tests/` runs under pytest and uses external-consumer oracles (`sdcv` for StarDict, `epubcheck` for EPUB, `fontTools` for fonts).

```bash
./venv/bin/python -m pytest engrish/tests/
```

Cost-gated golden-file regression tests are opt-in (run the full pipeline against committed byte-stable goldens for 7 small locales):

```bash
ENGRISH_RUN_GOLDENS=1 ./venv/bin/python -m pytest engrish/tests/test_m9_goldens.py
```

The pre-rewrite test suite at `tests/test_engrish.py` was deleted at M11 per project decision D9. The remaining `tests/` tree is the upstream wikidict test suite and is out of scope for this fork.
