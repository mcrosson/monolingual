# Verification — `la+fr+es+it`

Locales: la, fr, es, it

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 2,308,676 | @-set 916,230 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 1,363,900 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 555,225 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 916,230 | ✓ | OK |  |
| §5 .df & synonym count | - | 812,698 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=916,230 | .df @ count=916,230 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=812,698 | .df & count=812,698 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 812,698 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 916,230 entries | 45,805 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
