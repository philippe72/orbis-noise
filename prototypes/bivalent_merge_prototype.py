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

def swap_lr(key):
    if ":left" in key:
        return key.replace(":left", ":right")
    return key.replace(":right", ":left")

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
        return k, v, False  # offsets/signs are way-relative; re-based separately
    if k in ("house_numbers:range:left", "house_numbers:range:right"):
        f = v.split("|")
        if len(f) == 4:
            v = "|".join((f[1], f[0], f[2], f[3]))  # from/to follow way direction
        return swap_lr(k), v, True
    parts = k.split(":")
    swap = {"forward": "backward", "backward": "forward", "left": "right", "right": "left"}
    if any(p in swap for p in parts):
        k = ":".join(swap.get(p, p) for p in parts)
        return k, v, True
    if "lanes" in parts and "|" in v:  # lane lists are ordered left-to-right in travel direction
        return k, "|".join(reversed(v.split("|"))), True
    return k, v, True

# tags whose VALUE embeds its own along-the-way offset referencing. Sectioning
# rewrites these values even when reality is unchanged; the canonical model
# re-bases them into merged-road offset space. house_numbers:range is NOT here:
# its from/to blocks carry real spatial placement, so the canonical model keeps
# them as adjacent linear-referenced blocks (only direction-normalized).
WAY_RELATIVE_PREFIXES = ("gradient:linear", "curvature:linear")

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
    __slots__ = ("chain", "spans", "length", "linrefs", "flip_warnings",
                 "linfuncs")

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

# ---------------- way-relative canonicalization (#15) ----------------
# gradient:linear / curvature:linear: value = `off#val;...` with cm offsets
# along the way (first 0, last == way length in cm) and `a-b#null` no-data
# ranges. Empirics (join continuity): values are continuous across joins in
# travel direction and NEGATE when the way is traversed backwards.
# Canonical form: per merged road, linear functions re-based into merged-road
# cm space. house_numbers:range:{left,right} (`from|to|scheme|`, from/to in way
# direction) are deliberately NOT merged: each block carries real spatial
# placement, so they stay adjacent linear-referenced blocks, only
# direction-normalized (left/right key swap + from/to swap on reversal).

LINFUNC_KEYS = ("gradient:linear", "curvature:linear")

def parse_linvalue(v):
    """-> [(kind, start, end, val)]; kind 'pt' (end None) or 'rng'; val None = null."""
    entries = []
    for part in v.split(";"):
        o, val = part.split("#")
        val = None if val == "null" else int(val)
        if "-" in o:
            a, b = o.split("-", 1)
            entries.append(("rng", int(a), int(b), val))
        else:
            entries.append(("pt", int(o), None, val))
    return entries

def fmt_linvalue(entries):
    return ";".join(
        (f"{a}-{b}#" if k == "rng" else f"{a}#") + ("null" if v is None else str(v))
        for k, a, b, v in entries)

def entry_end(e):
    return e[2] if e[0] == "rng" else e[1]

def linvalue_ok(v):
    try:
        es = parse_linvalue(v)
    except Exception:
        return None
    if fmt_linvalue(es) != v or es[0][1] != 0:
        return None
    pos = 0
    for e in es:
        if e[1] < pos:
            return None
        pos = entry_end(e)
    return es

def mirror_linvalue(entries, length_cm):
    out = []
    for k, a, b, v in reversed(entries):
        nv = None if v is None else -v
        if k == "rng":
            out.append(("rng", length_cm - b, length_cm - a, nv))
        else:
            out.append(("pt", length_cm - a, None, nv))
    return out

dirty_values = collections.Counter()  # raw strings that don't parse/format-identity

def way_linfunc(wid, rev, key):
    """Way's linear-function value in merged-road direction, or None."""
    v = ways[wid][0].get(key)
    if v is None:
        return None
    es = linvalue_ok(v)
    if es is None:
        dirty_values[key] += 1
        return None
    L = entry_end(es[-1])
    return (mirror_linvalue(es, L) if rev else es), L

# --- build canonical runs per merged road, classifying every seam ---

seam_verdicts = collections.Counter()          # (key-type, verdict) counts
jump_sizes = []                                # gradient/curvature |Δ| at discontinuous seams
seam_by_joinnode = collections.defaultdict(set)  # node -> {verdicts at that join}

