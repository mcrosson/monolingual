# Verification — `ang+enm+en`

Locales: ang, enm, en

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 1,499,394 | @-set 929,199 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 531,616 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 34,877 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 929,199 | ✓ | OK |  |
| §5 .df & synonym count | - | 556,001 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=929,199 | .df @ count=929,199 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=556,001 | .df & count=556,001 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 556,001 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 929,199 entries | 32,545 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
