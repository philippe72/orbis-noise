"""PROBE 6 - THROWAWAY (issue #12). The four cases where candidate C's rule fires.

Probe 5 (corrected) found that over the whole of NLD, 26320 -> 26340, the rule
"union preserved (<= 0.10 m) AND attribution identical -> noise" fires on 3 of
25 geometric splits and 1 of 27 geometric merges. Four cases decide whether
candidate C earns its extra rule, so this probe prints them in full: every
footprint, its ring, its area, its attribution, and the deviation - so a human
can look at each one and say noise or real.
"""
import math

import osmium
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

DEL = "data/buildings/deleted_26320.osm.pbf"
CRE = "data/buildings/created_26340.osm.pbf"
BLD_KEYS = ("building", "building:part")
ID_KEYS = ("gers_identifier", "osm_identifier")
DEG_M = 111320.0
LAT0 = 52.125
KX = DEG_M * math.cos(math.radians(LAT0))
COVER = 0.60
OVERLAP_MIN = 0.10
T_ROUND_M = 0.10


def attr(t):
    skip = {"layer_id", "license", "license_zone", "supported", "data_size_index"}
    return {k: v for k, v in t.items()
            if k not in skip and k not in ID_KEYS
            and not k.startswith("layer_id:") and not k.startswith("license:")}


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}
        self.loc = {}

    def node(self, n):
        self.loc[n.id] = (n.location.lon, n.location.lat)

    def way(self, w):
        t = {x.k: x.v for x in w.tags}
        if any(k in t for k in BLD_KEYS):
            self.ways[w.id] = (t, [n.ref for n in w.nodes])


def build(path):
    c = Collector()
    c.apply_file(path)
    polys, ids, tags, lls = [], [], [], []
    for wid, (t, refs) in c.ways.items():
        ll = [c.loc[r] for r in refs if r in c.loc]
        if len(ll) < 3:
            continue
        if ll[0] != ll[-1]:
            ll.append(ll[0])
        pts = [(lon * KX, lat * DEG_M) for lon, lat in ll]
        p = Polygon(pts)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.area <= 0:
            continue
        polys.append(p)
        ids.append(wid)
        tags.append(t)
        lls.append(ll)
    return polys, ids, tags, lls


DP, DI, DT, DL = build(DEL)
CP, CI, CT, CL = build(CRE)
ctree, dtree = STRtree(CP), STRtree(DP)


def counterparts(p, tree, others):
    out = []
    for j in tree.query(p):
        q = others[j]
        inter = p.intersection(q).area
        if inter > 0 and inter / min(p.area, q.area) >= OVERLAP_MIN:
            out.append(j)
    return out


def show(label, i, js, srcP, srcI, srcT, srcL, dstP, dstI, dstT, dstL):
    u = unary_union([dstP[j] for j in js])
    dev = max(srcP[i].boundary.hausdorff_distance(u.boundary),
              u.boundary.hausdorff_distance(srcP[i].boundary))
    cen = srcP[i].centroid
    print(f"=== {label}: w{srcI[i]} 1:{len(js)} "
          f"deviation {dev:.3f} m  at {cen.x / KX:.6f},{cen.y / DEG_M:.6f}")
    print(f"  https://www.openstreetmap.org/#map=19/{cen.y / DEG_M:.6f}/{cen.x / KX:.6f}")
    print(f"  source   w{srcI[i]} area={srcP[i].area:.1f} m2 nodes={len(srcL[i]) - 1} "
          f"attr={attr(srcT[i])}")
    print(f"           ids={ {k: v for k, v in srcT[i].items() if k in ID_KEYS} }")
    tot = 0.0
    for j in js:
        tot += dstP[j].area
        print(f"  counter  w{dstI[j]} area={dstP[j].area:.1f} m2 nodes={len(dstL[j]) - 1} "
              f"attr={attr(dstT[j])}")
        print(f"           ids={ {k: v for k, v in dstT[j].items() if k in ID_KEYS} }")
    print(f"  areas: source {srcP[i].area:.1f} m2 vs counterparts total {tot:.1f} m2 "
          f"(union {u.area:.1f} m2); overlap of the two unions "
          f"{srcP[i].intersection(u).area / max(srcP[i].area, u.area):.4f}")
    print(f"  source ring : {[[round(x, 7), round(y, 7)] for x, y in srcL[i]]}")
    for j in js:
        print(f"  counter ring w{dstI[j]}: "
              f"{[[round(x, 7), round(y, 7)] for x, y in dstL[j]]}")
    print()


def attr_key(t):
    return tuple(sorted(attr(t).items()))


fired = 0
for i, p in enumerate(DP):
    js = counterparts(p, ctree, CP)
    if len(js) < 2:
        continue
    u = unary_union([CP[j] for j in js])
    if u.intersection(p).area / p.area < COVER:
        continue
    if not all(attr_key(CT[j]) == attr_key(DT[i]) for j in js):
        continue
    dev = max(p.boundary.hausdorff_distance(u.boundary),
              u.boundary.hausdorff_distance(p.boundary))
    if dev <= T_ROUND_M:
        fired += 1
        show("SPLIT", i, js, DP, DI, DT, DL, CP, CI, CT, CL)

for j, q in enumerate(CP):
    isx = counterparts(q, dtree, DP)
    if len(isx) < 2:
        continue
    u = unary_union([DP[i] for i in isx])
    if u.intersection(q).area / q.area < COVER:
        continue
    if not all(attr_key(DT[i]) == attr_key(CT[j]) for i in isx):
        continue
    dev = max(q.boundary.hausdorff_distance(u.boundary),
              u.boundary.hausdorff_distance(q.boundary))
    if dev <= T_ROUND_M:
        fired += 1
        show("MERGE", j, isx, CP, CI, CT, CL, DP, DI, DT, DL)

print(f"total cases where candidate C's rule fires: {fired}")
