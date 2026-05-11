# Verification — `el`

Locales: el

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 79,628 | @-set 30,605 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 48,668 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 1,076 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 30,605 | ✓ | OK |  |
| §5 .df & synonym count | - | 49,884 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=30,605 | .df @ count=30,605 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=49,884 | .df & count=49,884 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 49,884 & lines | 0 broken | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
