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

### Element change
One row of the literal PBF diff between baseline and target: a single way, node, or relation that was created, deleted, or modified. The unit of raw churn.

### Change group
A set of element changes jointly explained by one editing event (e.g. the four element changes produced by one sectioning). Verdicts (noise / real / mixed) land on change groups, and every element change points at the group that explains it. An element change no group claims and that leaves no trace in the canonical diff is pure noise by itself.
