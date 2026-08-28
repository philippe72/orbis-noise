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

## NLD-wide, class-filtered extracts (added under #12)

The Amersfoort clip turned out to be too quiet for buildings — 26320→26340 gives
only 15 created / 26 deleted / 2 modified footprints out of 152,387 — so #12 had
to widen the sample to the whole country. A bbox clip cannot do that cheaply: a
bbox extract needs a node-id index sized by the max node id (63.2 billion), so it
costs ~16 GB RAM regardless of area, and four of them at once ran out of memory on
a 63.7 GB machine.

`osmium tags-filter` needs **no** node index, so it is the cheap way to take one
feature class from the full 6.8 GB extract. Under `data/buildings/` (gitignored):

| file | contents | how |
|---|---|---|
| `nld_<ver>_buildings.osm.pbf` | 12.13M building ways + 118K relations, **no node coordinates** | `tags-filter -R … w/building w/building:part r/type=building r/building` |
| `nld_<ver>_addr.osm.pbf` | 12.2M address nodes with coordinates | `tags-filter -R … n/addr:housenumber n/address_point` |
| `deleted_26320.osm.pbf`, `created_26340.osm.pbf` | the churned footprints **with** coordinates | `getid -r … -i <id-file>` |

```
.tools-osmium/Library/bin/osmium.exe tags-filter -R --no-progress -O \
  -o data/buildings/nld_26320_buildings.osm.pbf \
  data/orbis/orbis_nexventura_26320_001_global_nld.osm.pbf \
  w/building w/building:part r/type=building r/building

.tools-osmium/Library/bin/osmium.exe tags-filter -R --no-progress -O \
  -o data/buildings/nld_26320_addr.osm.pbf \
  data/orbis/orbis_nexventura_26320_001_global_nld.osm.pbf \
  n/addr:housenumber n/address_point

.tools-osmium/Library/bin/osmium.exe getid -r --no-progress -O \
  -o data/buildings/deleted_26320.osm.pbf \
  data/orbis/orbis_nexventura_26320_001_global_nld.osm.pbf \
  -i prototypes/output/ids_deleted.txt
```

Measured costs on this workstation, useful for the **scaling to full NLD**
question on the map:

- `tags-filter -R` over one 6.8 GB extract: **~40 s**, ~920 MB out for buildings,
  ~510 MB for address nodes. No node index, so memory stays small.
- `getid -r` over one 6.8 GB extract for 2,500–7,690 way ids: **~60 s**.
- A pyosmium pass over the 920 MB coordinate-free building file, hashing tags plus
  node refs for all 12.13M ways: **~380 s and ~2.5 GB RAM** per version. The
  content hash must be per-element (Orbis resets version and timestamp each
  release, see #3), and an 8-byte digest keyed by element id is exact enough:
  the comparison is hash(a) against hash(b) for the *same* id, so there is no
  birthday problem.
- The pattern that worked: hash-diff on the coordinate-free file to find the
  changed ids, then `getid -r` to pull coordinates for only those. Coordinates for
  12M ways do not fit in Python.

**Trap:** `tags-filter -R` keeps only matching objects, so a `type=building`
relation whose members were not requested loses them. In the created/deleted
extracts every `building:part` way therefore reads as an orphan part
(`orphan_parts` 986 and 114 in the #12 run) — visible in the loader stats, which
is the point, but it means those two files are a churn sample, not a ledger
substrate.
