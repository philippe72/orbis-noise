"""PROBE 2 - THROWAWAY (issue #12). Structure of building storage in the clips.

Probe 1 (building_survey_probe.py) found: 152,387 building ways, 0 building
nodes, gers_identifier 99.73% unique, ref:bag ABSENT, and almost no churn
(26320->26340: 15 created / 26 deleted / 2 modified).

This probe asks what a candidate unit would have to group:
 - osm_identifier duplicate groups (149,888 distinct over 150,247 present):
   is a shared osm_identifier the split signature? do members touch?
 - building_group tag (303 ways): an explicit Orbis grouping?
 - building:part (646 ways): parts of what?
 - type=building (427) and type=multipolygon (783) relations: what do they hold?
 - touching footprints with identical attribution: the dissolve candidate's
   own target - how big do the blobs get?
"""
import collections
import osmium
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

CLIPS = {"26340": "data/clips/amersfoort_26340.osm.pbf"}
BLD_KEYS = ("building", "building:part")
IGNORE = {"layer_id", "license", "license_zone", "supported", "data_size_index",
          "gers_identifier", "osm_identifier"}


def is_ignored(k):
    return k in IGNORE or k.startswith("layer_id:") or k.startswith("license:")


def attr_dict(t):
    return {k: v for k, v in t.items() if not is_ignored(k)}


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}      # building ways: id -> (tags, refs)
        self.way_refs = {}  # ALL ways: id -> refs (relation member geometry)
        self.rels = {}      # id -> (tags, members)

    def way(self, w):
        refs = [n.ref for n in w.nodes]
        self.way_refs[w.id] = refs
        t = {x.k: x.v for x in w.tags}
        if any(k in t for k in BLD_KEYS):
            self.ways[w.id] = (t, refs)

    def relation(self, r):
        t = {x.k: x.v for x in r.tags}
        if any(k in t for k in BLD_KEYS) or t.get("type") in ("building", "multipolygon"):
            self.rels[r.id] = (t, [(m.type, m.ref, m.role) for m in r.members])


class LocFill(osmium.SimpleHandler):
    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.loc = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)


def load(path):
    c = Collector()
    c.apply_file(path)
    needed = set()
    for _, refs in c.ways.values():
        needed.update(refs)
    for t, mem in c.rels.values():
        for ty, ref, _ in mem:
            if ty == "w":
                needed.update(c.way_refs.get(ref, ()))
            elif ty == "n":
                needed.add(ref)
    lf = LocFill(needed)
    lf.apply_file(path)
    return c, lf.loc


def poly(refs, loc):
    pts = [loc[r] for r in refs if r in loc]
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    try:
        p = Polygon(pts)
        if p.is_empty:
            return None
        return p if p.is_valid else p.buffer(0)
    except Exception:
        return None


c, loc = load(CLIPS["26340"])
print(f"[26340] loaded {len(c.ways)} building ways, {len(loc)} node locations", flush=True)
W = c.ways
print()

# ---- 1. osm_identifier duplicate groups ----
byosm = collections.defaultdict(list)
for wid, (t, refs) in W.items():
    if "osm_identifier" in t:
        byosm[t["osm_identifier"]].append(wid)
dups = {k: v for k, v in byosm.items() if len(v) > 1}
print(f"--- osm_identifier groups with >1 footprint: {len(dups)} groups, "
      f"{sum(len(v) for v in dups.values())} ways ---")
print("  group sizes:", dict(collections.Counter(len(v) for v in dups.values())))
touch = same_attr = 0
for k, v in dups.items():
    ps = [poly(W[w][1], loc) for w in v]
    ps = [p for p in ps if p is not None]
    if len(ps) < 2:
        continue
    u = unary_union(ps)
    if getattr(u, "geom_type", "") == "Polygon":
        touch += 1
    attrs = {tuple(sorted(attr_dict(W[w][0]).items())) for w in v}
    if len(attrs) == 1:
        same_attr += 1
print(f"  of those groups: union is a single polygon (members touch) = {touch}")
print(f"                   all members attribution-identical        = {same_attr}")
for k, v in list(dups.items())[:4]:
    print(f"  e.g. osm_identifier={k} ways={v}")
    for w in v:
        t = W[w][0]
        print(f"       w{w} gers={t.get('gers_identifier', '-')} attr={attr_dict(t)}")

