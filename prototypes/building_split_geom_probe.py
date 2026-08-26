"""PROBE 5 - THROWAWAY (issue #12). Do buildings split or merge with FRESH nodes?

Probe 3 tested split/merge NLD-wide by shared node refs and found zero: every
node-ref-linked component over the 32,848 changed footprints is 1:1, 0:1 or 1:0.
That test only sees a split that RE-USES the parent's nodes. A split re-drawn
from scratch would show up as 1 deleted + several created footprints with no
node in common, and probe 3 counted 8,729 such (1,0) and 13,919 (0,1)
components.

This probe closes that gap with coordinates. It loads the 2,500 deleted
footprints from 26320 and the 7,690 created footprints from 26340 (pulled with
osmium getid -r) and asks, purely geometrically:

  split  - is one deleted footprint covered by two or more created ones?
  merge  - is one created footprint covered by two or more deleted ones?
  replace- is one deleted footprint covered by exactly one created one?
           (that is a re-draw, not a re-partitioning)
"""
import collections
import math

import osmium
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

DEL = "data/buildings/deleted_26320.osm.pbf"
CRE = "data/buildings/created_26340.osm.pbf"
BLD_KEYS = ("building", "building:part")
DEG_M = 111320.0
COVER = 0.60          # a counterpart must cover this share of the footprint's area
OVERLAP_MIN = 0.10    # ignore incidental slivers below this share


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


def load(path):
    c = Collector()
    c.apply_file(path)
    return c


def build(c, lat0):
    kx = DEG_M * math.cos(math.radians(lat0))
    polys, ids, tags = [], [], []
    skipped = 0
    for wid, (t, refs) in c.ways.items():
        pts = [((c.loc[r][0]) * kx, (c.loc[r][1]) * DEG_M) for r in refs if r in c.loc]
        if len(pts) < 3:
            skipped += 1
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        try:
            p = Polygon(pts)
            if not p.is_valid:
                p = p.buffer(0)
            if p.is_empty or p.area <= 0:
                skipped += 1
                continue
        except Exception:
            skipped += 1
            continue
        polys.append(p)
        ids.append(wid)
        tags.append(t)
    return polys, ids, tags, skipped


cd = load(DEL)
cc = load(CRE)
lats = [v[1] for v in cd.loc.values()][:200000] or [52.0]
lat0 = sum(lats) / len(lats)
print(f"[deleted] ways={len(cd.ways)} nodes={len(cd.loc)}")
print(f"[created] ways={len(cc.ways)} nodes={len(cc.loc)}")
print(f"projection latitude {lat0:.3f} (NLD-wide; scale error is <1% over the country)")

DP, DI, DT, dskip = build(cd, lat0)
CP, CI, CT, cskip = build(cc, lat0)
print(f"usable polygons: deleted={len(DP)} (skipped {dskip}) created={len(CP)} (skipped {cskip})")
print()

ctree = STRtree(CP)
dtree = STRtree(DP)


def counterparts(p, tree, others):
    """Created/deleted polygons overlapping p by more than a sliver."""
    out = []
    for j in tree.query(p):
        q = others[j]
        inter = p.intersection(q).area
        if inter <= 0:
            continue
        if inter / min(p.area, q.area) >= OVERLAP_MIN:
            out.append((j, inter))
    return out


split_cands, replace, orphan_del = [], [], 0
for i, p in enumerate(DP):
    cps = counterparts(p, ctree, CP)
    if not cps:
        orphan_del += 1
        continue
    covered = unary_union([CP[j] for j, _ in cps]).intersection(p).area / p.area
    if len(cps) >= 2 and covered >= COVER:
        split_cands.append((i, [j for j, _ in cps], covered))
    elif len(cps) == 1 and covered >= COVER:
        replace.append((i, cps[0][0], covered))

merge_cands, orphan_cre = [], 0
for j, q in enumerate(CP):
    dps = counterparts(q, dtree, DP)
    if not dps:
        orphan_cre += 1
        continue
    covered = unary_union([DP[i] for i, _ in dps]).intersection(q).area / q.area
    if len(dps) >= 2 and covered >= COVER:
        merge_cands.append((j, [i for i, _ in dps], covered))

print("--- geometric re-partitioning over the created / deleted footprints ---")
print(f"  deleted footprints with NO overlapping created footprint : {orphan_del}/{len(DP)}")
print(f"  created footprints with NO overlapping deleted footprint : {orphan_cre}/{len(CP)}")
print(f"  1 deleted -> 1 created, >={COVER:.0%} covered (re-draw)   : {len(replace)}")
print(f"  1 deleted -> N created, >={COVER:.0%} covered (SPLIT)      : {len(split_cands)}")
print(f"  N deleted -> 1 created, >={COVER:.0%} covered (MERGE)      : {len(merge_cands)}")
print()


ID_KEYS = ("gers_identifier", "osm_identifier")


