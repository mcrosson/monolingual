"""M2-AC regression tests for the wikidict shim.

Covers ACs 1 (idempotent + no residue), 3 (Case B no alias + Case C coverage),
4 (parse.process monkey-patch shape), 5 (LOCALE_ORIGIN toggling), 6 (namespaces
toggling), 7 (verify_upstream_parity helper). AC2 (shim-disengaged parity green)
is covered by ``test_harness_parity.py`` plus manual subprocess verification;
an in-process test here would accumulate submodule-import bloat that is
indistinguishable from real divergence.

Per-test fixture:
- Every test begins by assuming the shim is NOT active (tests_new/ does not
  import engrish at collection time).
- Tests that activate must also deactivate, or the next test sees engaged state.
- The ``shim`` fixture enforces deactivation in teardown.
"""

from __future__ import annotations

import functools
from typing import Any

import pytest

from engrish.wikidict_shim import (
    activate,
    deactivate,
    engrish_mode,
    is_active,
    verify_upstream_parity,
)


@pytest.fixture(autouse=True)
def _ensure_deactivated() -> Any:
    # If a prior test left the shim active, force-deactivate before this one.
    while is_active():
        deactivate()
    yield
    while is_active():
        deactivate()


def _snapshot_surfaces() -> dict[str, Any]:
    import wikidict.constants as c
    import wikidict.lang as L
    import wikidict.namespaces as ns

    return {
        "LOCALE_ORIGIN": dict(c.LOCALE_ORIGIN),
        "namespaces": {k: list(v) for k, v in ns.namespaces.items()},
        "_ALL_LOCALES_keys": sorted(L._ALL_LOCALES.keys()),
        "head_sections": dict(L.head_sections),
        "definitions_to_ignore": dict(L.definitions_to_ignore),
        "templates_ignored": dict(L.templates_ignored),
    }


def test_activate_sets_is_active_true_and_installs() -> None:
    assert not is_active()
    activate()
    try:
        assert is_active()
        import wikidict.constants as c

        assert c.LOCALE_ORIGIN.get("fro") == "en"
        assert c.LOCALE_ORIGIN.get("ang") == "en"
    finally:
        deactivate()


def test_deactivate_idempotent_when_inactive() -> None:
    assert not is_active()
    deactivate()
    assert not is_active()


def test_nested_activate_idempotent() -> None:
    activate()
    activate()
    activate()
    try:
        assert is_active()
        deactivate()
        assert is_active()
        deactivate()
        assert is_active()
    finally:
        deactivate()
    assert not is_active()


def test_deactivate_restores_every_tracked_surface() -> None:
    before = _snapshot_surfaces()
    with engrish_mode():
        assert is_active()
    after = _snapshot_surfaces()
    assert after == before, "shim deactivate failed to restore some surface"


def test_case_b_override_does_not_alias_en() -> None:
    """Derived (engrish-overridden) locale's mutable attrs must not alias en's."""
    with engrish_mode():
        import wikidict.lang as L

        it_adapters = L._ALL_LOCALES["it"].template_adapters
        en_adapters = L._ALL_LOCALES["en"].template_adapters
        assert it_adapters is not en_adapters
        # Mutate the derived locale's dict; en must NOT pick it up.
        sentinel_key = "__alias_test_sentinel__"
        it_adapters[sentinel_key] = lambda body: body
        assert sentinel_key not in en_adapters
        del it_adapters[sentinel_key]


def test_case_c_derived_modules_cover_every_engrish_only_code() -> None:
    """Every engrish.json code must be present in _ALL_LOCALES under engrish_mode."""
    import json
    from pathlib import Path

    cfg = json.loads(
        (Path(__file__).resolve().parent.parent / "engrish" / "engrish.json").read_text()
    )["languages"]
    with engrish_mode():
        import wikidict.lang as L

        missing = [code for code in cfg if code not in L._ALL_LOCALES]
        assert not missing, f"engrish codes missing from _ALL_LOCALES: {missing}"


def test_case_c_derived_module_not_in_sys_modules() -> None:
    """A1 registry-only: derived modules live in _ALL_LOCALES, not sys.modules."""
    import sys

    with engrish_mode():
        assert "wikidict.lang.ang" not in sys.modules
        assert "wikidict.lang.pi" not in sys.modules
        # Sanity: en (upstream-native) IS in sys.modules
        assert "wikidict.lang.en" in sys.modules


def test_parse_process_default_signature_shim_disengaged() -> None:
    """Shim-disengaged: parse.process exposes force_monolingual kwarg, default False."""
    import inspect

    import wikidict.parse as parse

    sig = inspect.signature(parse.process)
    assert "force_monolingual" in sig.parameters
    param = sig.parameters["force_monolingual"]
    assert param.default is False
    assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_parse_process_is_partial_shim_engaged() -> None:
    """Shim-engaged: parse.process is a partial binding force_monolingual=True."""
    with engrish_mode():
        import wikidict.parse as parse

        assert isinstance(parse.process, functools.partial)
        assert parse.process.keywords == {"force_monolingual": True}


def test_locale_origin_fro_toggles_between_fr_and_en() -> None:
    import wikidict.constants as c

    assert c.LOCALE_ORIGIN.get("fro") == "fr"
    with engrish_mode():
        assert c.LOCALE_ORIGIN.get("fro") == "en"
    assert c.LOCALE_ORIGIN.get("fro") == "fr"


def test_namespaces_ang_toggles_absent_present_absent() -> None:
    import wikidict.namespaces as ns

    assert "ang" not in ns.namespaces
    with engrish_mode():
        assert "ang" in ns.namespaces
        # ang inherits EN's namespaces.
        assert ns.namespaces["ang"] == ns.namespaces["en"]
        # But is not the SAME list object (copy-on-derive).
        assert ns.namespaces["ang"] is not ns.namespaces["en"]
    assert "ang" not in ns.namespaces


def test_verify_upstream_parity_reports_expected_divergences() -> None:
    """M2-AC7: debug helper reports the 7 keep-as-is files' divergence status.

    The helper is a comparison tool, not a zero-diff gate — every keep-as-is
    file is intentionally divergent from upstream per D10 (e.g. ``stubs.py``
    swaps list→set for ``Variants``, ``convert.py`` removes the deepcopy call
    site). The test confirms the helper runs and flags the known-divergent set;
    if any keep-as-is file is ever reverted to byte-identical, this test needs
    updating along with the disposition record.
    """
    diverged = verify_upstream_parity(debug=False)
    # Every keep-as-is file carries at least one fork-intentional change.
    expected_divergent = {
        "wikidict/stubs.py",
        "wikidict/convert.py",
        "wikidict/render.py",
        "wikidict/context.py",
        "wikidict/lang/ru/__init__.py",
        "wikidict/lang/ru/variant_handlers.py",
        "wikidict/lang/sv/variant_handlers.py",
    }
    assert set(diverged) == expected_divergent, (
        f"unexpected keep-as-is divergence set (expected {expected_divergent}, got {set(diverged)})"
    )


def test_aggregate_definitions_to_ignore_italian_restored_after_engage() -> None:
    """M1b-AC1 guard survives M2: post-deactivate, it falls back to upstream default."""
    import wikidict.lang as L

    assert L.definitions_to_ignore.get("it") == ()
    with engrish_mode():
        # Engaged: it's attrs overwritten from en; en has non-empty default.
        assert L.definitions_to_ignore.get("it") != ()
    assert L.definitions_to_ignore.get("it") == ()
