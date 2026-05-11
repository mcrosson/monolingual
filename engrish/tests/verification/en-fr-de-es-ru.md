# Verification — `en+fr+de+es+ru`

Locales: en, fr, de, es, ru

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 3,202,773 | @-set 1,814,377 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 1,304,373 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 321,470 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 1,814,377 | ✓ | OK |  |
| §5 .df & synonym count | - | 1,027,230 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=1,814,377 | .df @ count=1,814,377 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=1,027,230 | .df & count=1,027,230 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 1,027,230 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 1,814,377 entries | 98,656 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
