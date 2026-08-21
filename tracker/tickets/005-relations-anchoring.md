---
title: Re-anchoring relations onto the canonical model
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [001-orbis-identifier-stability.md]
---

## Question

Relations (turn restrictions, lane connectivity, routes, admin boundaries) reference ways and nodes that merging dissolves. How is each relation type re-expressed on the canonical model — e.g. a turn restriction as a (merged-road, offset) → (merged-road, offset) movement — so that a relation rebuilt from different member ways but describing the same maneuver compares as unchanged? Depends on what identifier stability [Do Orbis identifiers survive across releases?](001-orbis-identifier-stability.md) found for relations.
