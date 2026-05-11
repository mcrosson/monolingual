"""M11-AC1 — Automated audit: engrish.md ↔ engrish.cli surface parity.

Asserts the four [[goal-docs-in-sync]] / [[goal-cli-in-sync]] contracts:
- Every subcommand in engrish.md exists in code (parser).
- Every parser subcommand mentioned in engrish.md.
- Every flag shown in an engrish.md example exists in the parser for that subcommand.
- Every parser flag mentioned somewhere in engrish.md (example or prose).

Diagnostic test prints a drift table when the contracts fail, so the failure
message is actionable.
"""

from __future__ import annotations

import re
from argparse import ArgumentParser, _SubParsersAction
from pathlib import Path

import pytest

from engrish.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENGRISH_MD = REPO_ROOT / "engrish.md"

# Subcommands that may legitimately not appear as `engrish.py X` in a code
# block — e.g. they're only documented in prose. (None today; placeholder for
# future flexibility.)
PROSE_ONLY_SUBCOMMANDS: set[str] = set()

# Argparse-injected flags universal to every parser; not require explicit
# mention in engrish.md.
UNIVERSAL_FLAGS: set[str] = {"--help"}


def _parser_subcommands(parser: ArgumentParser) -> dict[str, set[str]]:
    """Return {subcommand: {flag_names}} from the new CLI parser."""
    out: dict[str, set[str]] = {}
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            for sub_name, sub_parser in action.choices.items():
                flags: set[str] = set()
                for sub_action in sub_parser._actions:
                    for opt_str in sub_action.option_strings:
                        if opt_str.startswith("--"):
                            flags.add(opt_str)
                out[sub_name] = flags
    return out


def _parser_global_flags(parser: ArgumentParser) -> set[str]:
    """Top-level flags (not subcommand-scoped)."""
    flags: set[str] = set()
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            continue
        for opt_str in action.option_strings:
            if opt_str.startswith("--"):
                flags.add(opt_str)
    return flags


# Match `engrish.py <subcommand> [args...]` inside fenced code blocks. The
# pattern allows the leading `./venv/bin/python` shim and the optional global
# `--keep-xml` flag before the subcommand.
_INVOCATION_RE = re.compile(
    # Negative lookbehind on \w excludes paths like `test_engrish.py` (the `_`
    # before `engrish.py` is a word char). The standalone `engrish.py` is always
    # preceded by `/` or whitespace, neither of which is a word char.
    r"(?<!\w)engrish\.py(?:\s+--[\w-]+)*\s+(?P<sub>[\w-]+)(?P<rest>(?:\s+--[\w-]+(?:\s+\S+)?)*)",
)
_FLAG_RE = re.compile(r"--[\w-]+")


def _md_examples(md_text: str) -> list[tuple[str, set[str]]]:
    """Return [(subcommand, flags_used_in_this_example), ...] from md examples."""
    examples: list[tuple[str, set[str]]] = []
    in_code = False
    for line in md_text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            continue
        for match in _INVOCATION_RE.finditer(line):
            sub = match.group("sub")
            rest = match.group("rest") or ""
            flags = set(_FLAG_RE.findall(rest))
            examples.append((sub, flags))
    return examples


def _md_all_flag_mentions(md_text: str) -> set[str]:
    """Every `--flag` token referenced anywhere in the doc (code or prose)."""
    return set(_FLAG_RE.findall(md_text))


@pytest.fixture(scope="module")
def parser_surface() -> dict[str, set[str]]:
    return _parser_subcommands(build_parser())


@pytest.fixture(scope="module")
def parser_global() -> set[str]:
    return _parser_global_flags(build_parser())


@pytest.fixture(scope="module")
def md_text() -> str:
    assert ENGRISH_MD.exists(), f"{ENGRISH_MD} not found"
    return ENGRISH_MD.read_text(encoding="utf-8")


def test_engrish_md_exists() -> None:
    assert ENGRISH_MD.exists(), f"{ENGRISH_MD} missing"


def test_md_examples_parse_at_least_one(md_text: str) -> None:
    """Sanity: the regex finds at least one engrish.py invocation."""
    examples = _md_examples(md_text)
    assert examples, "no engrish.py invocations found in engrish.md code blocks"


def test_every_md_subcommand_exists_in_parser(
    md_text: str, parser_surface: dict[str, set[str]]
) -> None:
    """M11-AC1.a — Every subcommand in engrish.md exists in code."""
    examples = _md_examples(md_text)
    md_subs = {sub for sub, _ in examples}
    parser_subs = set(parser_surface.keys())
    missing_in_code = md_subs - parser_subs
    assert not missing_in_code, (
        f"engrish.md documents subcommand(s) not in CLI: {sorted(missing_in_code)}. "
        f"Parser has: {sorted(parser_subs)}"
    )


def test_every_parser_subcommand_documented(
    md_text: str, parser_surface: dict[str, set[str]]
) -> None:
    """M11-AC1.b — Every code subcommand mentioned in engrish.md."""
    examples = _md_examples(md_text)
    md_subs = {sub for sub, _ in examples} | PROSE_ONLY_SUBCOMMANDS
    parser_subs = set(parser_surface.keys())
    missing_in_doc = parser_subs - md_subs
    assert not missing_in_doc, (
        f"CLI subcommand(s) not documented in engrish.md: {sorted(missing_in_doc)}. "
        f"Add an `engrish.py X ...` example for each."
    )


