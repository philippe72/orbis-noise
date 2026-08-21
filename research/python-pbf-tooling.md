# Python tooling for 6.4 GB Orbis PBF processing

Resolves ticket `002-python-pbf-tooling` (wayfinder:research).
Researched and verified 2026-08-21 on the target machine: Windows 11 Enterprise, Python 3.13.5, 64 GB RAM, NVMe SSD (~3 GB/s reads). All measurements below were taken against the real extracts in `data/orbis/`.

## Recommendation (TL;DR)

| Pipeline need | Tool | Scale |
|---|---|---|
| (1) Clip a region | **osmium-tool 1.19.1** (conda-forge win-64, via micromamba) — `osmium extract --strategy complete_ways` | full-NLD input, verified: 75 s / ~16 GB RAM |
| (2) Stream full element data incl. relations | **pyosmium 4.3.1** (pip wheel) — `SimpleHandler`/`apply_file`, or `FileProcessor` with C++-side filters | full-NLD: streaming, 47 s per pass; clip: sub-second |
| (3) Element-level diff of two PBFs | **`osmium cat -f pbf,add_metadata=none` + `osmium diff`** for the raw change list (5 s/clip); **pyosmium `zip_processors`** when the diff feeds Python logic. Content diff only — Orbis version/timestamp metadata is unusable (see below) | clip scale: seconds–minutes; full-NLD works but is a long streaming pass |
| (4) Graph for bivalent-node merging | **pyosmium → NetworkX 3.6.1** (pip) | clip scale (verified: 2.3 s for a 200 km² clip) |
| Fast whole-file SQL analytics (counts, id ranges, tag stats) | **DuckDB 1.5.5 spatial `st_readosm`** (pip wheel) | full-NLD: 22 s for a grouped count |

Everything except osmium-tool installs with plain `pip` into a venv (native cp313 win_amd64 wheels). osmium-tool has no official Windows binary but conda-forge ships a win-64 build; a single-file `micromamba.exe` download is enough to install it (no Anaconda needed).

## The data (facts that drove the choices)

`osmium fileinfo -e` on `orbis_nexventura_26340_000_global_nld.osm.pbf` (6.81 GB, 18.7 s):

- **282,566,439 nodes, 38,184,512 ways, 25,457,207 relations** — the relation count is enormous by OSM standards (planet OSM has ~12 M relations in 80+ GB). Orbis models a lot through relations; tooling that drops or mangles relations is disqualified for need (2).
- **Objects ordered by type and id: yes** — both `osmium diff` and pyosmium `zip_processors` require sorted input; the extracts qualify as-is, no `osmium sort` needed.
- **Largest node id: 63,232,540,885 (63.2 billion)** — this rules out every "dense" id-indexed structure:
  - `osmium extract` memory ≈ max-node-id/8 per pass: ~7.9 GB (`simple`), ~15.8 GB (`complete_ways`). Fits in 64 GB, verified. `smart` needs a bit more.
  - A pyosmium `dense_file_array` node-location cache would be 63.2e9 × 8 B ≈ **505 GB — impossible**. Use `flex_mem` (auto-sparse) or `sparse_file_array`, and only ever on a clip.
- **Every element has `version=1`** (checked across all 3.8 M elements of a clip) and all timestamps equal the release date (`2026-08-18` throughout). Consequence: **any version- or timestamp-based diff is blind to modifications.** `osmium derive-changes` compares type/id/version only — it would report a way whose tags changed as "unchanged". Element-level diffing must compare content (tags, node refs, member lists, coordinates).
- Generator is `osm4j-pbf-1.2.0`, blobs are ordinary ~1 MB zlib `OSMData` blocks (~260 objects per buffer), `pbf_dense_nodes=true`, no replication metadata. The header bbox is the whole planet; the actual data bbox `(-70.27, 11.78, 21.86, 58.27)` includes the Caribbean Netherlands, so "nld" is not a simple Netherlands rectangle — clip by bbox/polygon, don't assume extent.

## Candidate evaluation

### osmium-tool 1.19.1 (CLI) — recommended for clipping