def seam_node(mr, i):
    """Shared node between chain[i] and chain[i+1]."""
    wid, rev = mr.chain[i]
    refs = ways[wid][1]
    return refs[0] if rev else refs[-1]

def build_canonical(mr):
    mr.linfuncs = {}   # key -> [run]; run = {'start_m','spans':[(idx,wid,rev,Lcm)],'entries':[...]}
    n = len(mr.spans)
    for key in LINFUNC_KEYS:
        runs, cur, base = [], None, 0
        for i, (wid, rev, span) in enumerate(mr.spans):
            wf = way_linfunc(wid, rev, key)
            if wf is None:
                if cur:  # run ends: one-sided seam, extent is preserved canonically
                    runs.append(cur)
                    cur = None
                    seam_verdicts[(key, "extent boundary")] += 1
                    seam_by_joinnode[seam_node(mr, i - 1)].add("ok")
                continue
            es, L = wf
            if cur is None:
                cur = {"start_m": span[0], "spans": [], "entries": [], "cm": 0}
                base = 0
                if i > 0:  # run starts after a tag-less way: one-sided seam
                    seam_verdicts[(key, "extent boundary")] += 1
                    seam_by_joinnode[seam_node(mr, i - 1)].add("ok")
            else:
                base = cur["cm"]
                # classify + stitch the seam
                first = es[0]
                last = cur["entries"][-1]
                node = seam_node(mr, i - 1)
                if last[0] == "pt" and first[0] == "pt" and last[1] == base + first[1] \
                        and last[3] is not None and first[3] is not None:
                    if last[3] == first[3]:
                        seam_verdicts[(key, "continuous")] += 1
                        seam_by_joinnode[node].add("ok")
                        es = es[1:]  # dedupe the shared boundary point
                    else:
                        seam_verdicts[(key, "discontinuity")] += 1
                        jump_sizes.append(abs(last[3] - first[3]))
                        seam_by_joinnode[node].add("jump")
                elif last[0] == "rng" and last[3] is None and first[0] == "rng" and first[3] is None:
                    seam_verdicts[(key, "null-adjacent")] += 1
                    seam_by_joinnode[node].add("ok")
                    cur["entries"][-1] = ("rng", last[1], base + first[2], None)
                    es = es[1:]
                else:
                    seam_verdicts[(key, "mixed pt/null seam")] += 1
                    seam_by_joinnode[node].add("ok")
            cur["spans"].append((i, wid, rev, L))
            cur["entries"].extend(
                ("rng", base + a, base + b, v) if k == "rng" else ("pt", base + a, None, v)
                for k, a, b, v in es)
            cur["cm"] += L
        if cur:
            runs.append(cur)
        if runs:
            mr.linfuncs[key] = runs

print("building way-relative canonical forms ...", flush=True)
for mr in mrs:
    build_canonical(mr)

# --- canonical round-trip: slice each run back into per-way value strings ---

def slice_run(entries, s, e):
    out = []
    for k, a, b, v in entries:
        if k == "pt":
            if s <= a <= e:
                out.append((k, a, b, v))
        elif a < e and b > s:
            out.append((k, max(a, s), min(b, e), v))
    # boundary points shared with the neighboring way:
    if len(out) >= 2 and out[0][0] == "pt" == out[1][0] and out[0][1] == s == out[1][1]:
        out = out[1:]   # duplicate pt at start seam: the later one is ours
    if len(out) >= 2 and out[-1][0] == "pt" == out[-2][0] and out[-1][1] == e == out[-2][1]:
        out = out[:-1]  # duplicate pt at end seam: the earlier one is ours
    if out and out[0][0] == "pt" and out[0][1] == s and any(
            k == "rng" and a == s for k, a, b, v in out[1:]):
        out = out[1:]   # pt at s belongs to the previous way (we start with a null range)
    if len(out) >= 2 and out[-1][0] == "pt" and out[-1][1] == e and any(
            k == "rng" and b == e for k, a, b, v in out[:-1]):
        out = out[:-1]  # pt at e belongs to the next way (we end with a null range)
    return out

