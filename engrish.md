# engrish — Quick Start

Build StarDict dictionaries with Modern English definitions from Wiktionary.

## Setup

```bash
python3.13 -m venv venv
. venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Usage

### See what languages are available

```bash
python engrish.py language-stats
```

Downloads and parses the EN Wiktionary dump (if needed), then shows every language with definition counts.

### Add languages to the config

```bash
python engrish.py add-language --lang fr --lang de --lang es
```

Looks up the language codes in the dump and adds them to `engrish.json`.

### Generate dictionaries

```bash
python engrish.py generate --engrish-type ang+en
python engrish.py generate --engrish-type fr
python engrish.py generate --all
```

Downloads, parses, renders, and converts Wiktionary data into StarDict dictionaries. This takes a while on first run.

### Generate sampler EPUBs

```bash
python engrish.py epub --dict ang+en
python engrish.py epub --all
```

Creates a test EPUB with sample entries from existing dictionaries.

### `--no-cache`

Global flag — can be used with any subcommand. Deletes all cached downloads, parse databases, and pre-processing data before the subcommand runs, forcing a full reprocess.

## Output

Dictionaries are written to `data/engrish/<form>/`, where `<form>` is the locale string with `+` replaced by `-` (e.g. `data/engrish/ang-en/`). Each form directory contains StarDict files (`.ifo`, `.idx`, `.dict`, `.syn`, `.oft`) ready to copy to an eBook reader.

## Config

`engrish.json` in the project root defines which languages to extract from the English Wiktionary. Each entry maps an ISO language code to its Wiktionary section heading and display name. Use `add-language` to populate it.
