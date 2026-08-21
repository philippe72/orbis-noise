# Do Orbis identifiers survive across releases?

Research findings for ticket 001 (orbis-identifier-stability). Question: are OSM element IDs stable
between consecutive Orbis releases, which identifier-like tags does Orbis carry, on which feature
classes, and do they give reliable cross-version identity for ways, nodes, and relations?

Sources: TomTom/Overture/OSM primary documentation (cited inline) and empirical probing of the three
extracts in `data/orbis/` (`orbis_nexventura_{26320_001,26330_000,26340_000}_global_nld.osm.pbf`,
released 2026-08-06 / 2026-08-12 / 2026-08-19). Empirical method: pyosmium 4.3.1, single-pass sample
of a ~2 x 2.2 km bounding box in central Amsterdam (lon 4.88–4.91, lat 52.36–52.38), all three
versions, comparing element IDs, geometry, tags, and identifier-tag values pairwise.

## Verdict (summary)

1. **Empirically, raw element IDs are highly stable between these consecutive weekly releases** —
   99.98%+ of way IDs, 99.8–99.99% of node IDs, and 99.1–99.96% of relation IDs in the sampled area
   persist, and persisting IDs almost always keep their exact geometry. But **TomTom gives no
   documented stability guarantee for them**, its own materials call OSM-style IDs unreliable, and
   one observed churn event (below) confirms IDs can be regenerated wholesale for a feature class.
2. **`gers_identifier` (Overture GERS UUID) and `osm_identifier` (upstream OSM element ID) are the
   identity carriers TomTom actually commits to**, and empirically their *values* are 100% stable
   across all three versions in the sample. They cover roads, buildings, railways, and POIs — but
   not Orbis-specific relations (road_access etc.), not routing nodes, and not untagged geometry
   nodes.
3. **Neither element IDs nor identifier tags are one-to-one identity for ways**: ~15% of
   `gers_identifier` / `osm_identifier` values on ways are shared by 2+ ways (sectioning), so
   identifier tags name the *road segment*, not the way. Geometry/attribute matching still has to
   resolve which piece is which; identifiers can carry the grouping.

## 1. What the documentation says

### TomTom on Orbis IDs

