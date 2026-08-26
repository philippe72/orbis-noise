"""PROTOTYPE PROBE — verification of the #11 review claims (2026-08-26).

Re-derives every number quoted in the review of the drift-only ledger section.
The first pass used a star-shaped (non-transitive) grouping and a node-only,
exact-coordinate exchange detector; this one uses proper connected components,
covers POI ways as well as nodes, and tests two position tolerances.

Run: .venv-research/Scripts/python.exe prototypes/poi_review_probe.py
"""
import collections
import math
import sys

import osmium

sys.stdout.reconfigure(encoding="utf-8")

CLIP_A = "data/clips/amersfoort_26330.osm.pbf"
CLIP_B = "data/clips/amersfoort_26340.osm.pbf"
POI_KEYS = ("amenity", "shop", "tourism", "office", "craft", "healthcare",
            "emergency", "man_made", "historic", "sport")
ID_KEYS = ("gers_identifier", "osm_identifier", "source_identifier",
           "source_identifier:internal")
META = {"layer_id", "license", "license_zone", "supported", "data_size_index",
        "geometry_type", "zoomlevel_min"}
R50 = 50.0


def cls_of(t):
    for k in POI_KEYS:
        if k in t:
            return f"{k}={t[k]}"
    return None


def has_id(t):
    return any(k in t for k in ID_KEYS)


def attrib(t):
    return tuple(sorted((k, v) for k, v in t.items()
                        if k not in ID_KEYS and k not in META
                        and not k.startswith(("layer_id:", "license:"))))


def hav(lo1, la1, lo2, la2):
    R = 6371008.8
    p = math.radians
    return R * math.hypot(p(la2 - la1), p(lo2 - lo1) * math.cos(p((la1 + la2) / 2)))


def components(pts, radius):
    """pts: [(lon, lat)] -> list of index lists, transitive closure at radius."""
    n = len(pts)
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if hav(pts[i][0], pts[i][1], pts[j][0], pts[j][1]) <= radius:
                a, b = find(i), find(j)
                if a != b:
                    par[a] = b
    g = collections.defaultdict(list)
    for i in range(n):
        g[find(i)].append(i)
    return list(g.values())


