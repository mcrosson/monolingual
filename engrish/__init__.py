"""engrish — Build dictionaries with Modern English definitions from Wiktionary.

Importing ``engrish`` activates the wikidict shim (``activate()``) so every
subcommand inherits engrish mode. The shim is re-entrant; code that wants a
scoped activation can still use ``with engrish.wikidict_shim.engrish_mode():``.

Before the M2 rewrite, engrish-gated wikidict mutations ran at module-load
time of ``wikidict.lang`` / ``wikidict.constants`` / ``wikidict.namespaces``
via an ``ENGRISH_MODE`` environment-variable check. Those checks are gone.
Activation is now explicit and reversible.
"""

from engrish.wikidict_shim import activate

activate()