crt_pass = crt_fail = 0
crt_fail_examples = []
for mr in mrs:
    for key, runs in mr.linfuncs.items():
        for run in runs:
            cm = 0
            for idx, wid, rev, L in run["spans"]:
                local = [(k, a - cm, None if b is None else b - cm, v)
                         for k, a, b, v in slice_run(run["entries"], cm, cm + L)]
                if rev:
                    local = mirror_linvalue(local, L)
                got = fmt_linvalue(local)
                want = ways[wid][0][key]
                if got == want:
                    crt_pass += 1
                else:
                    crt_fail += 1
                    if len(crt_fail_examples) < 5:
                        crt_fail_examples.append((wid, key, want, got))
                cm += L

# --- join-level rollup for the "only way-relative diffs" category ---

wayrel_joins_ok = wayrel_joins_bad = 0
for node, (wa, wb) in bivalent.items():
    ta, tb = ways[wa][0], ways[wb][0]
    diff_keys = {k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k)}
    attr_diff = {k for k in diff_keys if tag_class(k) == "attribution"}
    if not attr_diff or not all(is_way_relative(k) for k in attr_diff):
        continue
    verdicts = seam_by_joinnode.get(node, set())
    if verdicts and "jump" not in verdicts and "range-break" not in verdicts:
        wayrel_joins_ok += 1
    else:
        wayrel_joins_bad += 1

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
add("\nWay-relative tags (`gradient:linear`, `curvature:linear`) embed their own")
add("along-the-way offsets, so sectioning rewrites their values without any")
add("real-world change; the canonical model re-bases them into merged-road offset")
add("space (with sign flip on reversal). `house_numbers:range:*` values are only")
add("direction-normalized (from/to swap on reversal) and stay adjacent blocks:")
add("each block's placement is real spatial information, never merged away.")
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

add("\n## 5. Way-relative canonicalization (#15)")
add("Semantics established empirically: `gradient:linear`/`curvature:linear` offsets")
add("are **cm along the way** (first 0, last = way length; `a-b#null` = no data);")
add("values are continuous across joins in travel direction and **negate on")
add("reversal** (opposing joins: median |Δ| = 0 flipped vs 6 unflipped).")
add("`house_numbers:range` is `from|to|scheme|` with from/to in way direction —")
add("direction-normalized but deliberately kept as adjacent blocks (real spatial")
add("placement), never merged into one range.")
add(f"\nSeam verdicts (each seam = one bivalent join crossed by a run):")
for (key, verdict), c in sorted(seam_verdicts.items()):
    add(f"- `{key}` — {verdict}: **{c:,}**")
if jump_sizes:
    jump_sizes.sort()
    add(f"- discontinuity sizes: median {jump_sizes[len(jump_sizes)//2]}, "
        f"p90 {jump_sizes[int(len(jump_sizes)*0.9)]}, max {jump_sizes[-1]}")
if dirty_values:
    add(f"- values failing parse/format-identity (kept opaque): {dict(dirty_values)}")
add(f"\nJoin-level rollup of the 'only way-relative tag values differ' category:")
tot = wayrel_joins_ok + wayrel_joins_bad
if tot:
    add(f"- reconcile in canonical form (pure sectioning artifact, confirmed): "
        f"**{wayrel_joins_ok:,}** ({wayrel_joins_ok/tot*100:.1f}%)")
    add(f"- do not reconcile (visible in canonical diff): **{wayrel_joins_bad:,}**")
    pure = join_stats["identical tags"] + \
        join_stats["identity/meta-only diff (pure sectioning signature)"] + wayrel_joins_ok
    add(f"- => **{pure:,}/{total:,}** joins ({pure/total*100:.1f}%) are now pure "
        f"sectioning artifacts (identical + meta-only + reconciled way-relative)")
add(f"\nCanonical round-trip (sliced run functions -> original per-way value strings):")
add(f"- exact: **{crt_pass:,}**, failures: **{crt_fail:,}**")
for wid, key, want, got in crt_fail_examples:
    add(f"- way {wid} `{key}`: want `{want[:80]}` got `{got[:80]}`")
add("(house-number ranges ride the ordinary tag round-trip in section 4: the")
add("from/to swap on reversal is a symmetric flip like oneway.)")

