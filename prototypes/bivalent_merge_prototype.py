"""PROTOTYPE — THROWAWAY (issue #8). Not production code.

Question: does the merged-road model hold on real Orbis data?

Loads one Amersfoort clip, merges road ways (highway=*) across bivalent
nodes into merged roads, re-expresses each constituent way's tags as
linear references (offset intervals in meters) on the merged road, and
answers three things concretely:

 1. What happens at topologically-bivalent nodes in practice — do tags
    change there, do oneway directions flip, do layer/bridge boundaries
    sit there? (I.e. is pure-topology bivalency a sound merge criterion,
    given linear referencing carries the tag changes?)
 2. How often merging fires (ways -> merged roads reduction).
 3. Whether linear-referenced attribution round-trips: can every original
    way's full tag set be reconstructed exactly from the merged road's
    linear references?

Run:  .venv-research/Scripts/python.exe prototypes/bivalent_merge_prototype.py
Output: stdout summary + prototypes/output/bivalent_merge_report.md
"""

import collections
import math
import os
import sys

import osmium

sys.stdout.reconfigure(encoding="utf-8")

CLIP = sys.argv[1] if len(sys.argv) > 1 else "data/clips/amersfoort_26340.osm.pbf"
OUT = "prototypes/output/bivalent_merge_report.md"

# --- tag classification (working rules; unclassified => attribution, fail-visible) ---

IDENTITY_KEYS = {"osm_identifier", "gers_identifier"}

def tag_class(k: str) -> str:
    if k in IDENTITY_KEYS:
        return "identity"
    if k in ("license_zone", "supported", "data_size_index"):
        return "meta"
    if k == "layer_id" or k.startswith("layer_id:"):
        return "meta"
    if k == "license" or k.startswith("license:"):
        return "meta"
    return "attribution"

# --- direction handling for reversed constituent ways ---

def flip_key_value(k: str, v: str):
    """Re-express a tag of a reversed way in the merged road's direction.
    Returns (k, v, flipped_ok). Symmetric: applying twice is identity."""
    if k == "oneway":
        if v == "yes":
            return k, "-1", True
        if v == "-1":
            return k, "yes", True
        return k, v, True  # no / other: direction-neutral
    if is_way_relative(k):
        return k, v, False  # offsets/signs are way-relative; needs re-basing, unsolved
    parts = k.split(":")
    swap = {"forward": "backward", "backward": "forward", "left": "right", "right": "left"}
    if any(p in swap for p in parts):
        k = ":".join(swap.get(p, p) for p in parts)
        return k, v, True
    if "lanes" in parts and "|" in v:  # lane lists are ordered left-to-right in travel direction
        return k, "|".join(reversed(v.split("|"))), True
    return k, v, True

# tags whose VALUE embeds its own along-the-way referencing (offsets, ranges,
# ordered aggregates). Sectioning rewrites these values even when reality is
# unchanged, and reversal would need value re-basing — flagged, not solved.
WAY_RELATIVE_PREFIXES = ("gradient:linear", "curvature:linear", "house_numbers:")

def is_way_relative(k: str) -> bool:
    return k.startswith(WAY_RELATIVE_PREFIXES) or \
        (k.startswith(("layer_id:", "license:", "supported:")) and
         is_way_relative(k.split(":", 1)[1]))


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------- pass 1: road ways ----------------

class WayPass(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}  # id -> (tags dict, [node refs])

    def way(self, w):
        if "highway" not in w.tags:
            return
        refs = [n.ref for n in w.nodes]
        if len(refs) < 2:
            return
        self.ways[w.id] = (dict((t.k, t.v) for t in w.tags), refs)


print(f"pass 1: reading road ways from {CLIP} ...", flush=True)
wp = WayPass()
wp.apply_file(CLIP)
ways = wp.ways
print(f"  {len(ways)} road ways", flush=True)

# --- degree analysis over road ways only (decision: bivalency counts road ways;
#     buildings/landuse sharing the node don't give a traveler a choice) ---

endpoint_at = collections.defaultdict(list)   # node -> [way ids], with multiplicity
interior_count = collections.Counter()
needed_nodes = set()
for wid, (tags, refs) in ways.items():
    endpoint_at[refs[0]].append(wid)
    endpoint_at[refs[-1]].append(wid)
    for r in refs[1:-1]:
        interior_count[r] += 1
    needed_nodes.update(refs)

