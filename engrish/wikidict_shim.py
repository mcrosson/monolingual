"""Consolidated shim for engrish-gated wikidict behavior.

Upstream ``wikidict`` is byte-identical to merge-base ``433889e3`` when
``engrish_mode()`` is not entered. Entering installs engrish-derived locales
(via a shim-owned registry that lives in ``wikidict.lang._ALL_LOCALES`` but
NOT in ``sys.modules`` — A1 registry-only), seeds
``wikidict.constants.LOCALE_ORIGIN`` and ``wikidict.namespaces.namespaces``
for each engrish code, rebuilds every aggregate dict on ``wikidict.lang`` to
reflect the derived registry, and monkey-patches ``wikidict.parse.process``
to force ``is_monolingual=True`` (B1 kwarg + ``functools.partial`` binding).
Exiting restores every mutation to its pre-enter snapshot.

The shim is re-entrant: nested ``activate()`` / ``engrish_mode()`` calls
counter-reference; only the outermost pair performs install / uninstall work.
``engrish/__init__.py`` calls ``activate()`` at package import for
process-lifetime activation, so every engrish subcommand inherits engrish
mode without per-call-site wrapping. Ad-hoc test code can still use
``with engrish_mode():`` for scoped activation.

See ``task-round-4-execution-plan`` M2 and ``task-round-4-acceptance-criteria``
M2-AC1..M2-AC7 for the acceptance contract.
"""

from __future__ import annotations

import copy
import functools
import json
import types
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

_ENGRISH_JSON = Path(__file__).parent / "engrish.json"

# Aggregate-dict attribute names populated by ``wikidict.lang._populate()``
# at module load. We rebuild each on install and restore each on uninstall.
_LANG_AGGREGATE_ATTRS: tuple[str, ...] = (
    "module_trans",
    "template_trans",
    "appendix_trans",
    "template_adapters",
    "template_overrides",
    "float_separator",
    "thousands_separator",
    "section_patterns",
    "sublist_patterns",
    "section_level",
    "section_sublevels",
    "head_sections",
    "etyl_section",
    "sections",
    "variant_titles",
    "reverse_variant_titles",
    "variant_templates",
    "reverse_variant_templates",
    "variant_handlers",
    "definitions_to_ignore",
    "templates_ignored",
    "find_genders",
    "find_pronunciations",
    "random_word_url",
    "adjust_wikicode",
)

_MISSING = object()  # sentinel for "attr was absent before install"

_entered_count = 0
_saved: dict[str, Any] = {}


def _load_engrish_cfg() -> dict[str, dict[str, str]]:
    if not _ENGRISH_JSON.exists():
        return {}
    return json.loads(_ENGRISH_JSON.read_text(encoding="utf-8")).get("languages", {})


def _rebuild_aggregate(
    all_locales: dict[str, Any],
    attr: str,
    defaults: Any,
) -> dict[str, Any]:
    """Mirror ``wikidict.lang._populate()`` against a specified locale map."""
    return {
        code: getattr(mod, attr) if hasattr(mod, attr) else getattr(defaults, attr)
        for code, mod in all_locales.items()
    }


def _copy_if_mutable(value: Any) -> Any:
    """Shallow-copy dict/list/set so Case B overrides don't alias ``en``'s refs.

    Addresses ``[[risk-lang-init-aliased-dicts]]`` — without copy-on-derive a
    later mutation on a derived locale would silently mutate ``en``.
    """
    if isinstance(value, (dict, list, set)):
        return copy.copy(value)
    return value


def _engrish_worker_init(locale: str) -> None:
    """Multiprocessing worker initializer patched onto ``wikidict.render.init_worker``.

    **Why this function is module-level in the shim:** ``wikidict.render.main``
    spawns a ``multiprocessing.Pool`` with ``initializer=init_worker`` (spawn
    start method). Pickle serializes the initializer by module + qualname.
    When the worker unpickles, Python imports that module — ``engrish.wikidict_shim``
    — which transitively imports ``engrish`` (the parent package), triggering
    ``engrish/__init__.py``'s ``activate()``. By the time the worker actually
    invokes this function, the shim is installed in the child process and
    ``LOCALE_ORIGIN`` / ``_ALL_LOCALES`` / aggregates are populated.

    Without this, the worker process starts fresh with no engrish state, so
    ``guess_locales("<engrish-only-code>")`` returns ``("<code>", "<code>")``
    and ``setup_modules_db`` looks for the dump in ``data/<code>/`` instead
    of ``data/en/`` — producing the "No dump found. Run with --download first"
    spam and 0% progress the user observed 2026-04-23.

    Delegates to the original ``init_worker`` (saved during the child process's
    own ``_install``).
    """
    # Child process has already run `activate()` via engrish package import.
    # `_saved["render_init_worker"]` holds the pristine wikidict.render.init_worker
    # captured at install-time in this child.
    _saved["render_init_worker"](locale)