def attr(t):
    """Attribution per docs/tag-classification.md: identity and metadata excluded.
    NOTE: an earlier version of this probe left the identifier tags in, which made
    'attribution-identical' impossible by construction. That was wrong; the
    identifier tags are ignored-identity and must not take part."""
    skip = {"layer_id", "license", "license_zone", "supported", "data_size_index"}
    return {k: v for k, v in t.items()
            if k not in skip and k not in ID_KEYS
            and not k.startswith("layer_id:") and not k.startswith("license:")}


def lonlat(p):
    kx = DEG_M * math.cos(math.radians(lat0))
    c = p.centroid
    return (c.x / kx, c.y / DEG_M)


for label, cands, srcP, srcI, srcT, dstP, dstI, dstT in (
        ("SPLIT", split_cands, DP, DI, DT, CP, CI, CT),
        ("MERGE", merge_cands, CP, CI, CT, DP, DI, DT)):
    if not cands:
        continue
    print(f"--- {label} cases ({len(cands)}), largest first ---")
    for i, js, cov in sorted(cands, key=lambda x: -len(x[1]))[:12]:
        lo, la = lonlat(srcP[i])
        print(f"  w{srcI[i]} area={srcP[i].area:.0f} m2 at {lo:.5f},{la:.5f} "
              f"covered {cov:.0%} by {len(js)}:")
        print(f"    {attr(srcT[i])}")
        for j in js[:6]:
            print(f"    -> w{dstI[j]} area={dstP[j].area:.0f} m2 {attr(dstT[j])}")
    print()

# attribution comparison on the split/merge cases: union-preserving and identical?
def attr_key(t):
    return tuple(sorted(attr(t).items()))


T_ROUND_M = 0.10   # #9 rounding tier: union preserved within this is noise-grade

print()
print("--- would candidate C's rule ever fire? "
      "(union preserved AND attribution identical) ---")
for label, cands, srcP, srcT, dstP, dstT in (
        ("SPLIT", split_cands, DP, DT, CP, CT),
        ("MERGE", merge_cands, CP, CT, DP, DT)):
    if not cands:
        print(f"  {label}: none, so nothing to classify")
        continue
    same_attr, dev, dev_same = 0, [], []
    fires = 0
    for i, js, cov in cands:
        identical = all(attr_key(dstT[j]) == attr_key(srcT[i]) for j in js)
        u = unary_union([dstP[j] for j in js])
        d = max(srcP[i].boundary.hausdorff_distance(u.boundary),
                u.boundary.hausdorff_distance(srcP[i].boundary))
        dev.append(d)
        if identical:
            same_attr += 1
            dev_same.append(d)
            if d <= T_ROUND_M:
                fires += 1
    dev.sort()
    dev_same.sort()
    print(f"  {label}: {len(cands)} cases; attribution-identical on every side "
          f"{same_attr}/{len(cands)}")
    print(f"    union deviation, all cases : median={dev[len(dev)//2]:.2f} m "
          f"min={dev[0]:.2f} m max={dev[-1]:.2f} m")
    if dev_same:
        print(f"    union deviation, attribution-identical cases: "
              f"median={dev_same[len(dev_same)//2]:.2f} m min={dev_same[0]:.2f} m "
              f"max={dev_same[-1]:.2f} m")
    print(f"    candidate C rule fires (deviation <= {T_ROUND_M:g} m AND "
          f"attribution identical): {fires}")

print()
print("--- would identity-first matching (#7) even form these N:M groups? ---")
for label, cands, srcP, srcI, srcT, dstP, dstI, dstT in (
        ("SPLIT", split_cands, DP, DI, DT, CP, CI, CT),
        ("MERGE", merge_cands, CP, CI, CT, DP, DI, DT)):
    if not cands:
        continue
    id_linked = iou_linked = 0
    for i, js, cov in cands:
        sids = {v for k, v in srcT[i].items() if k in ID_KEYS}
        if any(sids & {v for k, v in dstT[j].items() if k in ID_KEYS} for j in js):
            id_linked += 1
        best = max(srcP[i].intersection(dstP[j]).area /
                   srcP[i].union(dstP[j]).area for j in js)
        if best >= 0.30:
            iou_linked += 1
    print(f"  {label}: linked by a shared identifier {id_linked}/{len(cands)}; "
          f"best single-counterpart IoU >= 0.30 {iou_linked}/{len(cands)}")

# sanity: what do the orphan created footprints look like? (genuinely new buildings)
print()
print("--- sample of created footprints with no deleted counterpart (genuinely new) ---")
shown = 0
for j, q in enumerate(CP):
    if shown >= 6:
        break
    if counterparts(q, dtree, DP):
        continue
    lo, la = lonlat(q)
    print(f"  w{CI[j]} area={q.area:.0f} m2 at {lo:.5f},{la:.5f} {attr(CT[j])}")
    shown += 1

print()
tagged = collections.Counter()
for t in CT:
    tagged["building:part" if "building:part" in t else "building"] += 1
print(f"created footprint kinds: {dict(tagged)}")
tagged = collections.Counter()
for t in DT:
    tagged["building:part" if "building:part" in t else "building"] += 1
print(f"deleted footprint kinds: {dict(tagged)}")