bivalent = {}
for node, wids in endpoint_at.items():
    if len(wids) == 2 and wids[0] != wids[1] and interior_count[node] == 0:
        bivalent[node] = tuple(wids)

# ---------------- pass 2: locations + tags of bivalent nodes ----------------

class NodePass(osmium.SimpleHandler):
    def __init__(self, needed, join_nodes):
        super().__init__()
        self.needed = needed
        self.join_nodes = join_nodes
        self.loc = {}
        self.join_tags = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)
            if n.id in self.join_nodes:
                tags = dict((t.k, t.v) for t in n.tags)
                if tags:
                    self.join_tags[n.id] = tags


print("pass 2: reading node locations ...", flush=True)
np_ = NodePass(needed_nodes, set(bivalent))
np_.apply_file(CLIP)
loc = np_.loc
print(f"  {len(loc)} locations, {len(np_.join_tags)} tagged join nodes", flush=True)

# ---------------- characterize each bivalent join ----------------

join_stats = collections.Counter()
diff_key_counter = collections.Counter()
attribution_diff_examples = []
for node, (wa, wb) in bivalent.items():
    ta, tb = ways[wa][0], ways[wb][0]
    diff_keys = {k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k)}
    classes = {tag_class(k) for k in diff_keys}
    attr_diff = {k for k in diff_keys if tag_class(k) == "attribution"}
    if not diff_keys:
        cat = "identical tags"
    elif classes <= {"meta", "identity"}:
        cat = "identity/meta-only diff (pure sectioning signature)"
    elif all(is_way_relative(k) for k in attr_diff):
        cat = "only way-relative tag values differ (likely sectioning artifact)"
    else:
        cat = "attribution diff (needs linear referencing)"
        for k in diff_keys:
            if tag_class(k) == "attribution":
                diff_key_counter[k] += 1
        if len(attribution_diff_examples) < 8:
            akeys = sorted(k for k in diff_keys if tag_class(k) == "attribution")
            lon, lat = loc[node]
            attribution_diff_examples.append(
                (node, lat, lon, wa, wb,
                 [(k, ta.get(k), tb.get(k)) for k in akeys[:6]]))
    join_stats[cat] += 1
    # boundary flavors (not mutually exclusive)
    if ta.get("oneway", "no") != tb.get("oneway", "no"):
        join_stats["~ oneway value differs"] += 1
    for bk in ("layer", "bridge", "tunnel"):
        if ta.get(bk) != tb.get(bk):
            join_stats[f"~ {bk} boundary"] += 1
    if ta.get("highway") != tb.get("highway"):
        join_stats["~ highway class changes"] += 1
    if ta.get("name") != tb.get("name"):
        join_stats["~ name changes"] += 1

# ---------------- build merged roads (chains through bivalent nodes) ----------------

def other_end(wid, node):
    refs = ways[wid][1]
    return refs[-1] if refs[0] == node else refs[0]

visited = set()
merged_roads = []  # list of [(wid, reversed_flag), ...]

def walk(start_wid, start_node):
    """Walk from a chain terminus: start_node is the non-extendable end."""
    chain = []
    wid, node = start_wid, start_node
    while True:
        refs = ways[wid][1]
        rev = refs[0] != node
        chain.append((wid, rev))
        visited.add(wid)
        nxt = other_end(wid, node)
        pair = bivalent.get(nxt)
        if not pair:
            return chain
        na, nb = pair
        nw = nb if na == wid else na
        if nw in visited:  # closed ring
            return chain
        wid, node = nw, nxt

for wid, (tags, refs) in ways.items():
    if wid in visited:
        continue
    a, b = refs[0], refs[-1]
    if a not in bivalent:
        merged_roads.append(walk(wid, a))
    elif b not in bivalent:
        merged_roads.append(walk(wid, b))

for wid in ways:  # leftovers: pure rings (every node bivalent)
    if wid not in visited:
        merged_roads.append(walk(wid, ways[wid][1][0]))

