# Verification — `fa`

Locales: fa

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 16,696 | @-set 15,072 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 1,624 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 76 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 15,072 | ✓ | OK |  |
| §5 .df & synonym count | - | 1,822 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=15,072 | .df @ count=15,072 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=1,822 | .df & count=1,822 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 1,822 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
