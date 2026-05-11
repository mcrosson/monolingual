# Verification — `non`

Locales: non

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 9,445 | @-set 4,326 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 5,078 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 239 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 4,326 | ✓ | OK |  |
| §5 .df & synonym count | - | 5,351 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=4,326 | .df @ count=4,326 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=5,351 | .df & count=5,351 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 5,351 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
