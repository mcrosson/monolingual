"""Engrish pipeline stages (M4+).

Each stage module exposes ``run(locale)`` — the entry invoked by the
``engrish`` CLI — and a ``require_*`` helper that other stages call through
``engrish.pipeline.require_artifact`` for D29 recursion. Presence-on-disk is
the only validity signal; no staleness check.

M4: ``prepare``, ``render``, ``language_stats``.
M5-M8: later.
"""
