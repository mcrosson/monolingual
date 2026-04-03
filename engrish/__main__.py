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


def _add_all_or_items(parser: argparse.ArgumentParser, flag: str, metavar: str, help_item: str, help_all: str) -> None:
    """Add a mutually exclusive group with --flag (repeatable) and --all."""
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        f"--{flag}",
        action="append",
        metavar=metavar,
        help=help_item,
    )
    group.add_argument(
        "--all",
        action="store_true",
        help=help_all,
    )


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
    _add_all_or_items(
        gen_parser,
        flag="engrish-type",
        metavar="FORM",
        help_item=f"Locale code or '+'-separated codes to build. May be specified multiple times. Available: {available}",
        help_all="Build all configured locales combined",
    )

    epub_parser = subparsers.add_parser("epub", help="Generate sampler EPUB for existing dictionaries")
    _add_all_or_items(
        epub_parser,
        flag="dict",
        metavar="FORM",
        help_item="Dictionary form to generate EPUB for (e.g. ang+en). May be specified multiple times.",
        help_all="Generate EPUBs for all existing dictionaries",
    )

    add_lang_parser = subparsers.add_parser("add-language", help="Add languages to engrish.json config")
    _add_all_or_items(
        add_lang_parser,
        flag="lang",
        metavar="CODE",
        help_item="ISO language code to add (e.g. fr, de, es). May be specified multiple times.",
        help_all="Add every language from the dump to the config",
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

        if args.all:
            all_form = "+".join(ALL_LOCALES)
            process_form(all_form, _parse_form(all_form))
        else:
            for form in args.engrish_type:
                locales = _parse_form(form)
                process_form(form, locales)

    elif args.command == "epub":
        from .epub import run as run_epub

        return run_epub(args.dict, all_dicts=args.all)

    elif args.command == "add-language":
        from .add_language import run as run_add_language

        return run_add_language(args.lang, all_langs=args.all)

    elif args.command == "language-stats":
        from .stats import run

        return run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
