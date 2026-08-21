---
title: Matching canonical models across versions
labels: [wayfinder:grilling]
status: open
assignee:
blocked-by: [001-orbis-identifier-stability.md]
---

## Question

Given the baseline and target canonical models, how do merged roads (and other canonical units) find their counterparts? Decide the matching strategy: identity-first (where identifiers proved stable in [Do Orbis identifiers survive across releases?](001-orbis-identifier-stability.md)) with geometric snapping as fallback, or geometry-first throughout? What defines a match for a merged road whose extent itself changed (a junction added mid-road splits one canonical unit into two — the match is 1:N)? This decision feeds the geometry-tolerance prototype and change-group inference.
