"""CLI dispatch for the engrish rewrite.

Seven subcommands per I19: ``prepare``, ``language-stats``, ``add-language``,
``generate``, ``epub``, ``font``, ``update-fonts``. M4 wires ``prepare`` and
``language-stats`` to real behavior (via ``engrish.stages.*``); the render
stage is the internal producer for the per-locale JSON — it is NOT a CLI
subcommand per I19. ``language-stats`` (and later ``generate``) invoke it
programmatically via ``require_artifact`` when the render JSON is missing.
``generate`` / ``epub`` / ``font`` / ``update-fonts`` remain stubs until
their owning milestones. Post-M11 (2026-05-02) the pre-rewrite legacy CLI
(``engrish/__main___legacy.py``) was deleted; ``engrish.cli`` is now the
only dispatch path.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

log = logging.getLogger(__name__)


def _cmd_prepare(args: argparse.Namespace) -> int:
    from engrish.stages.prepare import run

    # Locale is nominal for prepare (dump is shared across all engrish locales).
    return run(args.locale or "en")


def _cmd_language_stats(args: argparse.Namespace) -> int:
    from engrish.stages.language_stats import run

    return run(args.locale)


def _cmd_generate(args: argparse.Namespace) -> int:
    from engrish.stages.generate import run

    return run(args.form)


def _cmd_epub(args: argparse.Namespace) -> int:
    from engrish.stages.epub_stage import run

    return run(args.form)


def _cmd_font(args: argparse.Namespace) -> int:
    from engrish.stages.font_stage import run

    return run(args.form)


def _cmd_update_fonts(args: argparse.Namespace) -> int:
    from engrish.stages.update_fonts_stage import run

    return run()


def _cmd_add_language(args: argparse.Namespace) -> int:
    from engrish.stages.add_language_stage import run

    langs: list[str] | None = args.lang
    return run(langs, all_langs=args.all)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engrish",
        description="Build dictionaries with Modern English definitions from Wiktionary.",
    )
    parser.add_argument(
        "--keep-xml",
        action="store_true",
        help=(
            "Keep the decompressed Wiktionary XML dump after parsing. "
            "Trade-off: ~10 GB disk vs ~3 min re-decompress on each run. "
            "Sets KEEP_XML=1 env var, which wikidict.parse reads to decide "
            "whether to delete pages-*.xml post-parse."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser(
        "prepare",
        help="Download the EN Wiktionary dump (idempotent if present)",
    )
    p_prepare.add_argument(
        "--locale",
        default=None,
        help="Locale code (informational only; dump is shared across engrish locales).",
    )
    p_prepare.set_defaults(func=_cmd_prepare)

    p_stats = sub.add_parser(
        "language-stats",
        help="Per-locale headword/definition/variant counts from the render JSON",
    )
    p_stats.add_argument(
        "--locale",
        required=True,
        help="Locale code. Recurses into render (and prepare) internally if needed.",
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
        help="Generate a merged StarDict dictionary for a '+'-separated form",
    )
    p_gen.add_argument(
        "--form",
        required=True,
        metavar="FORM",
        help="'+'-separated locale codes (e.g. 'en+ang+enm'). Recurses through render/parse/prepare.",
    )
    p_gen.set_defaults(func=_cmd_generate)

    p_epub = sub.add_parser(
        "epub",
        help="Generate an EPUB 2 sampler for a '+'-separated form",
    )
    p_epub.add_argument(
        "--form",
        required=True,
        metavar="FORM",
        help="'+'-separated locale codes. Recurses into generate → merge → df → render if needed.",
    )
    p_epub.set_defaults(func=_cmd_epub)

    p_font = sub.add_parser(
        "font",
        help="Generate 4 minimized TTFs (Regular/Bold/Italic/BoldItalic) for a form",
    )
    p_font.add_argument(
        "--form",
        required=True,
        metavar="FORM",
        help="'+'-separated locale codes. Recurses through generate→merge→df→render if needed.",
    )
    p_font.set_defaults(func=_cmd_font)

    p_updf = sub.add_parser(
        "update-fonts",
        help="Download missing Noto source fonts referenced in engrish.json (idempotent)",
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
    if getattr(args, "keep_xml", False):
        os.environ["KEEP_XML"] = "1"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
