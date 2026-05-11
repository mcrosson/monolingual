# Verification — `peo`

Locales: peo

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 294 | @-set 282 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 12 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 5 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 282 | ✓ | OK |  |
| §5 .df & synonym count | - | 12 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=282 | .df @ count=282 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=12 | .df & count=12 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 12 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