def _install() -> None:
    """Perform all engrish-gated mutations on wikidict. Outermost-enter only."""
    global _saved
    from wikidict import constants, lang, namespaces, parse, render as wdrender
    from wikidict.lang import defaults

    cfg = _load_engrish_cfg()
    en_module = lang._ALL_LOCALES.get("en")
    if en_module is None:
        # engrish presumes EN is loaded; abort installation rather than crash later.
        raise RuntimeError("engrish_mode requires wikidict.lang.en to be importable")

    _saved = {
        "namespaces": dict(namespaces.namespaces),
        "locale_origin": dict(constants.LOCALE_ORIGIN),
        "all_locales": dict(lang._ALL_LOCALES),
        "lang_aggregates": {a: getattr(lang, a, _MISSING) for a in _LANG_AGGREGATE_ATTRS},
        "case_b_saves": [],  # list of (module_ref, {attr: (had_before, value_before)})
        "parse_process": parse.process,
        "render_init_worker": wdrender.init_worker,
    }

    # --- namespaces: derived engrish locales inherit EN's namespaces. ---
    en_namespaces = namespaces.namespaces.get("en", [])
    for code in cfg:
        if code not in namespaces.namespaces and en_namespaces:
            namespaces.namespaces[code] = list(en_namespaces)

    # --- LOCALE_ORIGIN: every engrish code sources from EN Wiktionary. ---
    # Upstream ships {"fro": "fr"}; engrish mode overwrites fro → "en" because
    # Old French is then extracted from the EN dump, not the FR dump. The
    # saved snapshot restores the {"fro": "fr"} default on exit.
    for code in cfg:
        constants.LOCALE_ORIGIN[code] = "en"

    # --- Locale modules: Case A (en) noop, Case B (existing locale), Case C (derived). ---
    en_attrs = {a for a in dir(en_module) if not a.startswith("_")}
    default_attrs = {a for a in dir(defaults) if not a.startswith("_")}

    for code, cfg_entry in cfg.items():
        if code == "en":
            continue  # Case A: en's parsing rules are already correct.
        if code in lang._ALL_LOCALES:
            _apply_case_b(lang._ALL_LOCALES[code], en_module, en_attrs, default_attrs, cfg_entry)
        else:
            lang._ALL_LOCALES[code] = _build_case_c_module(code, en_module, en_attrs, cfg_entry)

    # --- Rebuild every aggregate dict on wikidict.lang. ---
    for attr in _LANG_AGGREGATE_ATTRS:
        setattr(lang, attr, _rebuild_aggregate(lang._ALL_LOCALES, attr, defaults))

    # --- parse.process monkey-patch: force is_monolingual=True in engrish mode. ---
    parse.process = functools.partial(_saved["parse_process"], force_monolingual=True)

    # --- render.init_worker monkey-patch: propagate shim to spawn workers. ---
    # See _engrish_worker_init docstring for the pickle-trick rationale.
    wdrender.init_worker = _engrish_worker_init


def _apply_case_b(
    existing: Any,
    en_module: Any,
    en_attrs: set[str],
    default_attrs: set[str],
    cfg_entry: dict[str, str],
) -> None:
    """Override a native locale's attrs with EN's, tracking every change for restore.

    Mutable values are copied (``_copy_if_mutable``) to break aliasing with EN.
    Attrs defined in ``defaults`` but not in ``en`` are removed so
    ``_populate`` falls back to ``defaults`` (matches fork's pre-shim behavior).
    """
    saves: dict[str, tuple[bool, Any]] = {}
    for attr in en_attrs:
        saves[attr] = (hasattr(existing, attr), getattr(existing, attr, None))
        setattr(existing, attr, _copy_if_mutable(getattr(en_module, attr)))
    for attr in default_attrs:
        if attr in en_attrs or not hasattr(existing, attr):
            continue
        saves.setdefault(attr, (True, getattr(existing, attr)))
        delattr(existing, attr)
    existing.head_sections = (cfg_entry["wiktionary_section"],)
    _saved["case_b_saves"].append((existing, saves))


