---
title: Clip a test region from all three extracts
labels: [wayfinder:task]
status: open
assignee:
blocked-by: [002-python-pbf-tooling.md]
---

## Question

Produce small clipped extracts (one city or town — pick somewhere with visible churn between versions) from each of the three Orbis NLD releases in `data/orbis/`, using the toolchain chosen in [Python tooling for 6.4 GB Orbis PBF processing](002-python-pbf-tooling.md). Output: three clip PBFs under `data/clips/` plus a note recording the bounding box, the command used, and rough element counts. These clips are the iteration substrate for every prototype ticket.
