# Verification — `fa+peo+pal`

Locales: fa, peo, pal

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 17,233 | @-set 15,595 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 1,638 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 105 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 15,595 | ✓ | OK |  |
| §5 .df & synonym count | - | 1,628 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=15,595 | .df @ count=15,595 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=1,628 | .df & count=1,628 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 1,628 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 15,595 entries | 200 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
