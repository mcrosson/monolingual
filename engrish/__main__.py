"""CLI entry point for engrish. Thin delegation to ``engrish.cli.main()``.

The full legacy dispatch lives at ``engrish.__main___legacy`` and is reachable
via ``python -m engrish.__main___legacy ...`` during the M3-M10 transition
window. Post-M11, the legacy path is deleted per D9.
"""

from __future__ import annotations

import sys

from engrish.cli import main

# Back-compat re-export for ``tests/test_engrish.py`` until D9 deletes the file
# at M11. ``_parse_form`` lives at its canonical home in ``__main___legacy``.
from engrish.__main___legacy import _parse_form  # noqa: F401


if __name__ == "__main__":
    sys.exit(main())
