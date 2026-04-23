"""CLI entry point for engrish — run via `python engrish.py` or `python -m engrish`."""

from __future__ import annotations

import argparse
import logging
import os
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
    parser.add_argument(
        "--keep-xml",
        action="store_true",
        help="Keep the decompressed Wiktionary XML dump after parsing (saves ~3 min on subsequent runs)",
    )
    subparsers = parser.add_subparsers(dest="command")

    gen_parser = subparsers.add_parser("generate", help="Generate StarDict dictionaries")
    gen_group = gen_parser.add_mutually_exclusive_group(required=True)
    gen_group.add_argument(
        "--engrish-type",
        action="append",
        metavar="FORM",
        help=f"Locale code or '+'-separated codes to build. May be specified multiple times. Available: {available}",
    )
    gen_group.add_argument(
        "--all",
        action="store_true",
        help="Build all configured locales combined into one dictionary",
    )
    gen_group.add_argument(
        "--all-singles",
        action="store_true",
        help="Build a separate single-language dictionary for each configured locale",
    )
    gen_parser.add_argument(
        "--epub",
        action="store_true",
        help="Generate a sampler EPUB for each dictionary after building it",
    )
    gen_parser.add_argument(
        "--font",
        action="store_true",
        help="Generate minimized fonts for each dictionary after building it",
    )

    epub_parser = subparsers.add_parser("epub", help="Generate sampler EPUB for existing dictionaries")
    _add_all_or_items(
        epub_parser,
        flag="dict",
        metavar="FORM",
        help_item="Dictionary form to generate EPUB for (e.g. ang+en). May be specified multiple times.",
        help_all="Generate EPUBs for all existing dictionaries",
    )

    font_parser = subparsers.add_parser("font", help="Generate minimized fonts for existing dictionaries")
    _add_all_or_items(
        font_parser,
        flag="dict",
        metavar="FORM",
        help_item="Dictionary form to generate fonts for (e.g. ang+en). May be specified multiple times.",
        help_all="Generate fonts for all existing dictionaries",
    )

    subparsers.add_parser(
        "prepare",
        help="Download and decompress the EN Wiktionary dump",
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

    subparsers.add_parser(
        "update-fonts",
        help="Download missing Noto Sans fonts referenced in the config",
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    if args.keep_xml:
        os.environ["KEEP_XML"] = "1"

    if args.no_cache:
        log.info("Clearing cache for all locales")
        delete_cache(list(ALL_LOCALES))

    if args.command == "generate":
        from .generate import process_form
        from .pipeline import run_wikidict

        # Collect all forms and their locales
        forms: list[tuple[str, list[str]]] = []
        if args.all:
            all_form = "+".join(ALL_LOCALES)
            forms.append((all_form, _parse_form(all_form)))
        elif args.all_singles:
            for locale in ALL_LOCALES:
                forms.append((locale, [locale]))
        else:
            for form in args.engrish_type:
                forms.append((form, _parse_form(form)))

        # Phase 1: run wikidict pipeline for all unique locales (once)
        all_locales: list[str] = []
        for _, locales in forms:
            all_locales.extend(locales)
        # Deduplicate while preserving order
        run_wikidict(list(dict.fromkeys(all_locales)))

        # Restore console logging (wikidict's setup_logging redirects to file)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
            force=True,
        )

        # Phase 2: generate engrish output per form
        import gc

        for form, locales in forms:
            process_form(form, locales)
            gc.collect()

        # Phase 3: optional EPUB generation
        if args.epub:
            from .epub import generate_epub
            from .paths import dict_base_name, engrish_form_dir, get_snapshot_date

            for form, locales in forms:
                date = get_snapshot_date(locales)
                form_dir = engrish_form_dir(form)
                epub_path = form_dir / f"test-{dict_base_name(form, date)}.epub"
                log.info("Generating sampler EPUB: %s", epub_path)
                generate_epub(locales, epub_path, form=form)
                gc.collect()

        # Phase 4: optional font generation
        if args.font:
            from .font import generate_fonts
            from .paths import engrish_form_dir as form_dir_fn

            for form, locales in forms:
                form_dir = form_dir_fn(form)
                log.info("Generating minimized fonts: %s", form_dir)
                generate_fonts(locales, form_dir, form=form)
                gc.collect()

    elif args.command == "prepare":
        from wikidict import download

        download.main("en")  # All engrish locales use EN Wiktionary

    elif args.command == "epub":
        from .epub import run as run_epub

        return run_epub(args.dict, all_dicts=args.all)

    elif args.command == "add-language":
        from .add_language import run as run_add_language

        return run_add_language(args.lang, all_langs=args.all)

    elif args.command == "language-stats":
        from .stats import run

        return run()

    elif args.command == "font":
        from .font import run_font

        return run_font(args.dict, all_dicts=args.all)

    elif args.command == "update-fonts":
        from .font import run_update

        return run_update()

    return 0


if __name__ == "__main__":
    sys.exit(main())
