"""CLI dispatch for the engrish rewrite.

Seven subcommands per I19: ``prepare``, ``language-stats``, ``add-language``,
``generate``, ``epub``, ``font``, ``update-fonts``. At M3, only ``add-language``
is wired to real behavior (via ``engrish.add_language``); the other six are
scaffold stubs per M3-AC3 — they raise ``NotImplementedError`` naming the
owning milestone. M4-M8 fill them in.

Legacy CLI remains available at ``python -m engrish.__main___legacy`` for users
who need the pre-rewrite dispatch (``generate`` with real pipeline, etc.) during
the M3-M10 transition window.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import NoReturn

log = logging.getLogger(__name__)


def _not_implemented(subcommand: str, milestone: str) -> NoReturn:
    raise NotImplementedError(
        f"subcommand '{subcommand}' is not yet implemented; owning milestone is "
        f"{milestone}. Until then, invoke the legacy CLI via "
        f"`python -m engrish.__main___legacy {subcommand} ...`."
    )


def _cmd_prepare(args: argparse.Namespace) -> int:
    _not_implemented("prepare", "M4")


def _cmd_language_stats(args: argparse.Namespace) -> int:
    _not_implemented("language-stats", "M4")


def _cmd_generate(args: argparse.Namespace) -> int:
    _not_implemented("generate", "M6")


def _cmd_epub(args: argparse.Namespace) -> int:
    _not_implemented("epub", "M7")


def _cmd_font(args: argparse.Namespace) -> int:
    _not_implemented("font", "M8")


def _cmd_update_fonts(args: argparse.Namespace) -> int:
    _not_implemented("update-fonts", "M8")


def _cmd_add_language(args: argparse.Namespace) -> int:
    # M3-AC2: config-mutating; no pipeline artifacts touched.
    from engrish.add_language import run as legacy_add_language_run

    langs: list[str] | None = args.lang
    return legacy_add_language_run(langs, all_langs=args.all)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engrish",
        description="Build dictionaries with Modern English definitions from Wiktionary.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser(
        "prepare",
        help="Download and decompress the EN Wiktionary dump (M4)",
    )
    p_prepare.set_defaults(func=_cmd_prepare)

    p_stats = sub.add_parser(
        "language-stats",
        help="Per-language statistics from the EN Wiktionary dump (M4)",
    )
    p_stats.set_defaults(func=_cmd_language_stats)

    p_addlang = sub.add_parser(
        "add-language",
        help="Add one or more languages to engrish.json (config-only)",
    )
    group = p_addlang.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--lang",
        action="append",
        metavar="CODE",
        help="Locale code to add; may be specified multiple times.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Add every known language from the Wiktionary dump.",
    )
    p_addlang.set_defaults(func=_cmd_add_language)

    p_gen = sub.add_parser(
        "generate",
        help="Generate StarDict dictionaries (M6)",
    )
    p_gen.set_defaults(func=_cmd_generate)

    p_epub = sub.add_parser(
        "epub",
        help="Generate sampler EPUBs for built dictionaries (M7)",
    )
    p_epub.set_defaults(func=_cmd_epub)

    p_font = sub.add_parser(
        "font",
        help="Generate minimized fonts for built dictionaries (M8)",
    )
    p_font.set_defaults(func=_cmd_font)

    p_updf = sub.add_parser(
        "update-fonts",
        help="Download missing Noto Sans source fonts (M8)",
    )
    p_updf.set_defaults(func=_cmd_update_fonts)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
