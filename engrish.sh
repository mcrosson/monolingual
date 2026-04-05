#!/bin/bash

# cleanup & test run capabilities
#    here for completeness, the main body of the script below is the primary use case and does not get a dedicated flag
for arg in "$@"; do
  case "$arg" in
    --delete-data)
      rm -r data
      exit
      ;;
    --run-tests)
      ./venv/bin/python engrish.py --keep-xml prepare
      ./venv/bin/python -m pytest tests/test_engrish.py -x -vvv -s
      exit
      ;;
  esac
done

# data processing

rm -r data/engrish

./venv/bin/python engrish.py generate --all-singles --epub && \
./venv/bin/python engrish.py generate --engrish-type ang+enm+en --epub && \
./venv/bin/python engrish.py generate --engrish-type grc+el --epub && \
./venv/bin/python engrish.py generate --engrish-type fro+frm+fr --epub && \
./venv/bin/python engrish.py generate --engrish-type fa+peo+pal --epub && \
./venv/bin/python engrish.py generate --engrish-type ru+cu --epub && \
./venv/bin/python engrish.py generate --engrish-type got+non+ang --epub && \
./venv/bin/python engrish.py generate --engrish-type la+grc --epub && \
./venv/bin/python engrish.py generate --engrish-type sa+la+grc --epub && \
./venv/bin/python engrish.py generate --engrish-type la+grc+he+arc+syc+cop --epub && \
./venv/bin/python engrish.py generate --engrish-type sa+pi --epub && \
./venv/bin/python engrish.py generate --engrish-type sa+pi+bo --epub && \
./venv/bin/python engrish.py generate --engrish-type la+fr+es+it --epub && \
./venv/bin/python engrish.py generate --engrish-type ar+he+arc+syc --epub && \
./venv/bin/python engrish.py generate --engrish-type ar+fa --epub && \
./venv/bin/python engrish.py generate --engrish-type zh+ja --epub && \
./venv/bin/python engrish.py generate --engrish-type egy+akk --epub && \
./venv/bin/python engrish.py generate --engrish-type la+ang+enm+fr --epub && \
./venv/bin/python engrish.py generate --engrish-type en+fr+de+es --epub && \
./venv/bin/python engrish.py generate --engrish-type en+fr+de+es+ru --epub && \
./venv/bin/python engrish.py generate --engrish-type en+enm+ang+es+fr+de+ru+it+el+la+fro+grc --epub

# === Historical / Diachronic ===
# ang+enm+en          # Comprehensive English
# grc+el              # Comprehensive Greek
# fro+frm+fr          # Comprehensive French
# fa+peo+pal          # Comprehensive Persian
# ru+cu               # Slavic Historical
# got+non+ang         # Germanic

# === Classical / Foundational ===
# la+grc              # Western Classics
# sa+la+grc           # Classical Triad

# === Religious Texts ===
# la+grc+he+arc+syc+cop  # Biblical
# sa+pi               # Buddhist Canonical
# sa+pi+bo            # Buddhist Studies

# === Regional Families ===
# la+fr+es+it         # Romance
# ar+he+arc+syc       # Semitic
# ar+fa               # Islamic Studies
# zh+ja               # East Asian
# egy+akk             # Ancient Near East

# === Period ===
# la+ang+enm+fr       # Medieval Western European

# === Modern Practical ===
# en+fr+de+es         # Modern Western European
# en+fr+de+es+ru      # Modern Major European

BASE="data/engrish"
HUMAN="$BASE/humanized"

# Create category directories
mkdir -p \
  "$HUMAN/historical-diachronic" \
  "$HUMAN/classical-foundational" \
  "$HUMAN/religious-texts" \
  "$HUMAN/regional-families" \
  "$HUMAN/period" \
  "$HUMAN/modern-practical"

# Copy source form dir into humanized/<category>/<name>,
# then rename immediate children: replace form identifier with human name
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
