---
title: "Prototype: bivalent merge + linear referencing on the clip"
labels: [wayfinder:prototype]
status: open
assignee:
blocked-by: [002-python-pbf-tooling.md, 003-clip-test-region.md]
---

## Question

Does the merged-road model hold on real Orbis data? Build a throwaway Python prototype (via /prototype) that loads one clip, merges ways across bivalent nodes, and re-expresses tags as linear references on merged roads. Key questions it must answer concretely: what makes a node non-bivalent in practice (tag changes at the node? oneway direction flips? layer/bridge boundaries?), how often merging fires, and whether the linear-referenced attribution round-trips (can reconstruct the original sectioning's information). Human reviews the output on the clip.
