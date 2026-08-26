"""PROBE 4 - THROWAWAY (issue #12). Does dissolving footprints destroy real information?

Probe 2 found the dissolve candidate collapses 152,387 Amersfoort footprints into
70,671 blobs: 110,186 footprints (72%) land in a multi-footprint blob, the largest
holding 55. Dutch row houses share walls and share attribution (building=yes and
nothing else), so the union is one polygon.

The test that decides it: does each footprint in a blob carry its own address?
If yes, a blob is many real buildings and dissolving them is information loss,
not noise removal. Address points are separate elements in Orbis, so this probe
counts distinct addresses falling inside each blob.
"""
import collections
import osmium
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

CLIP = "data/clips/amersfoort_26340.osm.pbf"
BLD_KEYS = ("building", "building:part")
ADDR_KEYS = ("addr:housenumber", "addr:street", "housenumber", "address")
IGNORE = {"layer_id", "license", "license_zone", "supported", "data_size_index",
          "gers_identifier", "osm_identifier"}


def is_ignored(k):
    return k in IGNORE or k.startswith("layer_id:") or k.startswith("license:")


def attr_of(t):
    return tuple(sorted((k, v) for k, v in t.items() if not is_ignored(k)))


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}
        self.addr = {}       # address-ish nodes: id -> (tags, lon, lat)
        self.addrkeys = collections.Counter()

    def node(self, n):
        t = {x.k: x.v for x in n.tags}
        if not t:
            return
        if any(k in t for k in ADDR_KEYS) or any(k.startswith("addr") for k in t):
            self.addr[n.id] = (t, n.location.lon, n.location.lat)
            for k in t:
                self.addrkeys[k] += 1

    def way(self, w):
        t = {x.k: x.v for x in w.tags}
        if any(k in t for k in BLD_KEYS):
            self.ways[w.id] = (t, [n.ref for n in w.nodes])


class LocFill(osmium.SimpleHandler):
    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.loc = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)


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


c = Collector()
c.apply_file(CLIP)
needed = set()
for _, refs in c.ways.values():
    needed.update(refs)
lf = LocFill(needed)
lf.apply_file(CLIP)
loc = lf.loc
W = c.ways
print(f"[26340] {len(W)} building ways, {len(loc)} locations, {len(c.addr)} address-ish nodes")
print("  address-node tag keys:", dict(c.addrkeys.most_common(14)))
print()

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


def union_(x, y):
    rx, ry = find(x), find(y)
    if rx != ry:
        parent[ry] = rx


for i, p in enumerate(polys):
    for j in tree.query(p):
        if j <= i:
            continue
        if p.intersects(polys[j]) and attr_of(W[ids[i]][0]) == attr_of(W[ids[j]][0]):
            union_(ids[i], ids[j])

comp = collections.defaultdict(list)
idx_of = {w: i for i, w in enumerate(ids)}
for w in ids:
    comp[find(w)].append(w)
multi = [v for v in comp.values() if len(v) > 1]
print(f"dissolved regions: {len(comp)} total, {len(multi)} multi-footprint")
print()

# address points per footprint and per blob
apts = [Point(lon, lat) for _, lon, lat in c.addr.values()]
atags = [t for t, _, _ in c.addr.values()]
atree = STRtree(apts)


def addr_labels(p):
    out = set()
    for i in atree.query(p):
        if p.covers(apts[i]):
            t = atags[i]
            lab = (t.get("addr:street", t.get("street", "")),
                   t.get("addr:housenumber", t.get("housenumber", "")),
                   t.get("addr:housenumber:suffix", ""))
            out.add(lab)
    return out


sample = sorted(multi, key=len, reverse=True)[:200]
rows = []
for v in sample:
    per_fp = {}
    for w in v:
        per_fp[w] = addr_labels(polys[idx_of[w]])
    blob_addrs = set().union(*per_fp.values()) if per_fp else set()
    with_addr = sum(1 for s in per_fp.values() if s)
    rows.append((len(v), with_addr, len(blob_addrs)))

print("--- 200 largest blobs: footprints vs distinct addresses inside them ---")
print("  blob_size  footprints_with_own_address  distinct_addresses_in_blob")
for n, wa, na in rows[:20]:
    print(f"   {n:9d}  {wa:27d}  {na:26d}")
tot_fp = sum(n for n, _, _ in rows)
tot_wa = sum(wa for _, wa, _ in rows)
tot_na = sum(na for _, _, na in rows)
print(f"  totals over 200 blobs: footprints={tot_fp} with own address={tot_wa} "
      f"distinct addresses={tot_na}")
multi_addr = sum(1 for n, wa, na in rows if na > 1)
print(f"  blobs holding more than one distinct address: {multi_addr}/200")

# same census over a random-ish spread of all multi blobs, not just the largest
allrows = []
for v in multi[::37]:
    per_fp = {w: addr_labels(polys[idx_of[w]]) for w in v}
    blob_addrs = set().union(*per_fp.values()) if per_fp else set()
    allrows.append((len(v), sum(1 for s in per_fp.values() if s), len(blob_addrs)))
print()
print(f"--- every 37th multi-footprint blob ({len(allrows)} blobs) ---")
print(f"  footprints={sum(n for n,_,_ in allrows)} "
      f"with own address={sum(wa for _,wa,_ in allrows)} "
      f"distinct addresses={sum(na for _,_,na in allrows)}")
print(f"  blobs holding >1 distinct address: "
      f"{sum(1 for n,wa,na in allrows if na > 1)}/{len(allrows)}")
print(f"  blobs where distinct addresses == footprints: "
      f"{sum(1 for n,wa,na in allrows if na == n)}/{len(allrows)}")

# a concrete named example for the human
big = sorted(multi, key=len, reverse=True)[0]
print()
print(f"--- largest blob ({len(big)} footprints) in detail ---")
u = unary_union([polys[idx_of[w]] for w in big])
print(f"  union geom_type={u.geom_type}  centroid={u.centroid.x:.5f},{u.centroid.y:.5f}")
print(f"  attribution shared by all: {dict(attr_of(W[big[0]][0]))}")
seen = []
for w in big[:12]:
    la = addr_labels(polys[idx_of[w]])
    seen.append((w, sorted(la)))
for w, la in seen:
    print(f"    w{w}  addresses={la}")