add("\n## 6. Example merged roads")
for mr in sorted(mrs, key=lambda m: -len(m.chain))[:3]:
    first_refs = ways[mr.chain[0][0]][1]
    last_refs = ways[mr.chain[-1][0]][1]
    p = loc[first_refs[-1] if mr.chain[0][1] else first_refs[0]]
    q = loc[last_refs[0] if mr.chain[-1][1] else last_refs[-1]]
    add(f"\n### {len(mr.chain)} ways, {mr.length:.0f} m, "
        f"from ({p[1]:.5f}, {p[0]:.5f}) to ({q[1]:.5f}, {q[0]:.5f})")
    add(f"ways: {[w for w, _ in mr.chain]}")
    shown = 0
    for k in sorted(mr.linrefs):
        if tag_class(k) != "attribution" or k == "highway" and False:
            continue
        ivs = mr.linrefs[k]
        if len(ivs) > 1 and shown < 8:  # only keys that actually vary along the road
            add(f"- `{k}`: " + " | ".join(f"[{s:.0f}–{e:.0f}m]={v!r}" for s, e, v in ivs[:6]))
            shown += 1

# ---------------- visual before/after examples (HTML) ----------------

HTML_OUT = "prototypes/output/bivalent_merge_examples.html"
PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2",
           "#ca8a04", "#db2777", "#4f46e5", "#65a30d", "#b91c1c", "#0d9488"]
ACCENT = "#0e7a8a"

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def val_color(v):
    return PALETTE[hash(str(v)) % len(PALETTE)]

def mr_points(mr):
    """Merged-road node coords in order, plus per-span point index ranges."""
    pts, spans_ix = [], []
    for wid, rev, _ in mr.spans:
        refs = ways[wid][1]
        seq = list(reversed(refs)) if rev else list(refs)
        start = len(pts)
        pts.extend(loc[r] for r in (seq if not pts else seq[1:]))
        spans_ix.append((max(0, start - 1) if start else 0, len(pts) - 1))
    return pts, spans_ix

def project(pts, w, h, pad=14):
    lats = [p[1] for p in pts]; lons = [p[0] for p in pts]
    clat = math.cos(math.radians(sum(lats) / len(lats)))
    xs = [p[0] * clat for p in pts]; ys = lats
    sx = (max(xs) - min(xs)) or 1e-9; sy = (max(ys) - min(ys)) or 1e-9
    k = min((w - 2 * pad) / sx, (h - 2 * pad) / sy)
    x0, y1 = min(xs), max(ys)
    return [((x - x0) * k + pad, (y1 - y) * k + pad) for x, y in zip(xs, ys)]

def svg_path(xy):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)

def geometry_svgs(mr, w=380, h=260):
    pts, spans_ix = mr_points(mr)
    xy = project(pts, w, h)
    before = []
    for i, ((a, b), (wid, rev, _)) in enumerate(zip(spans_ix, mr.spans)):
        c = PALETTE[i % len(PALETTE)]
        before.append(f'<path d="{svg_path(xy[a:b+1])}" fill="none" stroke="{c}" '
                      f'stroke-width="3.5" stroke-linecap="round"><title>way {wid}'
                      f'{" (reversed)" if rev else ""}</title></path>')
    for a, b in spans_ix[:-1]:  # join nodes between constituents
        x, y = xy[b]
        jlon, jlat = pts[b]
        before.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#fff" '
                      f'stroke="#111" stroke-width="1.6">'
                      f'<title>join at {jlat:.5f}, {jlon:.5f}</title></circle>')
    after = [f'<path d="{svg_path(xy)}" fill="none" stroke="{ACCENT}" '
             f'stroke-width="4" stroke-linecap="round"/>']
    for x, y in (xy[0], xy[-1]):
        after.append(f'<rect x="{x-4:.1f}" y="{y-4:.1f}" width="8" height="8" '
                     f'fill="#111"/>')
    wrap = lambda body: (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
                         f'<rect width="{w}" height="{h}" fill="#f8fafc"/>' +
                         "".join(body) + "</svg>")
    return wrap(before), wrap(after)

