# Concept-hub digest discipline (decomposing an oversized hub)

> Moved out of the wiki schema (`wiki/AGENTS.md`) in s134. Read this only when a Mode 7 audit (trigger 5: top concept hub ≥ 25K words) or the user calls for a hub decomposition. The create-vs-edit rule and all other writing rules in `wiki/AGENTS.md` still apply.

A concept hub that accumulates one `### From <source>` evidence block per
ingest will eventually cross the **~25K-word Read-limit proxy** (the 25K-token
cap on the Read tool). At that point, routine ingests on adjacent topics can
no longer Read it whole. When a hub approaches that size, decompose it by
**EXTRACTING evidence clusters, not COMPRESSING prose:**

1. **Extract, don't summarize-in-place.** The accumulated mass is usually
   load-bearing distinct synthesis (each block contributes a genuinely
   different framing; blocks cross-reference and disambiguate each other), not
   redundancy. A naive per-author consolidation pass replays the pull-quote
   information-loss failure the Mode B preservation rule exists to prevent.
2. **Shed each cluster to the page that should own it**: a spawned sub-concept
   page (e.g. a hub's component-taxonomy reconciliation → its own concept), an
   existing concept/comparison that already covers it, or the `pages/sources/`
   page that owns the full source summary. **Grep before creating** (the
   create-vs-edit rule): a cluster's natural home often already exists.
3. **Leave a synthesis-delta digest + `[[pointer]]` where the cluster was.**
   The bulk relocates; what stays on the hub is a one-paragraph digest of *why
   the cluster mattered to this concept* plus the pointer. **Every claim must
   live on a destination page before it leaves the hub.** If a claim has no
   clean home, leave it on the hub; never drop it (Karhade rule).
4. **Reduce the hub to its conceptual spine** (definition, the core framing,
   the load-bearing empirical anchors, and the digests) so it reads as the
   map, with the territory on the spawned/destination pages.

The pattern is repeatable. When one hub crosses the proxy, its trailing
siblings usually will too (size accretes on the same one-block-per-ingest
dynamic), so treat the decomposition as a reusable move (extract → digest →
pointer), not a one-off rescue of a single page.