- **Windows install**: no official binary (GitHub releases ship sources only; [osmcode.org/osmium-tool](https://osmcode.org/osmium-tool/) lists conda-forge as the packaged route). conda-forge has **win-64 builds** ([anaconda.org/conda-forge/osmium-tool](https://anaconda.org/conda-forge/osmium-tool)); installed here with a standalone `micromamba.exe` (v2.9.0) — no Anaconda/Miniconda footprint. Verified: `osmium version 1.19.1, libosmium 2.23.1` runs and reads the extracts.
- **Clip**: `osmium extract -b LON1,LAT1,LON2,LAT2 --strategy complete_ways` ([docs](https://docs.osmcode.org/osmium/latest/osmium-extract.html)). Strategies: `simple` (1 pass, ways may be cut), `complete_ways` (2 passes, ways reference-complete, relations included but not completed), `smart` (3 passes, also completes multipolygon relations; `-S types=any` for all relation types). Memory scales with **highest node id** (see above), not file size.
- **Diff**: `osmium diff` streams two sorted files and compares full content (report-only, no .osc); `osmium derive-changes` emits .osc but is version-based — **useless on Orbis** (see version=1 finding).
- Also useful: `fileinfo -e`, `tags-filter`, `cat` (PBF→XML for tools that need XML), `sort`.

### pyosmium 4.3.1 (PyPI package `osmium`) — recommended core library

- **Windows install**: official `osmium-4.3.1-cp313-cp313-win_amd64.whl` on PyPI — plain `pip install osmium` works ([pypi.org/project/osmium](https://pypi.org/project/osmium/#files)). Verified.
- **Streaming with relations**: full model — relations expose ordered members (type, ref, role), same libosmium as osmium-tool. Two APIs:
  - `SimpleHandler.apply_file()` — callbacks; only the element types you define callbacks for cross into Python. Measured **136 k nodes/s** with a Python callback; a full 6.8 GB pass where only relations hit Python ran in **46.8 s**.
  - `FileProcessor` — Python iterator, plus C++-side filters (`EntityFilter`, `KeyFilter`, `TagFilter`, `IdFilter`, `EmptyTagFilter`).
  - **Windows performance trap (measured)**: bare unfiltered `FileProcessor` iteration over the 6.8 GB file collapses to ~4 k obj/s (warm cache; 25 s for 100 k elements), while the same loop over a 71 MB clip runs at ~258 k obj/s and `apply_file` on the big file runs at 136 k obj/s. On the full file, always use `SimpleHandler` or attach C++-side filters; keep bare `FileProcessor` iteration for clips.
  - Gotcha: `apply_file` on a `SimpleHandler` with **no** callbacks raises `TypeError: Argument must be a handler-like object` — define at least one callback.
- **Diff**: `osmium.zip_processors(fp_old, fp_new)` yields id-aligned pairs from two sorted files — the building block for a content-level element diff ([reference](https://docs.osmcode.org/pyosmium/latest/reference/File-Processing/)). Verified on two clips (numbers below).
- **Clip (pure-Python fallback)**: `IdTracker` + `ForwardReferenceWriter`/`BackReferenceWriter` implement reference-complete geometric extracts per the official cookbook ([Filter-Data-By-Geometry](https://docs.osmcode.org/pyosmium/latest/cookbooks/Filter-Data-By-Geometry/)). All four classes present in 4.3.1 (verified). Slower than osmium-tool but removes the conda dependency if that ever becomes a problem.
- **Geometry**: `with_locations(storage=...)` — on Windows the mmap-backed indexes are unavailable; use default `flex_mem` on clips, `sparse_file_array` if RAM is tight. Never `dense_*` with 63-billion node ids.

### esy-osm-pbf 0.1.1 — viable minimal fallback, not recommended

- Pure-Python (`py3-none-any` wheel, only dep `protobuf`), installs and runs on 3.13 (verified: protobuf 7.36.0 pulled in). Full raw model incl. relation members. Read-only, no writer, no diff, no clip.
- Measured **152 k obj/s** on the real extract — ironically 38× faster than pyosmium's bare `FileProcessor` on the same file, but ~10× slower than DuckDB and it's a dormant project (last release 2024-04, DLR GitLab). Keep in mind only as a zero-native-code escape hatch.

### OSMnx 2.1.1 — not for reading; maybe later for graph algorithms

- Pure-Python, installs fine. **Cannot read PBF at all** — `graph_from_xml` takes OSM XML only ([docs](https://osmnx.readthedocs.io/en/stable/user-reference.html)); would require `osmium cat clip.osm.pbf -o clip.osm` first.
- Its `simplification.simplify_graph` (removes interstitial degree-2 nodes, keeps full edge geometry) is exactly bivalent-node merging — but we need custody of the merge rules (which tags must match, direction handling) for linear referencing, so a NetworkX implementation we control is the better fit. Verified below that plain NetworkX handles clip scale trivially; revisit OSMnx only if its algorithms are wanted wholesale.

### DuckDB 1.5.5 spatial `st_readosm` (+ QuackOSM) — recommended sidecar for analytics

- `pip install duckdb`, `INSTALL spatial; LOAD spatial;` — works on Windows/3.13 (verified). `st_readosm()` streams the PBF multithreaded and exposes raw `kind, id, tags, refs, ref_types, ref_roles, lat, lon` — **relation members with roles survive** (verified against real relations). Fastest whole-file scanner tested: **grouped count of all 346 M elements in 22.4 s**; 100 k-row sample in 0.3 s.
- Good for: id-range checks, tag inventories, quick full-NLD questions, even a SQL FULL OUTER JOIN diff of two versions if we ever need one at full scale.
- Not good for: producing a clipped **PBF** (can't write PBF), reference-complete extracts.
- **QuackOSM** (0.18.1, pure wheel) sits on top but converts relations into (multi)polygon *features* (GeoParquet) — relation structure does not survive, so it fails need (2); not installed.

### GDAL/OGR OSM driver — rejected

- PyPI `gdal` ships **no wheels** (sdist only, native build required); Windows binaries only via conda-forge/OSGeo4W ([gdal.org download](https://gdal.org/en/stable/download.html)). Read-only OSM driver squashes relations into `multipolygons`/`other_relations` layers — raw member lists are lost — and cannot write PBF. No advantage over the stack above for any of the four needs.

## Verified setup (exact commands that worked)

```powershell
# 1. Python env (repo-local venv; Python 3.13.5)
python -m venv .venv-research
.venv-research\Scripts\python.exe -m pip install osmium duckdb networkx shapely
# -> osmium 4.3.1, duckdb 1.5.5, networkx 3.6.1, shapely 2.1.2 (all native cp313 win_amd64 wheels)

# 2. osmium-tool via standalone micromamba (no Anaconda install)
Invoke-WebRequest https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64 -OutFile micromamba.exe
.\micromamba.exe create -y -p .\.tools-osmium -c conda-forge osmium-tool
# -> osmium-tool 1.19.1 / libosmium 2.23.1 at .tools-osmium\Library\bin\osmium.exe
```

Both env dirs (`.venv-research/`, `.tools-osmium/`) are machine-local and gitignored.

## Verification log (all on the real extracts)

| Check | Command / API | Result |
|---|---|---|
| Open + header | `osmium.io.Reader(...).header()` | generator `osm4j-pbf-1.2.0`, dense nodes, planet bbox |
| Stream first 100 k elements | pyosmium `FileProcessor` | works; 25–79 s (see performance trap above) |
| Full-file scan | pyosmium `apply_file`, relation callback only | 46.8 s; 25,457,207 relations |
| Full-file counts | DuckDB `st_readosm` group by kind | 22.4 s; 282.6 M / 38.2 M / 25.5 M |
| File stats | `osmium fileinfo -e` | 18.7 s; sorted; max node id 63.2e9 |
| Relation fidelity | DuckDB `st_readosm` sample | members `(type, ref, role)` intact |
| **Clip (ticket 003)** | `osmium extract -b 5.30,52.10,5.50,52.22 --strategy complete_ways` | **74.7 s**, 71 MB clip: 3,005,605 nodes / 447,528 ways / 371,613 relations |
| Element diff of two versions | pyosmium `zip_processors` and `osmium diff` (metadata stripped) over the 26330 vs 26340 clips | identical results both ways; see numbers below |
| Bivalent-node graph | pyosmium (`KeyFilter('highway')`) → NetworkX MultiGraph | clip: 87,876 highway ways read in 0.8 s; graph 165,526 nodes / 182,821 edges in 0.5 s; 129,059 bivalent nodes; degree-2 chain merge → 53,762 merged roads in 1.0 s |

### Element-level diff, verified two ways (26330 vs 26340, same bbox)

Both clips (identical `osmium extract` bbox, complete_ways) were diffed with two independent methods; **the results agree exactly**:

| Method | created | deleted | modified | time |
|---|---|---|---|---|
| pyosmium `zip_processors` + content signature (tags, refs/members, coords; metadata ignored) | 6,401 | 4,652 | 49,386 | 217 s |
| `osmium diff -c` after `osmium cat -f pbf,add_metadata=none` on both inputs | 6,401 (+) | 4,652 (-) | 49,386 (*) | 5.2 s (+ ~9 s for the two `cat` passes) |

(3,768,959 of 3,829,398 id-slots unchanged; note bbox-boundary drift inflates created/deleted slightly, and sub-threshold coordinate changes count as "modified" here by design — separating those is exactly the project's job.)

**Trap found:** raw `osmium diff` on Orbis files marks *every single element* as different, because Orbis stamps all elements with the release date as timestamp (26330 → `2026-08-11`, 26340 → `2026-08-18`) and `osmium diff` compares metadata. Strip metadata first (`osmium cat -f pbf,add_metadata=none`) or diff in Python ignoring metadata. Likewise `osmium derive-changes` is blind in the opposite direction: all elements are `version=1`, so version-based change detection reports modified elements as unchanged. **Content comparison is the only valid diff on Orbis data.**

The fast path for ticket-scale work: `osmium cat add_metadata=none` + `osmium diff` for the raw change list, `zip_processors` when the diff needs to feed Python logic directly (change-group building).

## Clip command for ticket 003

```powershell
.tools-osmium\Library\bin\osmium.exe extract `
  -b 5.30,52.10,5.50,52.22 `
  --strategy complete_ways `
  -o data\clips\amersfoort_26340.osm.pbf `
  data\orbis\orbis_nexventura_26340_000_global_nld.osm.pbf
```

~75 s and ~16 GB peak RAM per version on this machine. Run once per map version with the identical bbox; the resulting clips are the working set for everything downstream. Use `--strategy smart -S types=any` instead if complete relation membership across the clip boundary turns out to matter (costs one extra pass and somewhat more RAM).

## Clip-scale vs full-NLD summary

| Operation | Full-NLD (6.8 GB) | Clip (~70 MB) |
|---|---|---|
| `osmium extract` | 75 s, ~16 GB RAM | — |
| pyosmium streaming pass | 47 s (C++ path); bare `FileProcessor` pathological | sub-second |
| DuckDB `st_readosm` scan | 22 s | instant |
| Node-location cache | only `sparse_*`; `dense_*` impossible (505 GB) | `flex_mem` fine |
| Element-level content diff | possible (streaming, sorted input); `osmium diff` route preferred at this scale | `osmium diff` 5 s; `zip_processors` 217 s |
| NetworkX graph + bivalent merge | not attempted (38 M ways; would need ~10s of GB) | 2.3 s total |

## Sources

- osmium-tool manual: https://docs.osmcode.org/osmium/latest/ (extract, diff, derive-changes, sort, fileinfo pages)
- osmium-tool packaging: https://osmcode.org/osmium-tool/ · https://anaconda.org/conda-forge/osmium-tool
- pyosmium docs: https://docs.osmcode.org/pyosmium/latest/ (user manual 02/03/04/06, cookbook "Filter Data By Geometry", File-Processing reference) · https://pypi.org/project/osmium/#files
- DuckDB spatial `ST_ReadOSM`: https://duckdb.org/docs/stable/core_extensions/spatial/functions
- QuackOSM: https://github.com/kraina-ai/quackosm · https://kraina-ai.github.io/quackosm/
- GDAL OSM driver: https://gdal.org/en/stable/drivers/vector/osm.html · https://gdal.org/en/stable/download.html
- OSMnx reference: https://osmnx.readthedocs.io/en/stable/user-reference.html
- esy-osm-pbf: https://gitlab.com/dlr-ve-esy/esy-osm-pbf
- osmconvert (`--diff-contents`, alternative content differ with native Windows binaries): https://wiki.openstreetmap.org/wiki/Osmconvert
- All performance/memory/count figures: measured on this machine against `data/orbis/orbis_nexventura_26340_000_global_nld.osm.pbf` (and `26330` for the diff), 2026-08-21.
