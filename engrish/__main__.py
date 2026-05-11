"""CLI entry point for engrish. Thin delegation to ``engrish.cli.main()``."""

from __future__ import annotations

import sys

from engrish.cli import main


if __name__ == "__main__":
    sys.exit(main())
