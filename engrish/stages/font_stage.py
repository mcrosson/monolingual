"""``engrish font`` — produce 4 TTFs + coverage_gaps.txt for a form.

D29 chain: font ← merged .df ← per-locale .df ← render JSON ← parse ← prepare.
The font output produces ``data/engrish/<form>/fonts/{Regular,Bold,Italic,BoldItalic}.ttf``
plus the ``coverage_gaps.txt`` build artifact.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engrish.config import FONTS_DIR
from engrish.font import build_form_fonts
from engrish.paths import dict_base_name, engrish_form_dir, get_snapshot_date
from engrish.pipeline import require_artifact

log = logging.getLogger(__name__)


def _parse_form(form: str) -> list[str]:
    codes = [c.strip() for c in form.split("+") if c.strip()]
    if not codes:
        raise ValueError(f"empty form string: {form!r}")
    return codes


def run(form: str) -> int:
    """Entry: produce the four TTFs + coverage_gaps.txt for ``form``."""
    locales = _parse_form(form)

    # Ensure merged .df exists (recurses through generate → merge → df_stage → render).
    from engrish.stages.generate import run as generate_run

    try:
        date = get_snapshot_date(locales)
    except RuntimeError:
        log.info("[%s] font: no render JSON yet; recursing into generate", form)
        generate_run(form)
        date = get_snapshot_date(locales)

    form_dir = engrish_form_dir(form)
    merged_df = form_dir / f"{dict_base_name(form, date)}.df"
    require_artifact(merged_df, lambda: generate_run(form))

    out_dir = form_dir / "fonts"
    log_path = out_dir / "build.log"

    log.info("[%s] font: building 4 TTFs at %s", form, out_dir)
    outputs = build_form_fonts(
        form=form,
        locales=locales,
        df_path=merged_df,
        out_dir=out_dir,
        fonts_dir=FONTS_DIR,
        log_path=log_path,
    )

    log.info("[%s] font: produced %d files; coverage_gaps.txt at %s",
             form, len(outputs), out_dir / "coverage_gaps.txt")
    return 0
