---
title: Python tooling for 6.4 GB Orbis PBF processing
labels: [wayfinder:research]
status: open
assignee:
blocked-by: []
---

## Question

Which Python-centric toolchain handles this pipeline at our scale (6.4 GB PBF per version, two versions loaded per comparison, Windows workstation)? Needs: (1) clipping a region from a PBF (osmium-tool `extract`? pyosmium? availability on Windows), (2) streaming/reading full element data including relations, (3) diffing two PBFs at element level, (4) building an in-memory or on-disk graph for bivalent merging on a clip-sized region. Recommend a concrete stack (libraries + versions), note what only works clip-scale vs full-NLD, and verify the tools actually install and open one of the extracts in `data/orbis/`.

Findings: branch `research/python-pbf-tooling`, file `research/python-pbf-tooling.md`.
