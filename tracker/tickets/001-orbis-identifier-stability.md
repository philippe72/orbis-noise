---
title: Do Orbis identifiers survive across releases?
labels: [wayfinder:research]
status: open
assignee:
blocked-by: []
---

## Question

Are OSM element IDs (way/node/relation) stable between consecutive Orbis releases (e.g. 26330 → 26340), or is ID churn expected? Orbis additionally carries identifier-like tags on elements — which identifier tags exist, on which feature classes, and do they (alone or combined with element IDs) give stable cross-version identity for ways, nodes, and relations? The matching design must know what identity it can trust versus where geometry/attribute matching has to carry the load.

Sources to try: Orbis/TomTom documentation, OSM/Overture ID-scheme docs Orbis derives from, and empirically probing the three extracts in `data/orbis/` (do IDs of a sampled area persist across the three versions? what identifier-ish tags appear?).

Findings: branch `research/orbis-identifier-stability`, file `research/orbis-identifier-stability.md`.
