# Engrish — Dictionary Generation Project

## What this is
StarDict dictionary generator that extracts definitions from EN Wiktionary. Code lives in `engrish/`, `engrish.py`, `engrish.md`, `data/engrish/`. Non-engrish code is out of scope.

## How to work with me

### Rules
- **Listen first.** Read what I say carefully before acting. If I give instructions, follow them — don't substitute your own plan.
- **No fabricated results.** Never claim tests passed, builds succeeded, or data matches without proof you can cite. If you don't know, say so.
- **No busywork.** Don't re-run long test suites or full pipelines for small changes. The full test suite takes 10+ minutes. Only run it when I ask.
- **Verify small, verify targeted.** For a change that only affects one locale or one function, verify with a targeted check — import test, dry-run, unit logic, spot-check the affected data. Not a full suite run.
- **No sycophancy.** Don't apologize excessively or pad responses. Be direct. If you screwed up, say what happened in one sentence and move on.
- **No unsolicited improvements.** Don't refactor, add comments, or "improve" code I didn't ask about.
- **Broken counts are not acceptable.** When verification shows nonzero broken/dangling/dead counts, that is a problem to diagnose — not a footnote to dismiss. Determine whether each issue is a pipeline bug or a source data limitation, and say which.

### Sessions are iterative
- I work through multiple tasks per session.
- I need concise verification of each change as we go.
- Full test suite / full pipeline runs happen at the END of a session, or when I explicitly ask. Not before.

### When verifying code changes
1. Import check (does it parse?)
2. Targeted logic check (does the specific change work on relevant data?)
3. If tests exist for the specific area changed, run just those tests.
4. Do NOT run the full suite unless I say to.

## Project quick reference
- Entry point: `engrish.py` → `engrish/__main__.py`
- Config: `engrish/engrish.json` (languages, fonts, epub settings)
- Pipeline: download → parse → render JSON → normalize variants → convert .df → StarDict
- Key modules: `pipeline.py` (orchestration + normalization), `generate.py` (StarDict output), `merge.py` (multi-locale), `stardict.py` (low-level), `epub.py` (samplers)
- Tests: `./venv/bin/python -m pytest tests/test_engrish.py -x -vvv -s` (LONG — 10+ min)
- Source data: `data/<locale>/en/` (render JSON + output .df/.zip)
- Output data: `data/engrish/<form>/` (StarDict dirs + EPUBs)

## Data verification procedure

When asked to verify output data, perform ALL of the following steps for the specified locale(s). No shortcuts, no sampling, no summaries in place of actual values. Every claim must cite a file path and the key, line, or offset it came from. Never say "looks correct" or "appears to match" — show the number or say you can't.

### 1. Source inventory
- Read the render JSON (`data/<locale>/en/data-*.json`).
- Report: total headword count, count with definitions, count that are variant-only (have `variants` but no `definitions`).
- List any headwords that have BOTH definitions AND variants.

### 2. Variant target integrity
- For every entry that has variants (whether or not it also has definitions), confirm each variant target exists as a headword in the same JSON.
- Report: how many variant targets are valid, how many are broken (target doesn't exist).
- List ALL broken targets — not a sample, all of them.

### 3. Variant chain verification
- Find all variant-only entries whose target is ALSO variant-only (a chain).
- Follow each chain to its terminal entry. Report whether the terminal has definitions.
- List ALL chains that terminate without definitions (dead chains).
- List ALL chains that form cycles.

### 3a. Normalization assessment
- Categorize ALL broken variant targets from Step 2 by failure type:
  - Unicode normalization failures (fullwidth, combining marks, NFC/NFD)
  - Case mismatches
  - Anchor/fragment references (target contains # or //)
  - Missing headwords (target simply doesn't exist in source data)
  - Script/romanization mismatches
  - Locale-specific normalization failures (e.g. Persian kaf/yeh, Arabic tashkeel)
- For each category, report count and examples.
- For categories that normalize_variant_targets SHOULD handle, flag as pipeline bugs.
- For categories that are upstream Wiktionary data issues, flag as source data issues.

### 4. DF file cross-check
- Read the `.df` file(s) in `data/<locale>/en/output/`.
- Report: total `@` headword count, total `&` synonym count.
- Compare headword count to JSON headword count. Report the delta and list ALL missing/extra entries.

### 5. Synonym integrity in DF
- For every `&` synonym line, confirm the synonym target exists as an `@` headword in the same `.df` file.
- Report: valid count, broken count.
- List ALL broken synonym targets.

### 6. StarDict output check (if zip/dict files exist)
- Read the `.ifo` file. Report `bookname`, `wordcount`, `synwordcount`, `sametypesequence`.
- Compare `.ifo` `wordcount` to `.df` `@` headword count. Report match or delta.
- Compare `.ifo` `synwordcount` to `.df` `&` synonym count. Report match or delta.

### 7. Merged form check (multi-locale forms only)
- For each locale in the merge, count headwords in the per-locale `.df`.
- Count headwords in the merged `.df`. Verify it equals the **union** (not sum) of per-locale headwords.
- For headwords present in multiple locales, confirm the merged HTML contains `<h3>` section headers for each contributing locale.
- For headwords present in only one locale, confirm NO `<h3>` header exists.
- Check that all `res/` URLs in merged HTML have locale prefixes.

### 8. Final summary table and assessment

Format:

| Check | Source Value | Dest Value | Match? | Verdict |

Rows: headword counts, variant integrity, synonym integrity, wordcount, synwordcount.

Verdict column must be one of:
- OK — values match and are correct
- BUG — mismatch caused by pipeline code that should be fixed
- SOURCE — mismatch caused by upstream data the pipeline can't control
- REGRESSION — previously working, now broken

After the table, state:
- Total pipeline bugs found (count)
- Total source data issues found (count)
- Whether normalize_variant_targets is functioning correctly (yes/no, with evidence)

Do NOT conclude "pipeline is sound" or equivalent if any BUG verdicts exist.

If a file doesn't exist or a step doesn't apply, say so explicitly — never silently skip it.
