# Verification — `got+non+ang`

Locales: got, non, ang

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 78,708 | @-set 29,016 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 49,413 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 25,332 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 29,016 | ✓ | OK |  |
| §5 .df & synonym count | - | 25,364 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=29,016 | .df @ count=29,016 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=25,364 | .df & count=25,364 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 25,364 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 29,016 entries | 889 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