- The public Orbis "uncompiled maps" docs describe the PBF delivery (nodes/ways/relations) but are
  **silent on ID semantics and stability**
  ([docs.tomtom.com/orbis-uncompiled-maps/data-format](https://docs.tomtom.com/orbis-uncompiled-maps/data-format)).
  The extract naming scheme (`orbis_nexventura_*`) is not documented publicly at all.
- TomTom's Orbis/OSM/Overture white paper states Orbis "adopts Overture's standard Global Reference
  Entity System (GERS) IDs, and OSM identifiers (IDs) for seamless integration" — i.e. Orbis carries
  *both* GERS and OSM identifiers — with no stability claim for the PBF element IDs themselves
  ([download.tomtom.com/open/banners/white-paper-orbis-osm-overture.pdf](https://download.tomtom.com/open/banners/white-paper-orbis-osm-overture.pdf), pp. 4–5).
- TomTom's first-party engineering material explicitly criticizes OSM-style element IDs as unstable:
  "With every split, the original section ID gets removed, and the newly introduced sections get
  assigned new IDs … ID changes undermine the map reliability"
  ([engineering.tomtom.com/overture-transportation-network-linear-referencing](https://engineering.tomtom.com/overture-transportation-network-linear-referencing/));
  "unexpected ID changes can break commercial mapping systems … With the GERS ID … every item on
  the map has a unique ID that will not change"
  ([tomtom.com/newsroom/behind-the-map/what-makes-tomtom-orbis-maps-so-alluring-for-location-tech](https://www.tomtom.com/newsroom/behind-the-map/what-makes-tomtom-orbis-maps-so-alluring-for-location-tech/)).
- The authoritative spec ("TomTom Orbis Maps specifications", including the OSM-to-Orbis mapping) is
  referenced from
  [docs.tomtom.com/tomtom-orbis-maps/documentation/introduction](https://docs.tomtom.com/tomtom-orbis-maps/documentation/introduction)
  but is login-gated; it also notes "feature identifiers are not shared between the two platforms"
  (legacy TomTom Maps vs Orbis).

### Overture GERS

- "A GERS ID is a 128-bit unique identifier that Overture keeps stable across data releases and
  updates"; Overture runs matching/conflation "to ensure that the ID remains stable across releases"
  ([docs.overturemaps.org/gers](https://docs.overturemaps.org/gers/)). Since mid-2025 GERS IDs are
  UUIDs ([release notes](https://docs.overturemaps.org/blog/2025/06/25/release-notes/)), matching the
  UUID-format `gers_identifier` values observed in the extracts.
- TomTom's Global Entity Matcher docs: "GERS IDs are designed to be persistent. They change only when
  the real-world feature changes significantly"
  ([docs.tomtom.com/global-entity-matcher/gers-ids](https://docs.tomtom.com/global-entity-matcher/gers-ids)).

### OSM ID semantics (context for `osm_identifier`)

- OSM element IDs persist across ordinary edits of the same element
  ([wiki.openstreetmap.org/wiki/Elements](https://wiki.openstreetmap.org/wiki/Elements)), but the OSM
  wiki itself treats raw IDs as non-permanent — splits, retyping, and delete/recreate all change them
  ([wiki.openstreetmap.org/wiki/Permanent_ID](https://wiki.openstreetmap.org/wiki/Permanent_ID));
  on a way split the original ID stays on only one fragment
  ([josm.openstreetmap.de/wiki/Help/Action/SplitWay](https://josm.openstreetmap.de/wiki/Help/Action/SplitWay)).
- **Orbis is TomTom's own database exported in OSM PBF format** (generator `osm4j-pbf-1.2.0`, no
  replication metadata, no element timestamps in the header). OSM.org editing semantics do not
  automatically apply; nothing public documents how TomTom generates or regenerates element IDs per
  release.

## 2. Empirical: element-ID stability (Amsterdam 2 x 2 km sample)

Sample sizes per version: ~392.5k nodes, ~42.3k ways, ~85.2k relations (file-wide totals: ~282M
nodes, ~38M ways, ~25M relations for the Netherlands extract).

| Pair | Element | Common IDs | Only in older | Only in newer | Common w/ same geometry | Common w/ same tags |
|---|---|---|---|---|---|---|
| 26320→26330 | nodes | 391,909 (99.84%) | 640 | 667 | 391,892 (99.996%) | 391,249 |
| 26320→26330 | ways | 42,291 (99.94%) | 27 | 28 | 42,289 same refs | 41,568 |
| 26320→26330 | relations | 84,423 (99.12%) | 747 | 708 | 83,929 same members | 84,419 |
| 26330→26340 | nodes | 392,541 (99.99%) | 35 | 196 | 392,519 same coord | 392,414 |
| 26330→26340 | ways | 42,311 (99.98%) | 8 | 8 | 42,250 same refs | 35,848 |
| 26330→26340 | relations | 85,095 (99.96%) | 36 | 135 | 84,970 same members | 85,089 |
| 26320→26340 | nodes | 391,874 (99.83%) | 675 | 863 | 391,837 same coord | 391,163 |
| 26320→26340 | ways | 42,283 (99.92%) | 35 | 36 | 42,222 same refs | 35,633 |
| 26320→26340 | relations | 84,387 (99.08%) | 783 | 843 | 83,771 same members | 84,379 |

Key observations:

- **Way IDs: effectively stable, and vanished IDs are real removals, not renumbering.** For every
  pair, zero vanished way IDs reappeared under a new ID with identical geometry. Persisting way IDs
  keep their exact node-ref list in >99.8% of cases.
- **Node IDs: one observed churn event.** Between 26320 and 26330, 615 of ~63.6k `routing_node=yes`
  nodes (~1%) got new IDs at the *exact same coordinates and identical tags* — pure ID churn with no
  real-world change, confined to the routing-node population (synthetic IDs in the 63-billion range).
  Between 26330 and 26340 only 14 routing nodes churned. So churn is episodic and
  population-specific: a feature class can be regenerated with fresh IDs while everything else holds.
  These churned nodes carry no `gers_identifier`/`source_identifier`, so nothing tags them across the
  jump — only coordinates match them.
- **Untagged geometry nodes are near-perfectly stable** (10–15 lost per pair out of ~260k).
- **Relation IDs: stable at 99.1–99.96%**, with the 26320→26330 pair showing the most turnover
  (~0.9%), concentrated in Orbis-specific relation types (see below) that have no identifier tags at
  all.
- **Tag values change far more than identity**: 26330→26340, 6,463 of 42,311 common ways (15%)
  changed tags — dominated by `speed:profile_ids` (3,036), `gradient:linear` (2,665), and
  `speed:*` statistics. This is attribute refresh noise on stable elements, not identity churn.

### Element IDs are Orbis-synthetic, not OSM IDs

- ID ranges (26340): nodes up to 63,232,535,584; ways up to 7,905,137,919; relations up to
  8,101,224,965 — all far beyond the live OSM ID space.
- Of 25,331 ways carrying `osm_identifier`, **zero** have element ID equal to the tag value; the tag
  values sit in genuine OSM range (max ~1.53B). Orbis renumbers everything on ingest; the upstream
  OSM ID survives only as a tag.

## 3. Identifier-like tags: inventory and coverage

Per-element-type counts in the 26340 sample (392,737 nodes / 42,319 ways / 85,230 relations):

| Tag | Nodes | Ways | Relations | What it is |
|---|---|---|---|---|
| `gers_identifier` | 11,079 | 24,338 | 176 | Overture GERS UUID (e.g. `ce547268-4527-…`) |
| `osm_identifier` | 6,519 | 25,331 | 193 | Upstream OSM element ID (numeric) |
| `source_identifier:internal` | 11,623 | 23 | 0 | TomTom-internal source ref (e.g. `Aqua ID\|<uuid>`, `ttom-sd\|2024.03.007`) |
| `layer_id` | 132,663 (all tagged) | 39,926 | 85,228 | **Not** an identity — a numeric layer/theme code (16–30 distinct values); `layer_id:*`/`license:*`/`supported:*` prefixed keys are per-attribute metadata |
| `ref:bag` | — | 11,799 | 171 | Dutch BAG building-register ID (external national ID) |
| `evse_id` / `station_id` | — | — | 961 / 280 | EV-charging asset IDs |
| `wikidata` / `brand:wikidata` | 99 / 765 | 630 / 1 | 79 / 6 | External knowledge-base refs |

Coverage by feature class (26340):

- **Ways**: buildings 11,854/11,863 have `gers_identifier` (99.9%) and 11,812 `osm_identifier`;
  highways 10,949/11,217 GERS (97.6%), 10,806 OSM id; railways 1,159/1,159. Buildings additionally
  have `ref:bag` on 11,799/11,863. **Gaps**: `natural` (0/763 GERS; 635 have `osm_identifier`),
  `barrier` (0/395 either), `waterway`, `power`, most `man_made`/`landuse`, and 2,393 untagged ways.
- **Nodes**: only 11,079 of 392,737 nodes carry GERS (POIs: amenity 2,993/5,602, shop 2,614/3,305;
  some highway/railway point features). Address points (43,840) have no GERS/OSM id but have
  structured `addr:*` and `source_identifier:internal` on some. The two largest node populations —
  untagged geometry vertices (260k) and routing nodes (63.6k) — carry **no identifier tags at all**.
- **Relations**: essentially **no identifier tags on Orbis-specific relation types**, which dominate
  the file (sample: `road_access` 65,455, `line_in_named_place` 14,467, `connectivity` 1,153,
  `charging_*`, `traffic_sign_along_road`, …: all 0% GERS/OSM id). Only OSM-inherited types carry
  them: `multipolygon` 192/241, building relations 175/176. Orbis-specific relations are identified
  only by their element ID and membership.

## 4. Empirical: identifier-tag value stability (26330 → 26340; spot-checked 26320 → 26340)

| Tag (element) | Values in A | Values in B | Common | Common on same element ID(s) |
|---|---|---|---|---|
| `gers_identifier` (nodes) | 11,078 | 11,079 | 11,077 | 11,076 |
| `gers_identifier` (ways) | 15,463 | 15,463 | 15,463 | 15,459 |
| `gers_identifier` (relations) | 176 | 176 | 176 | 176 |
| `osm_identifier` (ways) | 17,719 | 17,719 | 17,719 | 17,713 |
| `osm_identifier` (nodes) | 6,498 | 6,502 | 6,498 | 6,493 |
| `source_identifier:internal` (nodes) | 11,341 | 11,349 | 11,331 | 11,330 |
| `ref:bag` (ways) | 11,790 | 11,790 | 11,790 | — |

- **Identifier values are essentially 100% stable** across the three weekly releases; over the full
  26320→26340 span only 14 of 15,463 way GERS values moved to a different element-ID set (i.e. the
  ways carrying that GERS were resectioned/renumbered while the GERS value survived) — exactly the
  behavior GERS promises.
- **But identifiers are not one-to-one on ways**: 2,232 of 15,463 way GERS values (14%) and 2,580 of
  17,719 way `osm_identifier` values (15%) are carried by 2+ ways. One Overture segment / one
  upstream OSM way maps to several Orbis ways (sectioning). Node GERS values are unique (0
  duplicates).

## 5. Implications for the matching design

- **Identity-first matching is viable where identifier tags exist** — for roads, buildings,
  railways, and GERS-bearing POIs, match on `gers_identifier` (fall back `osm_identifier`,
  `ref:bag` for buildings) at the *segment/feature* level, then resolve 1:N sectioning within a
  matched identifier group geometrically. This directly supports the merged-road model: a GERS
  value groups the ways of one real segment.
- **Element IDs are a strong heuristic but not a contract.** They are stable enough to seed change
  detection (an unchanged (ID, geometry, tags) triple is almost certainly the same feature), but the
  routing-node churn event proves TomTom regenerates IDs for whole populations without any real
  change — element-ID diffs alone will periodically manufacture large fake add/delete waves, which
  is precisely the noise this project must classify. Treat "same ID" as evidence, never as identity.
- **Geometry/attribute matching must carry the load for**: untagged geometry nodes, routing nodes,
  address points, `natural`/`barrier`/`waterway`/`power` ways, and — critically — **all
  Orbis-specific relation types** (`road_access` et al., 95%+ of relations), which have no
  identifiers beyond their (empirically fairly stable) element ID and their membership.

## 6. Coverage gaps and caveats

- Empirics come from **one 2 x 2 km urban sample, one country (NLD), three consecutive weekly
  releases** (26320_001 → 26340_000). Rural areas, other countries, larger spans, and major-release
  boundaries (e.g. 263xx → 264xx) may behave differently; the observed routing-node churn shows
  regeneration events happen and could hit other populations at other times.
- Bounding-box sampling has edge effects (ways/relations enter or leave the box); the only-in-one-
  version counts are upper bounds on true churn.
- No public TomTom document states element-ID stability either way; the gated "TomTom Orbis Maps
  specifications" is the authoritative source and is worth obtaining. The `nexventura` naming and
  the meaning of `layer_id` codes are undocumented publicly.
- Extracts carry no per-element version/timestamp metadata usable for change tracking
  (`has_multiple_object_versions=false`; element `version` fields present but not analyzed as
  meaningful).

## Appendix: reproduction

Sampling/comparison scripts used pyosmium 4.3.1 (single `SimpleHandler` pass per file; nodes
filtered to the bbox, ways kept when any node ref is in-bbox, relations kept when any member is a
kept node/way). Runtime ~2–4 min per 6.8 GB extract. Geometry comparison used exact coordinates
(7 decimal places) and node-ref lists; "reappears as new ID" = identical coordinate set (nodes) or
identical resolved coordinate sequence (ways) present under a different ID in the newer version.
