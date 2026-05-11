# Verification — `en+fr+de+es`

Locales: en, fr, de, es

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 2,827,630 | @-set 1,756,716 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 987,238 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 13,199 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 1,756,716 | ✓ | OK |  |
| §5 .df & synonym count | - | 1,044,975 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=1,756,716 | .df @ count=1,756,716 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=1,044,975 | .df & count=1,044,975 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 1,044,975 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 1,756,716 entries | 65,084 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
