"""engrish — Build dictionaries with Modern English definitions from Wiktionary."""

import os

# ENGRISH_MODE must be set before any wikidict.lang import.
# wikidict/lang/__init__.py runs engrish locale registration at module load
# time and checks this env var. Setting it here is intentional and permanent
# for the process lifetime — this is a known constraint of the injection design.
os.environ["ENGRISH_MODE"] = "1"