def ribbon(blocks, L, label, width=760, bh=26):
    """blocks: [(start_m, end_m, text, color)] -> one labelled ribbon row."""
    k = width / (L or 1)
    parts = [f'<div class="rl">{esc(label)}</div><svg width="{width}" height="{bh}">']
    for s, e, txt, c in blocks:
        x, bw = s * k, max((e - s) * k, 1.5)
        parts.append(f'<rect x="{x:.1f}" y="2" width="{bw:.1f}" height="{bh-8}" '
                     f'fill="{c}" fill-opacity="0.75" stroke="#fff" stroke-width="1">'
                     f'<title>[{s:.0f}-{e:.0f} m] {esc(txt)}</title></rect>')
        if bw > 34:
            parts.append(f'<text x="{x+3:.1f}" y="{bh-12}" font-size="10" '
                         f'fill="#111">{esc(str(txt)[:int(bw/6)])}</text>')
    parts.append("</svg>")
    return '<div class="row">' + "".join(parts) + "</div>"

def linfunc_chart(mr, key, width=760, h=110):
    """Before: raw per-way values placed on the merged axis (reversed ways keep
    their stored sign/offsets -> visible mismatch). After: normalized run."""
    segs_before, segs_after = [], []
    for i, (wid, rev, span) in enumerate(mr.spans):
        v = ways[wid][0].get(key)
        es = linvalue_ok(v) if v else None
        if not es:
            continue
        Lcm = entry_end(es[-1])
        pts = [(span[1] - a / 100 if rev else span[0] + a / 100, val)
               for k2, a, b, val in es if k2 == "pt" and val is not None]
        if rev:
            pts.reverse()
        if len(pts) >= 2:
            segs_before.append((PALETTE[i % len(PALETTE)], pts))
    for run in mr.linfuncs.get(key, []):
        pts = [(run["start_m"] + a / 100, val) for k2, a, b, val in run["entries"]
               if k2 == "pt" and val is not None]
        if len(pts) >= 2:
            segs_after.append((ACCENT, pts))
    allv = [v for _, pts in segs_before + segs_after for _, v in pts]
    if not allv:
        return ""
    vmin, vmax = min(allv + [0]), max(allv + [0])
    rng = (vmax - vmin) or 1
    kx, pad = width / (mr.length or 1), 6
    def draw(segs, title):
        out = [f'<svg width="{width}" height="{h}">'
               f'<rect width="{width}" height="{h}" fill="#f8fafc"/>']
        y0 = pad + (vmax - 0) / rng * (h - 2 * pad)
        out.append(f'<line x1="0" y1="{y0:.1f}" x2="{width}" y2="{y0:.1f}" '
                   f'stroke="#cbd5e1" stroke-dasharray="3,3"/>')
        for c, pts in segs:
            xy = [(x * kx, pad + (vmax - v) / rng * (h - 2 * pad)) for x, v in pts]
            out.append(f'<path d="{svg_path(xy)}" fill="none" stroke="{c}" '
                       f'stroke-width="2"/>')
        out.append(f'<text x="4" y="12" font-size="11" fill="#475569">{title} '
                   f'(y: {vmin}..{vmax})</text></svg>')
        return "".join(out)
    nrev = sum(1 for _, rev, _ in mr.spans if rev)
    # full-width, stacked, shared x-scale — side-by-side would overflow the page
    return ("<div>" +
            draw(segs_before, f"before: raw per-way values — {nrev}/{len(mr.spans)} "
                 "ways stored against the merged direction, their raw values negated") +
            draw(segs_after, "after: one normalized function on the merged road") +
            "</div>")

def maplink(lon, lat, label):
    return (f'<a href="https://www.openstreetmap.org/?mlat={lat:.6f}&amp;'
            f'mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}" target="_blank">'
            f'{esc(label)}: {lat:.5f}, {lon:.5f}</a>')