class Load(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.pn = {}          # POI node id -> (tags, lon, lat)
        self.pw = {}          # POI way id -> (tags, refs)
        self.rel_type = collections.Counter()
        self.rel_poikey = collections.Counter()   # per type: POI class key on the relation
        self.chst = {}        # charging_station rel -> (tags, members)
        self.cheq = {}        # charging_equipment rel -> tags
        self.bld = {}         # building rel -> members
        self.racc = {}        # road_access rel -> members
        self.wcent = {}

    def node(self, n):
        t = dict(n.tags)
        if cls_of(t):
            self.pn[n.id] = (t, n.location.lon, n.location.lat)

    def way(self, w):
        t = dict(w.tags)
        if cls_of(t) and "highway" not in t:
            self.pw[w.id] = (t, [x.ref for x in w.nodes])

    def relation(self, r):
        t = dict(r.tags)
        ty = t.get("type", "<none>")
        self.rel_type[ty] += 1
        if cls_of(t):
            self.rel_poikey[ty] += 1
        ms = tuple((m.type, m.ref, m.role) for m in r.members)
        if ty == "charging_station":
            self.chst[r.id] = (t, ms)
        elif ty == "charging_equipment":
            self.cheq[r.id] = t
        elif ty == "building":
            self.bld[r.id] = ms
        elif ty == "road_access":
            self.racc[r.id] = ms


class Loc(osmium.SimpleHandler):
    def __init__(self, need):
        super().__init__()
        self.need = need
        self.loc = {}

    def node(self, n):
        if n.id in self.need:
            self.loc[n.id] = (n.location.lon, n.location.lat)


S = {}
for side, clip in (("A", CLIP_A), ("B", CLIP_B)):
    h = Load()
    h.apply_file(clip)
    need = set()
    for wid, (t, refs) in h.pw.items():
        need.update(refs)
    lf = Loc(need)
    lf.apply_file(clip)
    for wid, (t, refs) in h.pw.items():
        pts = [lf.loc[r] for r in refs if r in lf.loc]
        if pts:
            h.wcent[wid] = (sum(p[0] for p in pts) / len(pts),
                            sum(p[1] for p in pts) / len(pts))
    S[side] = h
    print(f"loaded {side}: poi nodes={len(h.pn)} poi ways={len(h.pw)} "
          f"rels={sum(h.rel_type.values())}", flush=True)
A, B = S["A"], S["B"]

print("\n===== V4/V6: does the relation type carry a POI class key on itself? =====")
for ty in ("charging_station", "charging_equipment", "building", "site", "is_same",
           "located_in", "road_access", "multipolygon"):
    print(f"  {ty:20} count={A.rel_type[ty]:7}  with a POI class key on the relation="
          f"{A.rel_poikey[ty]}")
ev = list(A.cheq.values())
print(f"  charging_equipment: with evse_id={sum(1 for t in ev if 'evse_id' in t)}/{len(ev)}"
      f"  distinct evse_id values={len({t.get('evse_id') for t in ev})}")
print(f"  charging_station:   with station_id="
      f"{sum(1 for t, _ in A.chst.values() if 'station_id' in t)}/{len(A.chst)}"
      f"  distinct station_id values={len({t.get('station_id') for t, _ in A.chst.values()})}")

print("\n===== V7: building relation members =====")
mk = collections.Counter()
roles = collections.Counter()
for rid, ms in A.bld.items():
    for mt, ref, role in ms:
        roles[role] += 1
        src = A.pn if mt == "n" else (A.pw if mt == "w" else None)
        mk["POI-class" if (src is not None and ref in src) else "not POI-class"] += 1
print(f"  relations={len(A.bld)} roles={dict(roles)} members={dict(mk)}")

print("\n===== V1: ambiguity census, transitive closure, both map versions =====")


def census(h):
    byc = collections.defaultdict(list)
    for nid, (t, lo, la) in h.pn.items():
        if not has_id(t):
            byc[cls_of(t)].append((nid, lo, la, attrib(t)))
    out = {}
    for c, items in byc.items():
        comps = [g for g in components([(i[1], i[2]) for i in items], R50) if len(g) > 1]
        out[c] = (len(items), comps, items)
    return out


def node_changed(nid, at, lo, la):
    """Did this baseline POI node change in the target map?"""
    if nid not in B.pn:
        return True
    t2, lo2, la2 = B.pn[nid]
    return (attrib(t2) != at or round(lo2, 7) != round(lo, 7)
            or round(la2, 7) != round(la, 7))


ca, cb = census(A), census(B)
rows = []
tot_nodes = tot_comps = tot_churn = 0
for c, (tot, comps, items) in ca.items():
    if not comps:
        continue
    nmem = sum(len(g) for g in comps)
    ident = sum(1 for g in comps if len({items[i][3] for i in g}) == 1)
    churn = sum(1 for g in comps
                if any(node_changed(items[i][0], items[i][3], items[i][1], items[i][2])
                       for i in g))
    rows.append((nmem, c, tot, len(comps), ident, churn))
    tot_nodes += nmem
    tot_comps += len(comps)
    tot_churn += churn
print(f"{'class':38} {'no-id':>6} {'comps':>6} {'members':>8} {'ident attr':>11} {'comps w/ churn':>14}")
for nmem, c, tot, ncomp, ident, churn in sorted(rows, reverse=True):
    print(f"{c:38} {tot:6} {ncomp:6} {nmem:8} {ident:11} {churn:14}")
print(f"  BASELINE: nodes in an ambiguous component={tot_nodes} components={tot_comps} "
      f"components containing an element change={tot_churn}")
print(f"  TARGET:   nodes in an ambiguous component="
      f"{sum(sum(len(g) for g in comps) for tot, comps, items in cb.values())} "
      f"components={sum(len(comps) for tot, comps, items in cb.values())}")

print("\n===== V2/V3/V9: exchange detector, nodes + POI way centroids =====")


def feats(h):
    out = {}
    for nid, (t, lo, la) in h.pn.items():
        out[("n", nid)] = (cls_of(t), lo, la)
    for wid, (t, refs) in h.pw.items():
        if wid in h.wcent:
            lo, la = h.wcent[wid]
            out[("w", wid)] = (cls_of(t), lo, la)
    return out


fa, fb = feats(A), feats(B)
for prec, label in ((7, "exact, 1e-7 deg"), (6, "about 11 cm")):
    def occ(f):
        d = collections.defaultdict(collections.Counter)
        for k, (c, lo, la) in f.items():
            d[(round(lo, prec), round(la, prec))][c] += 1
        return d
    oa, ob = occ(fa), occ(fb)
    empty = collections.Counter()
    diff = [p for p in set(oa) | set(ob) if oa.get(p, empty) != ob.get(p, empty)]
    comps = components(diff, 60.0)
    true_x, single = [], []
    for g in comps:
        ps = [diff[i] for i in g]
        ma = sum((oa.get(p, empty) for p in ps), collections.Counter())
        mb = sum((ob.get(p, empty) for p in ps), collections.Counter())
        if ma != mb:
            continue
        (true_x if sum(ma.values()) >= 2 else single).append((ps, ma))
    print(f"\n  -- tolerance {label}: differing positions={len(diff)} clusters={len(comps)}")
    print(f"     multiset unchanged, >=2 occupants (TRUE EXCHANGE): {len(true_x)}")
    for ps, ma in true_x:
        print(f"       {' + '.join(sorted(ma.elements()))}")
        for p in sorted(ps):
            ka = [k for k, v in fa.items() if (round(v[1], prec), round(v[2], prec)) == p]
            kb = [k for k, v in fb.items() if (round(v[1], prec), round(v[2], prec)) == p]
            print(f"         {p} A={[k[0] + str(k[1]) for k in ka]} "
                  f"B={[k[0] + str(k[1]) for k in kb]}")
    print(f"     multiset unchanged, 1 occupant: {len(single)}")
    for ps, ma in single:
        detail = []
        for p in sorted(ps):
            ka = [k for k, v in fa.items() if (round(v[1], prec), round(v[2], prec)) == p]
            kb = [k for k, v in fb.items() if (round(v[1], prec), round(v[2], prec)) == p]
            detail.append(f"{p} A={[k[0] + str(k[1]) for k in ka]} B={[k[0] + str(k[1]) for k in kb]}")
        print(f"       {' + '.join(sorted(ma.elements()))}: " + " | ".join(detail))

print("\n===== V8: road_access whose access_to is an identifier-less POI node =====")
noid = {nid for nid, (t, lo, la) in A.pn.items() if not has_id(t)}
tgt = {rid: ms for rid, ms in A.racc.items()
       if any(mt == "n" and ref in noid and role == "access_to" for mt, ref, role in ms)}
same = sum(1 for rid, ms in tgt.items() if B.racc.get(rid) == ms)
gone = sum(1 for rid in tgt if rid not in B.racc)
print(f"  count={len(tgt)}  unchanged in target={same}  absent in target={gone} "
      f"  members changed={len(tgt) - same - gone}")

print("\n===== V10: the two charging sites, re-asserted =====")


def stloc_parent(h):
    d = {}
    for rid, (t, ms) in h.chst.items():
        for mt, ref, role in ms:
            if role == "charging_station_location":
                d[ref] = (t.get("station_id"), rid)
    return d


pa, pb = stloc_parent(A), stloc_parent(B)
sl_a = {n for n, (t, lo, la) in A.pn.items() if t.get("amenity") == "charging_station_location"}
sl_b = {n for n, (t, lo, la) in B.pn.items() if t.get("amenity") == "charging_station_location"}
print(f"  station_location nodes: A={len(sl_a)} B={len(sl_b)}; "
      f"with any identifier: A={sum(1 for n in sl_a if has_id(A.pn[n][0]))} "
      f"B={sum(1 for n in sl_b if has_id(B.pn[n][0]))}; "
      f"members of a charging_station: A={len(pa)} B={len(pb)}")
for cla, clb, sla, slb in ((43312024793, 60989216882, 63189510030, 63217178114),
                           (63029169725, 63042446897, 63125711610, 63216640076)):
    swap = ((round(A.pn[cla][1], 7), round(A.pn[cla][2], 7))
            == (round(B.pn[slb][1], 7), round(B.pn[slb][2], 7))
            and (round(A.pn[sla][1], 7), round(A.pn[sla][2], 7))
            == (round(B.pn[clb][1], 7), round(B.pn[clb][2], 7)))
    d = hav(A.pn[cla][1], A.pn[cla][2], B.pn[clb][1], B.pn[clb][2])
    print(f"  site cl n{cla}->n{clb}, sl n{sla}->n{slb}: exact swap={swap} dist={d:.2f} m")
    print(f"    parent station A={pa.get(sla)} B={pb.get(slb)}")
    print(f"    charging_location attribution identical="
          f"{attrib(A.pn[cla][0]) == attrib(B.pn[clb][0])}; "
          f"station_location attribution identical={attrib(A.pn[sla][0]) == attrib(B.pn[slb][0])}")
