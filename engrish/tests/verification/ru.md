# Verification — `ru`

Locales: ru

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 375,143 | @-set 57,674 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 317,135 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 308,271 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 57,674 | ✓ | OK |  |
| §5 .df & synonym count | - | 20,374 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=57,674 | .df @ count=57,674 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=20,374 | .df & count=20,374 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 20,374 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
