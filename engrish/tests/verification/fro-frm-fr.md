# Verification — `fro+frm+fr`

Locales: fro, frm, fr

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 373,951 | @-set 100,682 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 267,810 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 2,468 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 100,682 | ✓ | OK |  |
| §5 .df & synonym count | - | 269,898 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=100,682 | .df @ count=100,682 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=269,898 | .df & count=269,898 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 269,898 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 100,682 entries | 6,781 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
