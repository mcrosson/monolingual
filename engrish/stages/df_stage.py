"""Per-locale ``.df`` production stage. Bridge between M4 render JSON and M5 df_writer.

Invoked by ``engrish.stages.generate`` for each locale in a form. Uses
``engrish.pipeline.require_artifact`` to recurse into ``render`` when the
per-locale render JSON is missing (D29).

Also emits a ``broken_variants.txt`` companion artifact next to the ``.df``
listing every variant whose target headword is absent from the render JSON
(M13-AC8 / D38). Per CLAUDE.md verification §2/§3a, broken variant targets
must be visible — this artifact is the per-locale evidence consumed by AC5
data verification.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from engrish.df_writer import write_broken_variants_report, write_df_from_json
from engrish.paths import df_path
from engrish.pipeline import require_artifact
from engrish.stages.render import _existing_json, latest_json, run as render_run

log = logging.getLogger(__name__)


def ensure_locale_df(locale: str) -> Path:
    """Produce (if absent) and return the per-locale ``.df`` path.

    Chain: per-locale .df ← render JSON ← SQLite ← xml.bz2 ← download.

    Side effect: writes ``broken_variants.txt`` next to the ``.df`` (per M13-AC8).
    """
    target = df_path(locale)
    broken_report = target.parent / "broken_variants.txt"

    def _produce() -> None:
        # Ensure render JSON first.
        if _existing_json(locale) is None:
            render_run(locale)
        json_path = latest_json(locale)
        log.info("[%s] df_stage: writing .df from %s → %s", locale, json_path, target)
        write_df_from_json(json_path, target, locale)
        # Emit broken-variant report alongside (M13-AC8 / D38).
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        n_broken = write_broken_variants_report(json_data, broken_report)
        log.info(
            "[%s] df_stage: broken_variants.txt → %d missing-target groups (see %s)",
            locale, n_broken, broken_report,
        )

    require_artifact(target, _produce)
    return target
