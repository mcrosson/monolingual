# Verification — `ja`

Locales: ja

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 82,610 | @-set 82,380 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 220 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 1,900 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 82,380 | ✓ | OK |  |
| §5 .df & synonym count | - | 197 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=82,380 | .df @ count=82,380 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=197 | .df & count=197 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 197 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