def _build_case_c_module(
    code: str,
    en_module: Any,
    en_attrs: set[str],
    cfg_entry: dict[str, str],
) -> types.ModuleType:
    """Create a derived locale module patterned on EN. Not registered in sys.modules."""
    derived = types.ModuleType(f"wikidict.lang.{code}")
    for attr in en_attrs:
        setattr(derived, attr, _copy_if_mutable(getattr(en_module, attr)))
    setattr(derived, "head_sections", (cfg_entry["wiktionary_section"],))
    heading_title = cfg_entry["wiktionary_section"].replace(" ", "_").title()
    setattr(
        derived,
        "random_word_url",
        f"https://en.wiktionary.org/wiki/Special:RandomInCategory/"
        f"{heading_title}_lemmas#{heading_title.replace('_', ' ')}",
    )
    return derived


def _uninstall() -> None:
    """Restore every wikidict mutation from the pre-enter snapshot."""
    global _saved
    from wikidict import constants, lang, namespaces, parse, render as wdrender

    namespaces.namespaces.clear()
    namespaces.namespaces.update(_saved["namespaces"])

    constants.LOCALE_ORIGIN.clear()
    constants.LOCALE_ORIGIN.update(_saved["locale_origin"])

    for existing, saves in _saved["case_b_saves"]:
        for attr, (had_before, value_before) in saves.items():
            if had_before:
                setattr(existing, attr, value_before)
            else:
                with suppress(AttributeError):
                    delattr(existing, attr)

    lang._ALL_LOCALES.clear()
    lang._ALL_LOCALES.update(_saved["all_locales"])

    for attr, value in _saved["lang_aggregates"].items():
        if value is _MISSING:
            with suppress(AttributeError):
                delattr(lang, attr)
        else:
            setattr(lang, attr, value)

    parse.process = _saved["parse_process"]
    wdrender.init_worker = _saved["render_init_worker"]
    _saved = {}


def activate() -> None:
    """Process-lifetime activation. Idempotent counter-increment.

    ``engrish/__init__.py`` calls this on package import so every subcommand
    inherits engrish mode without per-call-site wrapping.
    """
    global _entered_count
    _entered_count += 1
    if _entered_count == 1:
        _install()


def deactivate() -> None:
    """Decrement the activation counter. Uninstalls only at the outermost exit."""
    global _entered_count
    if _entered_count == 0:
        return
    _entered_count -= 1
    if _entered_count == 0:
        _uninstall()


@contextmanager
def engrish_mode():
    """Scoped context manager form of activate/deactivate. Re-entrant."""
    activate()
    try:
        yield
    finally:
        deactivate()


def is_active() -> bool:
    return _entered_count > 0


def verify_upstream_parity(debug: bool = False) -> list[str]:
    """Compare the 7 keep-as-is wikidict files against upstream merge-base ``433889e3``.

    Returns a list of file paths whose current on-disk bytes differ from
    upstream. Empty list = every keep-as-is file is byte-identical.

    Intended as a debug hook only; not called in production. Implements M2-AC7
    per ``[[task-rewrite-wikidict-disposition]]``. ``debug=True`` prints a
    per-file summary to stderr for interactive use.
    """
    import subprocess
    import sys

    keep_as_is = (
        "wikidict/stubs.py",
        "wikidict/convert.py",
        "wikidict/render.py",
        "wikidict/context.py",
        "wikidict/lang/ru/__init__.py",
        "wikidict/lang/ru/variant_handlers.py",
        "wikidict/lang/sv/variant_handlers.py",
    )
    repo_root = Path(__file__).resolve().parents[1]
    diverged: list[str] = []
    for rel_path in keep_as_is:
        proc = subprocess.run(
            ["git", "diff", "--exit-code", "433889e3", "--", rel_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            diverged.append(rel_path)
            if debug:
                print(f"DIVERGED: {rel_path}", file=sys.stderr)
        elif debug:
            print(f"ok: {rel_path}", file=sys.stderr)
    return diverged
