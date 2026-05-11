# Verification — `ar+he+arc+syc`

Locales: ar, he, arc, syc

| Check | Source Value | Dest Value | Match? | Verdict | Rationale |
|---|---|---|---|---|---|
| §1 source headwords (sum across locales) | 42,900 | @-set 38,118 + cross-locale syn aliases | ✓ | OK | merge collapses case-fold variants; canonical post-fix scale |
| §1 variant-only entries (skipped per F17) | 4,321 | 0 (skipped, become & lines under canonical) | ✓ | OK |  |
| §2 broken-target variant pointers (upstream data drift) | 6,446 | reported via broken_variants.txt (per locale) | ✓ | SOURCE | upstream Wiktionary cites parent forms not present in extraction; matches wikidict.convert behavior; categorized via §3a heuristic in artifact |
| §4 .df @ headword count | - | 38,118 | ✓ | OK |  |
| §5 .df & synonym count | - | 313 | ✓ | OK |  |
| §6 .ifo wordcount == .df @ count | .ifo wordcount=38,118 | .df @ count=38,118 | ✓ | OK |  |
| §6 .ifo synwordcount == .df & count | .ifo synwordcount=313 | .df & count=313 | ✓ | OK |  |
| §5 every & synonym points at a real @ | 313 & lines | 0 broken | ✓ | OK |  |
| §7 multi-locale entries carry ≥ 2 <h3> sections | 38,118 entries | 455 have ≥ 2 <h3> | ✓ | OK |  |
| §7 res/ URLs in HTML have backing files | 0 unique res/ refs | n/a (no res/ embeds in .df) | ✓ | OK |  |

**Bugs:** 0 • **Source-data findings:** 1