def test_every_md_flag_exists_in_parser_for_its_subcommand(
    md_text: str, parser_surface: dict[str, set[str]], parser_global: set[str]
) -> None:
    """M11-AC1.c — Every flag shown in engrish.md examples exists in the
    parser for that subcommand (or is a documented global flag)."""
    examples = _md_examples(md_text)
    drift: list[str] = []
    for sub, flags in examples:
        sub_flags = parser_surface.get(sub, set())
        for flag in flags:
            if flag not in sub_flags and flag not in parser_global:
                drift.append(f"engrish.py {sub} {flag} — flag not in parser")
    assert not drift, "Documented flags not in parser:\n  " + "\n  ".join(drift)


def test_every_parser_flag_mentioned_in_doc(
    md_text: str, parser_surface: dict[str, set[str]], parser_global: set[str]
) -> None:
    """M11-AC1.d — Every parser flag mentioned somewhere in engrish.md."""
    all_parser_flags = set(parser_global)
    for flags in parser_surface.values():
        all_parser_flags |= flags
    all_parser_flags -= UNIVERSAL_FLAGS
    md_mentions = _md_all_flag_mentions(md_text)
    undocumented = all_parser_flags - md_mentions
    assert not undocumented, (
        f"Parser flag(s) never mentioned in engrish.md: {sorted(undocumented)}. "
        "Add to an example or prose section."
    )


def test_engrish_sh_referenced_in_md_actually_exists() -> None:
    """M13-AC6 — every `./engrish.sh` reference in engrish.md presupposes the
    file exists at workspace root. Catches the doc-orphan that nearly landed
    when engrish.sh was briefly deleted 2026-05-09."""
    engrish_sh = REPO_ROOT / "engrish.sh"
    md = ENGRISH_MD.read_text(encoding="utf-8")
    if "engrish.sh" in md:
        assert engrish_sh.exists(), (
            f"engrish.md references engrish.sh in {md.count('engrish.sh')} place(s) "
            f"but {engrish_sh} does not exist."
        )


def test_engrish_sh_md_flags_match_engrish_sh_argparse(md_text: str) -> None:
    """M13-AC6 — every `./engrish.sh <flag>` invocation in engrish.md must
    correspond to a flag handled by engrish.sh's case statement.

    Catches drift where docs claim a flag that engrish.sh doesn't recognize.
    """
    engrish_sh = REPO_ROOT / "engrish.sh"
    if not engrish_sh.exists():
        pytest.skip("engrish.sh missing — caught by separate test")
    sh_text = engrish_sh.read_text(encoding="utf-8")

    # Flags engrish.sh case-statement handles.
    sh_flags: set[str] = set()
    for m in re.finditer(r"^\s*(--\w[\w-]*)\)", sh_text, re.MULTILINE):
        sh_flags.add(m.group(1))

    # Flags engrish.md shows in `./engrish.sh <flag>` invocations.
    md_sh_flags: set[str] = set()
    for line in md_text.splitlines():
        m = re.search(r"\./engrish\.sh\s+(--[\w-]+)", line)
        if m:
            md_sh_flags.add(m.group(1))

    undocumented_in_md = sh_flags - md_sh_flags
    missing_in_sh = md_sh_flags - sh_flags
    msg_parts = []
    if missing_in_sh:
        msg_parts.append(
            f"engrish.md documents engrish.sh flag(s) NOT handled by engrish.sh: {sorted(missing_in_sh)}"
        )
    if undocumented_in_md:
        msg_parts.append(
            f"engrish.sh handles flag(s) NOT documented in engrish.md: {sorted(undocumented_in_md)}"
        )
    assert not msg_parts, "\n".join(msg_parts)


def test_drift_table_diagnostic(
    md_text: str, parser_surface: dict[str, set[str]], parser_global: set[str]
) -> None:
    """Print a full diagnostic table. Always passes — purely informational.

    Run with pytest -s to see output:
        pytest engrish/tests/test_m11_docs_sync.py::test_drift_table_diagnostic -s
    """
    examples = _md_examples(md_text)
    md_subs_to_flags: dict[str, set[str]] = {}
    for sub, flags in examples:
        md_subs_to_flags.setdefault(sub, set()).update(flags)

    print("\n=== engrish.md ↔ engrish.cli surface parity ===")
    print(f"\nGlobal flags: parser={sorted(parser_global)}")

    all_subs = set(parser_surface) | set(md_subs_to_flags)
    print(f"\n{'Subcommand':<20}{'In parser':<12}{'In doc':<10}{'Flag drift'}")
    print("-" * 80)
    for sub in sorted(all_subs):
        in_parser = sub in parser_surface
        in_doc = sub in md_subs_to_flags
        if in_parser and in_doc:
            parser_flags = parser_surface[sub]
            md_flags = md_subs_to_flags[sub]
            only_in_doc = md_flags - parser_flags - parser_global
            only_in_parser = parser_flags - md_flags
            drift_parts = []
            if only_in_doc:
                drift_parts.append(f"doc-only: {sorted(only_in_doc)}")
            if only_in_parser:
                drift_parts.append(f"parser-only: {sorted(only_in_parser)}")
            drift = "; ".join(drift_parts) if drift_parts else "OK"
        elif in_parser:
            drift = f"parser-only ({sorted(parser_surface[sub])})"
        else:
            drift = f"doc-only ({sorted(md_subs_to_flags[sub])})"
        print(f"{sub:<20}{'YES' if in_parser else 'no':<12}{'YES' if in_doc else 'no':<10}{drift}")
