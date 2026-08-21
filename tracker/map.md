---
title: Route to the Orbis change-noise prototype
labels: [wayfinder:map]
status: open
---

## Destination

A working prototype, run on the real Orbis NLD extracts (clipped region first), that diffs two map versions, reconstructs both into canonical models, and produces a two-layer ledger — element changes grouped into change groups, each group classified noise / real / mixed — inspectable in an interactive map viewer with aggregate stats on top. Done when a sample inspection convinces the human the noise verdicts are right and the real-change list is reviewably small.

## Notes

- Domain: Orbis (`.osm.pbf`) map releases; vocabulary lives in [CONTEXT.md](../CONTEXT.md) — consult it every session. Sharpen it via /domain-modeling; default ticket skills: /grilling + /domain-modeling.
- Stack: Python, running locally on this Windows workstation. Iterate on a clipped region before full NLD.
- Full feature scope (roads, POIs, buildings, land use, admin areas) and full attribution scope (tags and relations).
- Baseline map is treated as ground truth; classification is operational — noise iff the change vanishes in the canonical-model diff. A tag value correction is a real change.
- Inputs are always Orbis `.osm.pbf` extracts (other countries may arrive later in the same format).
- Tracker: local markdown. Tickets live in `tracker/tickets/`, frontmatter `status`/`assignee`/`blocked-by` (ticket filenames). Frontier = open, unassigned, all blockers closed.

## Decisions so far

<!-- one line per closed ticket: gist + link -->

## Not yet specified

- **Change-group inference** — the algorithm that partitions element changes into change groups and ties them to canonical-diff entries. Can't be phrased sharply until matching and merging exist on real data.
- **The viewer** — interaction model, rendering stack, how a change group is presented. Waits until the ledger's real shape exists to react to.
- **Headline stats** — which aggregate numbers summarize a version pair. Waits on first real noise ratios.
- **Scaling to full NLD** — memory/runtime strategy for 6.4 GB × 2. Waits on what the clip-scale prototype actually costs.
- **Orbis tag semantics per feature class** — which tags are attribution vs housekeeping/metadata (a metadata-only tag change may itself be noise). Likely graduates out of the non-road canonicalization and tooling tickets.
- **Created/deleted real-world features** — how genuinely new or removed features (not churn) are told apart from unmatched-by-accident, and how they appear in the ledger.

## Out of scope

- Productization: packaging, deployment, non-local runtimes. The destination is a prototype.
- Input-format generality — anything other than Orbis `.osm.pbf` extracts.
- Machine-readable diff API for downstream systems (Q2 option c); human viewer + stats only.