print()
# ---- 2. building_group ----
bg = collections.defaultdict(list)
for wid, (t, refs) in W.items():
    if "building_group" in t:
        bg[t["building_group"]].append(wid)
print(f"--- building_group: {sum(len(v) for v in bg.values())} ways in {len(bg)} distinct values ---")
print("  value-group sizes:", dict(collections.Counter(len(v) for v in bg.values())))
for k, v in list(bg.items())[:5]:
    print(f"  building_group={k} -> {len(v)} ways {v[:6]}")

print()
# ---- 3. building:part ----
bp = [wid for wid, (t, _) in W.items() if "building:part" in t]
print(f"--- building:part: {len(bp)} ways; also carry building=* : "
      f"{sum(1 for w in bp if 'building' in W[w][0])} ---")
for w in bp[:3]:
    print(f"  w{w} attr={attr_dict(W[w][0])}")
nonpart = [wid for wid, (t, _) in W.items() if "building:part" not in t]
np_polys, np_ids = [], []
for wid in nonpart:
    p = poly(W[wid][1], loc)
    if p is not None:
        np_polys.append(p)
        np_ids.append(wid)
np_tree = STRtree(np_polys)
inside = 0
for w in bp:
    p = poly(W[w][1], loc)
    if p is None:
        continue
    for i in np_tree.query(p):
        if np_polys[i].contains(p.buffer(-1e-9)):
            inside += 1
            break
print(f"  building:part ways contained in a plain building footprint: {inside}/{len(bp)}")

print()
# ---- 4. relations ----
print("--- relations carrying building / type=building / type=multipolygon ---")
for want in ("building", "multipolygon"):
    sel = {rid: (t, m) for rid, (t, m) in c.rels.items() if t.get("type") == want}
    print(f"  type={want}: {len(sel)}")
    roles = collections.Counter(r for _, m in sel.values() for _, _, r in m)
    print(f"    member roles: {dict(roles.most_common(8))}")
    memtypes = collections.Counter(ty for _, m in sel.values() for ty, _, _ in m)
    print(f"    member types: {dict(memtypes)}")
    print(f"    with gers_identifier: {sum(1 for t, _ in sel.values() if 'gers_identifier' in t)}")
    inbld = sum(1 for _, m in sel.values() for ty, ref, _ in m if ty == "w" and ref in W)
    allmem = sum(len(m) for _, m in sel.values())
    print(f"    members that are building ways: {inbld}/{allmem}")
    tagkeys = collections.Counter(k for t, _ in sel.values() for k in t if not is_ignored(k))
    print(f"    attribution keys: {dict(tagkeys.most_common(10))}")
    for rid, (t, m) in list(sel.items())[:3]:
        print(f"    r{rid} tags={attr_dict(t)} members={m[:5]}")

print()
# ---- 5. touching + attribution-identical census (the dissolve candidate) ----
polys, ids = [], []
for wid, (t, refs) in W.items():
    p = poly(refs, loc)
    if p is not None:
        polys.append(p)
        ids.append(wid)
tree = STRtree(polys)
parent = {w: w for w in ids}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union_(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra


def attr_of(w):
    return tuple(sorted(attr_dict(W[w][0]).items()))


pairs = 0
for i, p in enumerate(polys):
    for j in tree.query(p):
        if j <= i:
            continue
        if p.intersects(polys[j]) and attr_of(ids[i]) == attr_of(ids[j]):
            union_(ids[i], ids[j])
            pairs += 1
comp = collections.defaultdict(list)
for w in ids:
    comp[find(w)].append(w)
sizes = collections.Counter(len(v) for v in comp.values())
multi = {k: v for k, v in comp.items() if len(v) > 1}
print("--- dissolve census: touching + attribution-identical ---")
print(f"  touching attribution-identical pairs: {pairs}")
print(f"  {len(polys)} footprints -> {len(comp)} dissolved regions "
      f"({len(multi)} of them multi-footprint, covering "
      f"{sum(len(v) for v in multi.values())} footprints)")
print(f"  blob size distribution: {dict(sorted(sizes.items())[:15])}")
print(f"  largest blob: {max(len(v) for v in comp.values())} footprints")
for v in sorted(multi.values(), key=len, reverse=True)[:3]:
    print(f"    blob of {len(v)}: {v[:10]}")