def example_html(mr, title, note):
    gb, ga = geometry_svgs(mr)
    pts, _ = mr_points(mr)
    mid = pts[len(pts) // 2]
    out = [f"<section><h2>{esc(title)}</h2><p>{esc(note)}</p>",
           "<p class='meta'>GPS: " + " · ".join((
               maplink(*pts[0], "start"), maplink(*mid, "middle"),
               maplink(*pts[-1], "end"))) + "</p>",
           f"<p class='meta'>{len(mr.chain)} ways, {mr.length:.0f} m, "
           f"{sum(1 for _, r, _ in mr.spans if r)} stored reversed. Ways: "
           f"{', '.join(str(w) for w, _ in mr.chain)}</p>",
           "<div class='pair'><div><h3>Before: "
           f"{len(mr.chain)} separate ways</h3>{gb}</div>",
           f"<div><h3>After: one merged road</h3>{ga}</div></div>"]
    # attribution ribbons: before (per-way blocks) vs after (merged intervals)
    keys = [k for k in sorted(mr.linrefs)
            if tag_class(k) == "attribution" and not is_way_relative(k)
            and len(mr.linrefs[k]) > 1][:5]
    if keys:
        out.append("<h3>Attribution — before: one value per way</h3>")
        for k in keys:
            blocks = []
            for wid, rev, (s, e) in mr.spans:
                v = ways[wid][0].get(k, "—")
                blocks.append((s, e, v, val_color(v) if v != "—" else "#e2e8f0"))
            out.append(ribbon(blocks, mr.length, k))
        out.append("<h3>Attribution — after: linear references on the merged road</h3>")
        for k in keys:
            blocks = [(s, e, v, val_color(v)) for s, e, v in mr.linrefs[k]]
            out.append(ribbon(blocks, mr.length, k))
    for key in LINFUNC_KEYS:
        chart = linfunc_chart(mr, key)
        if chart and mr.linfuncs.get(key):
            out.append(f"<h3><code>{key}</code> — re-based into merged-road space</h3>")
            out.append(chart)
    for nkey in ("house_numbers:range:left", "house_numbers:range:right"):
        raw = [(s, e, ways[wid][0].get(nkey if not rev else swap_lr(nkey)))
               for wid, rev, (s, e) in mr.spans]
        if sum(1 for _, _, v in raw if v) < 2:
            continue
        out.append(f"<h3><code>{esc(nkey)}</code> — direction-normalized, kept as "
                   "adjacent blocks (never merged: placement is real information)</h3>")
        blocks = [(s, e, v, val_color(v)) for s, e, v in raw if v]
        out.append(ribbon(blocks, mr.length, "before (raw per way)"))
        blocks = [(s, e, v, val_color(v)) for s, e, v in mr.linrefs.get(nkey, [])]
        out.append(ribbon(blocks, mr.length, "after (normalized blocks)"))
    out.append("</section>")
    return "".join(out)

def pick_examples():
    ex, used = [], set()
    def take(keyfn, title, note):
        m = max((x for x in mrs if id(x) not in used), key=keyfn)
        used.add(id(m))
        ex.append((m, title, note))
    def grad_score(m):
        runs = m.linfuncs.get("gradient:linear", [])
        return max((len(r["spans"]) for r in runs), default=0) + \
            2 * any(rev for _, rev, _ in m.spans)
    take(grad_score,
         "Gradient re-basing across a long chain",
         "Before: each way stores gradient with its own cm offsets and its own "
         "direction (reversed ways carry negated values — visible as mirrored "
         "segments). After: one continuous function over the merged road.")
    def range_score(m):
        return sum(1 for k in ("house_numbers:range:left", "house_numbers:range:right")
                   for _, _, v in m.linrefs.get(k, []) if v)
    take(range_score,
         "House-number blocks stay adjacent",
         "from|to ranges are direction-normalized (from/to follow travel "
         "direction) but deliberately NOT merged: each block's placement is "
         "real spatial information, so the blocks sit side by side on the "
         "merged road.")
    take(lambda m: len(m.chain),
         "Heaviest sectioning in the clip",
         "The most-sectioned merged road: many tiny ways collapse into one "
         "road whose partial attribution becomes offset intervals.")
    def var_score(m):
        return sum(1 for k in ("name", "lanes", "bridge", "layer", "maxspeed", "oneway")
                   if len(m.linrefs.get(k, [])) > 1)
    take(var_score,
         "Attribution variation carried by linear references",
         "Name/lanes/bridge/layer change mid-road; sectioning carried this "
         "before, offset intervals carry it after.")
    return ex

html = ["""<meta charset="utf-8">
<title>Bivalent merge — before/after examples</title>
<style>
body{font-family:system-ui,sans-serif;max-width:840px;margin:24px auto;padding:0 16px;color:#0f172a}
h1{font-size:1.4em} h2{font-size:1.15em;margin-top:2em;border-top:2px solid #e2e8f0;padding-top:1em}
h3{font-size:.95em;color:#334155;margin:1.2em 0 .4em}
p{line-height:1.5} .meta{font-size:.8em;color:#64748b;word-break:break-all}
.pair{display:flex;gap:16px;flex-wrap:wrap} .pair>div{flex:1;min-width:340px}
.row{display:flex;align-items:center;gap:8px;margin:2px 0}
.rl{width:190px;font-size:.75em;font-family:monospace;text-align:right;color:#334155}
code{background:#f1f5f9;padding:1px 4px;border-radius:3px}
svg{display:block}
details{margin:6px 0} summary{cursor:pointer;font-size:.85em}
table{border-collapse:collapse;font-size:.75em;margin:6px 0;width:100%}
th,td{border:1px solid #e2e8f0;padding:2px 6px;text-align:left;vertical-align:top;word-break:break-all}
tr.ign td{color:#94a3b8} tr.ign code{background:none;color:#94a3b8}
.chip{font-size:.85em;padding:0 5px;border-radius:8px;white-space:nowrap}
.chip.ign{background:#f1f5f9;color:#94a3b8}
.chip.wrel{background:#e0f2f1;color:#0e7a8a}
</style>
<h1>Bivalent merge + linear referencing — before/after examples</h1>
<p><b>Question (issues #8, #15):</b> does merging Orbis road ways across bivalent
nodes, with tags re-expressed as linear references and way-relative values
re-based into merged-road space, preserve all information while erasing
sectioning? Each example shows the same road <b>before</b> (as stored: many ways,
one value per way) and <b>after</b> (canonical: one merged road, offset-based
attribution). Circles = bivalent join nodes erased by the merge; squares = real
endpoints (junctions/dead ends). Hover any block for exact values.</p>
<p class="meta">Clip: """ + esc(CLIP) + """ — generated by prototypes/bivalent_merge_prototype.py (throwaway prototype)</p>"""]
def appendix_html(mr, title):
    out = [f"<section><h2>Appendix — {esc(title)}</h2>",
           "<p>Complete tag set of every constituent way, exactly as stored. "
           "Rows marked <span class='chip ign'>ignored</span> are excluded from "
           "canonical-model comparison as identity/housekeeping/metadata — every "
           "change to them is noise by construction, so check nothing real hides "
           "there. Unmarked rows are attribution; "
           "<span class='chip wrel'>way-relative</span> attribution is re-based "
           "into merged-road space.</p>"]
    rows_open = 0
    for wid, rev, (s, e) in mr.spans:
        tags = ways[wid][0]
        n_attr = sum(1 for k in tags if tag_class(k) == "attribution")
        rows = []
        order = {"attribution": 0, "identity": 1, "meta": 2}
        for k in sorted(tags, key=lambda k: (order[tag_class(k)], k)):
            cls = tag_class(k)
            if cls == "attribution":
                chip = ("<span class='chip wrel'>way-relative</span>"
                        if is_way_relative(k) else "")
                rows.append(f"<tr><td><code>{esc(k)}</code></td>"
                            f"<td>{esc(tags[k])}</td><td>{chip}</td></tr>")
            else:
                label = "ignored — identity" if cls == "identity" else "ignored — metadata"
                rows.append(f"<tr class='ign'><td><code>{esc(k)}</code></td>"
                            f"<td>{esc(tags[k])}</td>"
                            f"<td><span class='chip ign'>{label}</span></td></tr>")
        out.append(
            f"<details{' open' if not rows_open else ''}><summary>way <b>{wid}</b> — span {s:.0f}–{e:.0f} m"
            f"{', stored reversed' if rev else ''} · {n_attr} attribution + "
            f"{len(tags) - n_attr} ignored tags</summary>"
            "<table><tr><th>key</th><th>value</th><th></th></tr>" +
            "".join(rows) + "</table></details>")
        rows_open += 1
    out.append("</section>")
    return "".join(out)

examples = pick_examples()
for m, t, n in examples:
    html.append(example_html(m, t, n))
html.append("<h1>Appendix: complete per-way tag sets</h1>")
for m, t, n in examples:
    html.append(appendix_html(m, t))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(HTML_OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(html) + "\n")
add(f"\n## 7. Visual examples\nBefore/after geometry + attribution: `{HTML_OUT}`")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("\n".join(lines[:40]))
print(f"\nfull report: {OUT}\nvisual examples: {HTML_OUT}")
