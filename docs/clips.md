# Test-region clips (Amersfoort)

Small clipped extracts of the same region from all three Orbis NLD map versions —
the iteration substrate for every prototype ticket (see #1; produced under #4).
Amersfoort was picked because it shows visible churn between versions: the
26330→26340 element diff of these clips found 6,401 created / 4,652 deleted /
49,386 modified elements (see the #3 resolution).

## Files

Under `data/clips/` (gitignored, ~71 MB each; regenerate with the commands below):

| File | Map version | Nodes | Ways | Relations |
|---|---|---|---|---|
| `amersfoort_26320.osm.pbf` | 26320 (2026-08-06) | 3,004,781 | 447,644 | 370,524 |
| `amersfoort_26330.osm.pbf` | 26330 (2026-08-12) | 3,004,647 | 447,582 | 370,768 |
| `amersfoort_26340.osm.pbf` | 26340 (2026-08-19) | 3,005,605 | 447,528 | 371,613 |

## Region

Requested bbox `5.30,52.10,5.50,52.22` (Amersfoort and surroundings, ~200 km²).
The `complete_ways` strategy keeps referenced ways whole, so the effective data
bbox is slightly larger: `(5.1047, 52.0616, 5.5878, 52.2629)` — identical
across all three clips.

## Commands

Using osmium-tool 1.19.1 from the local micromamba env (`.tools-osmium/`; see
the resolution of issue #3 for the toolchain choice and its full findings):

```
.tools-osmium/Library/bin/osmium.exe extract -b 5.30,52.10,5.50,52.22 --strategy complete_ways -o data/clips/amersfoort_26320.osm.pbf data/orbis/orbis_nexventura_26320_001_global_nld.osm.pbf
.tools-osmium/Library/bin/osmium.exe extract -b 5.30,52.10,5.50,52.22 --strategy complete_ways -o data/clips/amersfoort_26330.osm.pbf data/orbis/orbis_nexventura_26330_000_global_nld.osm.pbf
.tools-osmium/Library/bin/osmium.exe extract -b 5.30,52.10,5.50,52.22 --strategy complete_ways -o data/clips/amersfoort_26340.osm.pbf data/orbis/orbis_nexventura_26340_000_global_nld.osm.pbf
```

~75 s and ~16 GB RAM per clip (memory scales with the max node id, 63.2 billion,
not file size). Counts above from `osmium fileinfo -e`.
