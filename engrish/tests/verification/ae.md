# Verification — `ae`

Locales: ae

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 336 | @-set 319 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 17 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 7 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 319 | ✓ | OK |  |
| §5 .df & synonym count | - | 14 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=319 | .df @ count=319 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=14 | .df & count=14 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 14 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
