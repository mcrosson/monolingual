# Verification — `grc`

Locales: grc

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 55,337 | @-set 20,836 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 34,174 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 17,862 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 20,836 | ✓ | OK |  |
| §5 .df & synonym count | - | 20,152 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=20,836 | .df @ count=20,836 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=20,152 | .df & count=20,152 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 20,152 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
