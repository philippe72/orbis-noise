# Context

Glossary of the ubiquitous language for this project. Terms are canonical: use them exactly as defined here.

## Glossary

### Map version
One released Orbis extract (`.osm.pbf`), e.g. `orbis_nexventura_26340_000_global_nld`. Comparisons are always between two map versions of the same region.

### Baseline map
The older of the two compared map versions. Treated as ground truth: reality is unobservable, so the baseline's canonical model stands in for reality, and all changes are measured from it.

### Target map
The newer of the two compared map versions. Its changes relative to the baseline are what get classified as noise or real.

### Way
A map element with geometry (sequence of nodes) and tags, as stored in the source map. A way is a modeling artifact — one real-world road may be stored as many ways.

### Sectioning
Splitting a way into multiple ways so that a tag can apply to only part of it. Sectioning changes the model, not reality.

### Bivalent node
A node where exactly two ways meet — a continuation, not a choice. A traveler passing a bivalent node has no decision to make.

### Merged road
The result of joining ways across bivalent nodes until every endpoint is a real choice point (junction, dead end). The unit of the canonical model for the road network.

### Linear reference
An offset-based anchoring of attribution onto a merged road ("tag X applies from offset a to offset b"), replacing sectioning as the carrier of partial attribution.

### Canonical model
The reconstruction of a map version into a representation of the reality it models — merged roads with linearly referenced attribution, plus canonical forms for non-road features. Two map versions are compared by diffing their canonical models, not their raw elements.

### Noise
A change between two map versions that disappears in the canonical model: the source elements changed, but the reality they model did not. Examples: sectioning, ID churn, sub-threshold geometry shifts from rounding.

### Real change
A change between two map versions that survives into the canonical model diff: the modeled reality itself differs.

### Canonical feature
The unit of the canonical model for any feature class: one real-world thing with an identity, a geometry, and attribution. Feature classes share this shape and differ only in parameters — geometry type, identity key, tolerances. The merged road is the canonical feature of the road network.

### Attribution
The tags of a feature that describe the modeled reality, excluding identity tags (identifiers), housekeeping, and metadata. Only attribution participates in canonical-model comparison; which tags count as attribution is decided per class. Excluding a tag is verdict-setting — every change to an excluded tag is noise by construction — so exclusion is always an explicit per-tag decision, and an unclassified tag counts as attribution (fail-visible) until ruled out.

### Label/outline twin
One real-world thing stored as two elements — a label node carrying the attribution and an outline area carrying the geometry — linked by an `is_same` relation and a shared identifier. Canonically a single feature: the outline's geometry with the label's attribution.

### Chunked tag
A logical tag value split across `key#N#` tags at byte boundaries. The canonical model joins chunks in order into one value, so re-chunking between map versions is noise; edits inside the joined value remain visible.

### Way-relative tag
A tag whose value embeds its own along-the-way offset referencing, measured from the way's start in the way's direction (`gradient:linear`, `curvature:linear`). Sectioning rewrites these values without any real-world change, so the canonical model must re-base them into the merged road's linear reference space (including direction normalization); placing the opaque value in a linear-reference interval is not enough. House-number ranges are not way-relative: their from/to blocks carry real spatial placement, so they stay adjacent linear-referenced blocks (only direction-normalized), and splitting a range is a real change.

### Canonical list value
A joined multi-valued tag treated as an unordered set of entries, so re-ordering is noise. Tags whose entry order is meaningful are exempted via a per-tag override (none known yet).

### Dissolved region
The union of touching polygons of one class with identical attribution, taken as a single canonical feature. Where a class uses dissolved regions, re-tiling the same region is noise — the polygon analog of sectioning.

### Render area
A cartographic area shape (display-layer, typically identifier-less), e.g. a parking-lot outline. Modeled as a canonical feature whose identity comes from matching rather than an identifier.

### Element change
One row of the literal PBF diff between baseline and target: a single way, node, or relation that was created, deleted, or modified. The unit of raw churn.

### Change group
A set of element changes jointly explained by one editing event (e.g. the four element changes produced by one sectioning). Verdicts (noise / real / mixed) land on change groups, and every element change points at the group that explains it. An element change no group claims and that leaves no trace in the canonical diff is pure noise by itself.
