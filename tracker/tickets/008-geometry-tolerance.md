---
title: "Prototype: geometry noise tolerance"
labels: [wayfinder:prototype]
status: open
assignee:
blocked-by: [006-cross-version-matching.md, 007-merge-lr-prototype.md]
---

## Question

Where is the line between a rounding shift and a real geometry change? Starting hypothesis: a fixed lateral-deviation tolerance (~1 m) between matched merged-road geometries. Build a prototype that computes geometry deviations for all matched pairs across a real version pair on the clip, plots the distribution, and lets the human eyeball candidate thresholds against actual examples. Output: a chosen (configurable) tolerance and the deviation metric (pointwise lateral? Hausdorff? area-between-curves per length) that best separates the two populations.
