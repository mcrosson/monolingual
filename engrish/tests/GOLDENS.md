# Goldens — M9 byte-determinism regression fixtures

This directory holds full pipeline output for every locale in the
[D35](../wikis/monolingual/wiki/pages/decision-log.md) corpus. Tests under
`test_m9_goldens.py` re-run the engrish pipeline and assert byte-equality
against these fixtures.

## Layout

```
engrish/tests/goldens/
├── ang/   Old English
├── egy/   Egyptian
├── got/   Gothic
├── grc/   Ancient Greek
├── it/    Italian
├── pi/    Pali
└── ru/    Russian
```

Each locale dir mirrors `data/engrish/<locale>/` exactly:

- `<locale>-en-<date>.df` — merged dictionary file
- `<locale>-en-<date>.ifo / .idx / .dict / .dict.dz` — StarDict outputs
- `<locale>-en-<date>.idx.oft / .syn / .syn.oft` — KOreader/sdcv extras
- `<locale>-en-<date>.epub` — EPUB sampler
- `fonts/{Regular,Bold,Italic,BoldItalic}.ttf` — generated fonts
- `fonts/build.log` — coverage warnings emitted during font build
- `fonts/coverage_gaps.txt` — same content, build-artifact form

All artifacts are byte-deterministic (M9-AC1).

## Running the regression

```bash
ENGRISH_RUN_GOLDENS=1 ./venv/bin/python -m pytest engrish/tests/test_m9_goldens.py -v
```

The opt-in gate exists because the test re-runs the pipeline for each of
the 7 locales. Measured wall-clock for a warm-cache full audit:

- **8 tests passed in ~2m25s** (7 parametrized locales + 1 sentinel) on
  the reference workstation (2026-04-26).

Per-locale breakdown is dominated by `generate` (PyGlossary StarDict
assembly) and `font` (subset + instantiate + pairwise-merge across
multi-script Noto sources for `pi`, `ru`, `grc`). `language-stats` /
`render` no-op via D29 because the per-locale render JSON exists. Cold
runs would add the per-locale render time (which is the hour-scale step
the user primes separately, not a normal-test concern).

Default `pytest engrish/tests/` invocations skip the cost-gated test (the
sentinel `test_d35_corpus_locales_have_goldens` still runs and catches
missing fixtures).

## Refresh workflow

Goldens should only be regenerated when an output change is **intentional**
and reviewed. Steps:

1. **Identify the cause.** What pipeline change (code or upstream data)
   produced the divergence? Document it before touching the goldens.
2. **Re-run the determinism regression** (`ENGRISH_RUN_GOLDENS=1 pytest
   engrish/tests/test_m9_goldens.py`) on the locked corpus. Every per-locale
   diff must remain empty against the existing goldens (otherwise you have a
   *new* M9-AC1 violation in addition to whatever you intended to change).
   Refresh the goldens only after this regression passes against the
   _intended_ output.
3. **Repopulate goldens** from `data/engrish/<locale>/`:

   ```bash
   for loc in got egy pi ang it ru grc; do
       rm -rf engrish/tests/goldens/$loc
       mkdir -p engrish/tests/goldens/$loc
       cp -r data/engrish/$loc/. engrish/tests/goldens/$loc/
   done
   ```

4. **Run the goldens regression** to confirm parity:

   ```bash
   ENGRISH_RUN_GOLDENS=1 ./venv/bin/python -m pytest engrish/tests/test_m9_goldens.py -v
   ```

5. **Commit with an explicit rationale.** The PR description must spell out:
   - What changed in the pipeline.
   - Why the new bytes are correct (cite spec / decision-log entry / upstream
     data drift / etc.).
   - Per-locale size delta if material.

   Reviewers should reject golden updates lacking either the rationale or
   the determinism audit confirmation.

## When goldens are *NOT* the right tool

- **Validating new locales.** Add the locale to `D35` first (decision-log
  entry), then prime → audit → land golden in the same PR.
- **Catching upstream Wiktionary data drift.** Goldens lock against a
  specific snapshot date. A new snapshot legitimately changes outputs;
  refresh per the workflow above.
- **Format-level conformance.** sdcv / epubcheck / fontTools cmap-diff
  cover that — see M6/M7/M8 acceptance criteria.

## See also

- [decision-log D35](../wikis/monolingual/wiki/pages/decision-log.md) —
  M9 corpus lock + per-locale rationale
- [task-round-4-acceptance-criteria M9](../wikis/monolingual/wiki/pages/task-round-4-acceptance-criteria.md) —
  per-AC contracts and current evidence
- [`test_m9_goldens.py`](test_m9_goldens.py) — cost-gated determinism regression
  (replaces the M9-era `prime-2.sh` audit script removed during M12 wiki/scope
  cleanup).
