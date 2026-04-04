# engrish — Quick Start

Build StarDict dictionaries with Modern English definitions from Wiktionary.

## Warnings & Considerations

- **Do not run multiple instances at the same time.** The tool and tests share the same `data/` directory for intermediate files (downloads, parse databases, render output). Running multiple instances simultaneously — or running tests while a generate is in progress — will corrupt data.
- **Large languages take a long time.** Languages with many entries (e.g. English at ~900K, Spanish at ~640K, Italian at ~560K) can take 20+ minutes to render and convert depending on hardware. Smaller languages (under 10K entries) typically finish in under a minute.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
```

## Usage

### See what languages are available

```bash
./venv/bin/python engrish.py language-stats
```

Downloads and parses the EN Wiktionary dump (if needed), then shows every language with definition counts.

### Add languages to the config

```bash
./venv/bin/python engrish.py add-language --lang fr --lang de --lang es
```

Looks up the language codes in the dump and adds them to `engrish.json`.

### Generate dictionaries

```bash
./venv/bin/python engrish.py generate --engrish-type ang+en
./venv/bin/python engrish.py generate --engrish-type fr
./venv/bin/python engrish.py generate --all
./venv/bin/python engrish.py generate --all-singles
```

Downloads, parses, renders, and converts Wiktionary data into StarDict dictionaries. This takes a while on first run. `--all` builds every configured locale combined into one dictionary. `--all-singles` builds a separate single-language dictionary for each configured locale.

### `--keep-xml`

Global flag — can be used with any subcommand. Keeps the decompressed Wiktionary XML dump after parsing instead of deleting it. Saves ~3 minutes on subsequent runs by skipping decompression.

### Generate sampler EPUBs

```bash
./venv/bin/python engrish.py epub --dict ang+en
./venv/bin/python engrish.py epub --all
```

Creates a test EPUB with sample entries from existing dictionaries.

### `--no-cache`

Global flag — can be used with any subcommand. Deletes all cached downloads, parse databases, and pre-processing data before the subcommand runs, forcing a full reprocess. To wipe the cache manually, delete the `data/` directory.

## Output

Dictionaries are written to `data/engrish/<form>/`, where `<form>` is the locale string with `+` replaced by `-` (e.g. `data/engrish/ang-en/`). Each form directory contains StarDict files (`.ifo`, `.idx`, `.dict`, `.syn`, `.oft`) ready to copy to an eBook reader.

## Config

`engrish/engrish.json` defines which languages to extract from the English Wiktionary. The `languages` key maps ISO language codes to their Wiktionary section heading, display name, and required fonts. Use `add-language` to populate it.

The `seed_fonts` key lists fonts that are always downloaded and checked first during font detection. Seed fonts resolve characters (Latin, Greek, Cyrillic, common symbols, etc.) that can't be mapped to a script-specific font via the name-prefix heuristic. If `add-language` reports unmatched characters that should be covered by a general-purpose font, adding that font to `seed_fonts` is the correct fix.

When multiple fonts cover the same characters, font detection uses a deterministic tiebreak: Sans over Serif, then smaller cmap (more targeted font) over larger, then alphabetical. This means a script-specific font like NotoSansArabic will always win over a general font like NotoSans for Arabic characters, and NotoSans will win over NotoSansMath for characters both cover since NotoSans has the smaller cmap.

## Testing

Only needed if you are modifying the engrish source code. The test suite runs the full pipeline against real Wiktionary data and verifies dictionary generation, merging, EPUB output, and stats. The first run is slow (it generates StarDict dictionaries); subsequent runs reuse cached output.

```bash
./venv/bin/python -m pytest tests/test_engrish.py -x -vvv -s
```

The `-s` flag is important — it disables pytest's output capture so you can see progress bars and log messages during the pipeline steps.
