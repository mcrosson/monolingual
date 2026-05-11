# Verification — `la+grc+he+arc+syc+cop`

Locales: la, grc, he, arc, syc, cop

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 729,517 | @-set 85,361 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 642,673 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 569,327 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 85,361 | ✓ | OK |  |
| §5 .df & synonym count | - | 85,853 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=85,361 | .df @ count=85,361 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=85,853 | .df & count=85,853 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 85,853 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 85,361 entries | 1,482 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