# deterministic orientation from geometry (node ids are unstable across releases)
for chain in merged_roads:
    fw, frev = chain[0]
    lw, lrev = chain[-1]
    frefs, lrefs = ways[fw][1], ways[lw][1]
    start = loc[frefs[-1] if frev else frefs[0]]
    end = loc[lrefs[0] if lrev else lrefs[-1]]
    if end < start:
        chain.reverse()
        for i, (w, r) in enumerate(chain):
            chain[i] = (w, not r)

# ---------------- linear referencing ----------------

class MergedRoad:
    __slots__ = ("chain", "spans", "length", "linrefs", "flip_warnings")

def build_linear(chain):
    mr = MergedRoad()
    mr.chain = chain
    mr.spans = []
    mr.flip_warnings = []
    offset = 0.0
    raw = collections.defaultdict(list)  # key -> [(start, end, value)]
    for wid, rev in chain:
        tags, refs = ways[wid]
        pts = [loc[r] for r in (reversed(refs) if rev else refs)]
        length = sum(haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
        span = (offset, offset + length)
        mr.spans.append((wid, rev, span))
        for k, v in tags.items():
            if rev:
                k2, v2, ok = flip_key_value(k, v)
                if not ok:
                    mr.flip_warnings.append((wid, k))
            else:
                k2, v2 = k, v
            raw[k2].append((span[0], span[1], v2))
        offset += length
    mr.length = offset
    # merge adjacent intervals with equal value — this is where sectioning dies
    mr.linrefs = {}
    for k, ivs in raw.items():
        ivs.sort()
        out = [list(ivs[0])]
        for s, e, v in ivs[1:]:
            if v == out[-1][2] and abs(s - out[-1][1]) < 1e-9:
                out[-1][1] = e
            else:
                out.append([s, e, v])
        mr.linrefs[k] = [tuple(x) for x in out]
    return mr

print("building merged roads + linear references ...", flush=True)
mrs = [build_linear(c) for c in merged_roads]

# ---------------- round-trip validation ----------------

def reconstruct(mr, span, rev):
    got = {}
    s0, s1 = span
    if s1 - s0 <= 0:
        return None  # zero-length constituent: span carries no interval
    for k, ivs in mr.linrefs.items():
        for s, e, v in ivs:
            if s <= s0 + 1e-9 and e >= s1 - 1e-9:  # interval covers the span
                if rev:
                    k2, v2, _ = flip_key_value(k, v)
                else:
                    k2, v2 = k, v
                got[k2] = v2
                break
            if s < s1 - 1e-9 and e > s0 + 1e-9:  # partial overlap: broken
                got[k + "!!PARTIAL"] = v
    return got

rt_pass = rt_fail = rt_zero = 0
rt_fail_examples = []
for mr in mrs:
    for wid, rev, span in mr.spans:
        got = reconstruct(mr, span, rev)
        if got is None:
            rt_zero += 1
            continue
        want = ways[wid][0]
        if got == want:
            rt_pass += 1
        else:
            rt_fail += 1
            if len(rt_fail_examples) < 5:
                miss = {k: v for k, v in want.items() if got.get(k) != v}
                extra = {k: v for k, v in got.items() if want.get(k) != v}
                rt_fail_examples.append((wid, dict(list(miss.items())[:4]),
                                         dict(list(extra.items())[:4])))

# ---------------- report ----------------

n_ways = len(ways)
n_join = len(bivalent)
sizes = collections.Counter(len(m.chain) for m in mrs)
multi = sum(c for s, c in sizes.items() if s > 1)
flip_warned = [(m, w) for m in mrs for w in m.flip_warnings]
reversed_ways = sum(1 for m in mrs for _, rev, _ in m.spans if rev)
join_node_tag_keys = collections.Counter(
    k for t in np_.join_tags.values() for k in t if tag_class(k) == "attribution")

lines = []
add = lines.append
add(f"# Bivalent merge + linear referencing — prototype report (issue #8)")
add(f"\nClip: `{CLIP}` — road ways = ways with `highway=*`.")
add("Bivalency counted over road ways only; a node is a join iff exactly two road")
add("ways end there and no road way passes through it. Merging fires on pure")
add("topology; tag changes at the join are carried by linear references.\n")

add("## 1. How often merging fires")
add(f"- road ways: **{n_ways:,}**")
add(f"- bivalent join nodes: **{n_join:,}**")
add(f"- merged roads: **{len(mrs):,}**  (reduction ×{n_ways/len(mrs):.2f})")
add(f"- merged roads made of >1 way: **{multi:,}** ({multi/len(mrs)*100:.1f}%)")
add(f"- chain size distribution (ways per merged road): " +
    ", ".join(f"{s}:{c:,}" for s, c in sorted(sizes.items())[:12]) +
    (f", max {max(sizes)}" if max(sizes) > 12 else ""))

add("\n## 2. What sits at a topologically-bivalent node")
total = sum(v for k, v in join_stats.items() if not k.startswith("~"))
for k, v in join_stats.most_common():
    if not k.startswith("~"):
        add(f"- {k}: **{v:,}** ({v/total*100:.1f}%)")
add("\nWay-relative tags (`gradient:linear`, `curvature:linear`, `house_numbers:*`)")
add("embed their own along-the-way offsets/ranges, so sectioning rewrites their")
add("values without any real-world change. Plain linear referencing does NOT")
add("canonicalize them — they need value re-basing into merged-road offset space")
add("(and direction normalization: gradient sign flips on reversal).")
add("\nBoundary flavors at joins (overlapping categories):")
for k, v in sorted(join_stats.items()):
    if k.startswith("~"):
        add(f"- {k[2:]}: **{v:,}**")
add(f"\nJoin nodes carrying their own attribution tags: **{len([t for t in np_.join_tags.values() if any(tag_class(k)=='attribution' for k in t)]):,}**"
    f" — top keys: {join_node_tag_keys.most_common(8)}")
add(f"\nTop attribution keys that differ across a join:")
for k, v in diff_key_counter.most_common(15):
    add(f"- `{k}`: {v:,}")
add("\nExample joins with attribution diffs (lat, lon → paste in a map):")
for node, lat, lon, wa, wb, kvs in attribution_diff_examples:
    add(f"- node {node} ({lat:.5f}, {lon:.5f}) ways {wa}/{wb}: " +
        "; ".join(f"`{k}`: {a!r} → {b!r}" for k, a, b in kvs))

add("\n## 3. Direction handling")
add(f"- constituent ways stored reversed relative to merged-road direction: **{reversed_ways:,}**")
add(f"- flips applied: `oneway` yes↔-1, `forward`↔`backward` and `left`↔`right` key-segment swaps, `|`-list reversal for `:lanes` keys")
add(f"- unflippable tags on reversed ways (all way-relative value referencing): **{len(flip_warned):,}**")
warn_keys = collections.Counter(w[1] for _, w in flip_warned)
if warn_keys:
    add(f"  - top keys: {warn_keys.most_common(10)}")

add("\n## 4. Round-trip (merged road + linear refs → original way tags)")
add(f"- exact reconstructions: **{rt_pass:,}**")
add(f"- failures: **{rt_fail:,}**")
add(f"- zero-length constituent ways (span carries no interval): **{rt_zero:,}**")
for wid, miss, extra in rt_fail_examples:
    add(f"- way {wid}: missing/changed {miss} — spurious {extra}")

add("\n## 5. Example merged roads")
for mr in sorted(mrs, key=lambda m: -len(m.chain))[:3]:
    first_refs = ways[mr.chain[0][0]][1]
    p = loc[first_refs[-1] if mr.chain[0][1] else first_refs[0]]
    add(f"\n### {len(mr.chain)} ways, {mr.length:.0f} m, starts at ({p[1]:.5f}, {p[0]:.5f})")
    add(f"ways: {[w for w, _ in mr.chain]}")
    shown = 0
    for k in sorted(mr.linrefs):
        if tag_class(k) != "attribution" or k == "highway" and False:
            continue
        ivs = mr.linrefs[k]
        if len(ivs) > 1 and shown < 8:  # only keys that actually vary along the road
            add(f"- `{k}`: " + " | ".join(f"[{s:.0f}–{e:.0f}m]={v!r}" for s, e, v in ivs[:6]))
            shown += 1

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("\n".join(lines[:40]))
print(f"\nfull report: {OUT}")
