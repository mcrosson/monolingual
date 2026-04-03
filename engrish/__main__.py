"""CLI entry point for engrish — run via `python engrish.py` or `python -m engrish`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ALL_LOCALES
from .pipeline import delete_cache

log = logging.getLogger(__name__)


def _parse_form(form: str) -> list[str]:
    """Parse a form string into a validated list of locale codes."""
    codes = [c.strip() for c in form.split("+") if c.strip()]
    if not codes:
        print("Error: empty form string", file=sys.stderr)
        sys.exit(1)
    for code in codes:
        if code not in ALL_LOCALES:
            print(
                f"Error: unknown locale '{code}'. "
                f"Available: {', '.join(ALL_LOCALES)}",
                file=sys.stderr,
            )
            sys.exit(1)
    return codes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    available = ", ".join(ALL_LOCALES)
    parser = argparse.ArgumentParser(
        description="engrish — Build dictionaries with Modern English definitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Delete all cached downloads and pre-processing data before running",
    )
    subparsers = parser.add_subparsers(dest="command")

    gen_parser = subparsers.add_parser("generate", help="Generate StarDict dictionaries")
    gen_parser.add_argument(
        "--engrish-type",
        action="append",
        required=True,
        metavar="FORM",
        help=f"Locale code or '+'-separated codes to build. May be specified multiple times. Available: {available}, all",
    )

    subparsers.add_parser(
        "language-stats",
        help="Show per-language statistics from the EN Wiktionary dump",
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    if args.no_cache:
        log.info("Clearing cache for all locales")
        delete_cache(list(ALL_LOCALES))

    if args.command == "generate":
        from .generate import process_form

        for form in args.engrish_type:
            if form == "all":
                all_form = "+".join(ALL_LOCALES)
                process_form(all_form, _parse_form(all_form))
            else:
                locales = _parse_form(form)
                process_form(form, locales)

    elif args.command == "language-stats":
        from .stats import run

        return run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
