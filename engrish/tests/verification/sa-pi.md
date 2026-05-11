# Verification — `sa+pi`

Locales: sa, pi

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 18,778 | @-set 15,241 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 2,905 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 828 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 15,241 | ✓ | OK |  |
| §5 .df & synonym count | - | 2,905 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=15,241 | .df @ count=15,241 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=2,905 | .df & count=2,905 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 2,905 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 15,241 entries | 632 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
