# Tag classification (attribution vs ignored)

The per-tag include/exclude decisions for canonical-model comparison
(see *Attribution* in [CONTEXT.md](../CONTEXT.md)). Excluding a tag is
verdict-setting — every change to an excluded tag becomes noise by
construction — so every exclusion here is an explicit decision, and
**anything not listed counts as attribution (fail-visible) until ruled out**.

Status: working list, established on road ways (`highway=*`) by the #8/#15
prototypes on the Amersfoort clips. Other feature classes have not been
classified yet. The reference implementation of these rules is `tag_class()`
in `prototypes/bivalent_merge_prototype.py` (branch `prototype/bivalent-merge`);
keep this file and that function in sync until a real implementation exists.

## Ignored — identity

Identifiers, not descriptions of reality. Used for feature identity/matching,
excluded from attribution comparison.

| tag | note |
|---|---|
| `gers_identifier` | 100% stable across releases (#2); names segments |
| `osm_identifier` | 100% stable across releases (#2) |

## Ignored — metadata / housekeeping

Provenance and pipeline bookkeeping about the data, not about the road.

| tag | note |
|---|---|
| `layer_id`, `layer_id:<key>` | per-tag provenance: source layer of `<key>` |
| `license`, `license:<key>` | per-tag provenance: license of `<key>` |
| `license_zone` | provenance |
| `supported` | pipeline flag (on nearly every element) |
| `data_size_index` | pipeline bookkeeping |

Note: `supported:<key>` (e.g. `supported:bridge`, `supported:foot`) is **not**
excluded — only the bare `supported` is. The `supported:*` family is
unclassified (see below).

## Attribution — with special canonical handling

| tag | handling |
|---|---|
| `gradient:linear`, `curvature:linear` | way-relative (see CONTEXT.md): cm offsets along the way, `a-b#null` = no data; re-based into merged-road offset space, values negate on reversal (#15, empirically confirmed) |
| `house_numbers:range:{left,right}` | `from\|to\|scheme\|`; direction-normalized on reversal (left/right key swap + from/to value swap) but kept as **adjacent blocks, never merged** — placement is real information (#15 review) |
| `oneway` | `yes`↔`-1` on reversal |
| `*:forward` / `*:backward` | key-segment swap on reversal |
| `*:left` / `*:right` | key-segment swap on reversal |
| `*:lanes*` with `\|`-separated values | list order reverses on reversal (lanes are left-to-right in travel direction) |

## Attribution — unclassified, worth ruling out (candidates, undecided)

Currently counted as attribution by the fail-visible default, but flagged
during #15 review as likely metadata/derived; each needs an explicit decision:

- `zoomlevel_min` and render/display-layer policy tags (#1 notes)
- `qa:*`, `confidence:*` (#1 notes)
- `parsed:name:*` (derived from `name`)
- pronunciation blocks (`name:*:pronunciation:*`, `*fonxsamp*`)
- `speed:free_flow`, `speed:week*`, `speed:profile_ids` (possibly derived/statistical)
- `derivedmaxspeed*` (name suggests derived)
- `supported:<key>` family (semantics unknown)
- `wikidata`, `supported:wikidata`
- `data_size_index` siblings if more appear

Deciding any of these is verdict-setting: record the decision here and in the
ticket that made it.
