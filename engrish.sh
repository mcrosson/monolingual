#!/bin/bash
# engrish.sh — top-level shell orchestrator (per D27).
#
# Wraps the per-form Python CLI (per D26: 7 subcommands, --form FORM, no --all
# / --all-singles / --epub / --font flags on `generate`) in loops to build the
# project's full dictionary set. Per-form work is 3 sequential subcommand
# calls: `generate --form X`, `epub --form X`, `font --form X`.
#
# After all forms complete, performs humanized-name + category post-processing
# on the StarDict output directories.

set -e  # abort on first failure (replaces && chaining of the legacy version)

PY="./venv/bin/python"
ENGRISH="$PY engrish.py --keep-xml"

# === Argument parsing ===

ONLY_FORM=""
i=0
for arg in "$@"; do
  i=$((i+1))
  case "$arg" in
    --delete-data)
      rm -rf data
      ;;
    --prepare)
      $ENGRISH prepare
      exit
      ;;
    --run-tests)
      # Post-M11 test tree per D9; pre-M11 tests/test_engrish.py was deleted.
      $PY -m pytest engrish/tests/ -x -vvv -s
      exit
      ;;
    --only)
      # --only FORM — run build_form for a single form, skip singles loop and
      # post-processing. Used by integration tests (M11-AC3) and ad-hoc rebuilds.
      eval "ONLY_FORM=\${$((i+1))}"
      ;;
  esac
done

# build_form: invoke the 3 per-form subcommands in dependency order.
# Each subcommand recurses into its upstream producer per D29 if needed,
# so `generate` will trigger render/parse/prepare on first call.
build_form() {
  local form="$1"
  $ENGRISH generate --form "$form"
  $ENGRISH epub --form "$form"
  $ENGRISH font --form "$form"
}

if [ -n "$ONLY_FORM" ]; then
  # Single-form mode (used for integration testing). Does NOT wipe data/engrish
  # to allow incremental re-runs against the same artifact tree.
  build_form "$ONLY_FORM"
  exit
fi

# === Build (full pipeline) ===

# Wipe per-form output directories from prior runs. Source data (parse DB,
# render JSON, fonts) is NOT touched — D29 presence-on-disk = valid; the
# per-form rebuild reuses upstream artifacts.
rm -rf data/engrish

# === Single-locale forms (was --all-singles in legacy CLI) ===
# Iterate every language key in engrish.json; one form per locale.
SINGLES=$($PY -c "import json; cfg = json.load(open('engrish/engrish.json')); print(' '.join(cfg['languages'].keys()))")
for locale in $SINGLES; do
  build_form "$locale"
done

# === Historical / Diachronic ===
build_form "ang+enm+en"          # Comprehensive English
build_form "grc+el"              # Comprehensive Greek
build_form "fro+frm+fr"          # Comprehensive French
build_form "fa+peo+pal"          # Comprehensive Persian
build_form "ru+cu"               # Slavic Historical
build_form "got+non+ang"         # Germanic

# === Classical / Foundational ===
build_form "la+grc"              # Western Classics
build_form "sa+la+grc"           # Classical Triad

# === Religious Texts ===
build_form "la+grc+he+arc+syc+cop"  # Biblical
build_form "sa+pi"               # Buddhist Canonical
build_form "sa+pi+bo"            # Buddhist Studies

# === Regional Families ===
build_form "la+fr+es+it"         # Romance
build_form "ar+he+arc+syc"       # Semitic
build_form "ar+fa"               # Islamic Studies
build_form "zh+ja"               # East Asian
build_form "egy+akk"             # Ancient Near East

# === Period ===
build_form "la+ang+enm+fr"       # Medieval Western European

# === Modern Practical ===
build_form "en+fr+de+es"         # Modern Western European
build_form "en+fr+de+es+ru"      # Modern Major European

# === Personal ===
build_form "en+enm+ang+es+fr+de+ru+it+el+la+fro+grc"  # Comprehensive 12-locale

# === Post-processing: humanized category layout ===

BASE="data/engrish"
HUMAN="$BASE/humanized"

# Create category directories
mkdir -p \
  "$HUMAN/historical-diachronic" \
  "$HUMAN/classical-foundational" \
  "$HUMAN/religious-texts" \
  "$HUMAN/regional-families" \
  "$HUMAN/period" \
  "$HUMAN/modern-practical" \
  "$HUMAN/personal"

# Copy source form dir into humanized/<category>/<name>,
# then rename immediate children: replace form identifier with human name.
# Form directories on disk use `+` → `-` per engrish.paths.engrish_form_dir().
copy_dict() {
  local form="$1" category="$2" name="$3"
  local src="$BASE/${form//+/-}"
  local dst="$HUMAN/$category/$name"
  local form_us="${form//+/_}"

  cp -r "$src" "$dst"

  for item in "$dst"/*; do
    [ -e "$item" ] || continue
    old="$(basename "$item")"
    new="${old//$form_us/$name}"
    [ "$old" != "$new" ] && mv "$dst/$old" "$dst/$new"
  done
}

# === Historical / Diachronic ===
copy_dict "ang+enm+en"   "historical-diachronic" "comprehensive-english"
copy_dict "grc+el"        "historical-diachronic" "comprehensive-greek"
copy_dict "fro+frm+fr"   "historical-diachronic" "comprehensive-french"
copy_dict "fa+peo+pal"   "historical-diachronic" "comprehensive-persian"
copy_dict "ru+cu"         "historical-diachronic" "slavic-historical"
copy_dict "got+non+ang"  "historical-diachronic" "germanic"

# === Classical / Foundational ===
copy_dict "la+grc"        "classical-foundational" "western-classics"
copy_dict "sa+la+grc"    "classical-foundational" "classical-triad"

# === Religious Texts ===
copy_dict "la+grc+he+arc+syc+cop" "religious-texts" "biblical"
copy_dict "sa+pi"         "religious-texts" "buddhist-canonical"
copy_dict "sa+pi+bo"     "religious-texts" "buddhist-studies"

# === Regional Families ===
copy_dict "la+fr+es+it"  "regional-families" "romance"
copy_dict "ar+he+arc+syc" "regional-families" "semitic"
copy_dict "ar+fa"         "regional-families" "islamic-studies"
copy_dict "zh+ja"         "regional-families" "east-asian"
copy_dict "egy+akk"       "regional-families" "ancient-near-east"

# === Period ===
copy_dict "la+ang+enm+fr" "period" "medieval-western-european"

# === Modern Practical ===
copy_dict "en+fr+de+es"   "modern-practical" "modern-western-european"
copy_dict "en+fr+de+es+ru" "modern-practical" "modern-major-european"

# === Personal ===
copy_dict "en+enm+ang+es+fr+de+ru+it+el+la+fro+grc" "personal" "engrish"
