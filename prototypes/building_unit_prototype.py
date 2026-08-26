"""PROTOTYPE - THROWAWAY (issue #12). Not production code.

Question: what is the building canonical unit? Build the three candidates over
real churn and compare their ledgers.

  A "dissolve"   - touching same-attribution footprints union into one feature
  B "footprint"  - one feature per footprint; every split/merge is a real change
  C "footprint+" - one feature per footprint, but a match group whose union is
                   preserved and whose attribution set is identical is noise

Run:
    python prototypes/building_unit_prototype.py <baseline.pbf> <target.pbf> [label]

Geometry is projected to local metres at load time (equirectangular about the
data centre), so every area is m2 and every distance is metres. Deviation uses
the symmetric Hausdorff distance between the two unions' boundaries - the
polygon analog of #9's symmetric max sampled deviation.

Structural findings that shape the loader (probe 2, Amersfoort 26340):
 - 0 building nodes. Buildings are ways, plus 70 multipolygon relations that
   carry building=* while NONE of their member ways do (0/2296) - so a
   way-only scan silently misses them.
 - 427 type=building relations hold roles outline (427) + part (647) and carry
   no tags but type. This is the 3D scheme: building:part ways are sub-volumes
   of one outline, never separate real buildings. 646 building:part ways exist,
   0 of them also carry building=*, and 566/646 sit geometrically inside a
   plain footprint. Per CONTEXT.md the type=building relation is a
   CONSTITUTIVE relation and its part members are feature parts: absorbed into
   the outline, never features of their own.
 - The 359 osm_identifier values shared by two footprints are exactly
   outline+part pairs (all 359 touch, 0 are attribution-identical). A shared
   osm_identifier on buildings means "same OSM source building", NOT a split.
 - building_group has only 2 distinct values in the clip (education 294,
   healthcare 9). It is a category, not a grouping key: attribution.
"""
import collections
import hashlib
import json
import math
import sys
import time

import osmium
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

BLD_KEYS = ("building", "building:part")
# ignored - identity (docs/tag-classification.md)
ID_KEYS = ("gers_identifier", "osm_identifier")
# ignored - metadata / housekeeping (docs/tag-classification.md)
META = {"layer_id", "license", "license_zone", "supported", "data_size_index"}

T_ROUND_M = 0.10         # union preserved within rounding (#9 rounding tier)
T_DRIFT_M = 1.80         # shape residual tier from #9; above this, geometry is real
OVERLAP_SHARE = 0.20     # geometric fallback: link every counterpart covering this
                         # share of the smaller of the two footprints (coverage, not
                         # best-IoU, so 1:N and N:M groups can form)
DEG_M = 111320.0


def is_meta(k):
    return k in META or k.startswith("layer_id:") or k.startswith("license:")


def attribution(tags):
    return {k: v for k, v in tags.items() if k not in ID_KEYS and not is_meta(k)}


# ---------------- loading ----------------

class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.bways = {}      # building / building:part ways: id -> (tags, refs)
        self.way_refs = {}   # ALL ways: id -> refs (multipolygon member geometry)
        self.bldrels = {}    # type=building: id -> (tags, members)
        self.mprels = {}     # type=multipolygon carrying building=*: id -> (tags, members)

    def way(self, w):
        refs = [n.ref for n in w.nodes]
        self.way_refs[w.id] = refs
        t = {x.k: x.v for x in w.tags}
        if any(k in t for k in BLD_KEYS):
            self.bways[w.id] = (t, refs)

    def relation(self, r):
        t = {x.k: x.v for x in r.tags}
        mem = [(m.type, m.ref, m.role) for m in r.members]
        if t.get("type") == "building":
            self.bldrels[r.id] = (t, mem)
        elif t.get("type") == "multipolygon" and any(k in t for k in BLD_KEYS):
            self.mprels[r.id] = (t, mem)


class LocFill(osmium.SimpleHandler):
    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.loc = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)


class Feature:
    __slots__ = ("fid", "geom", "attribution", "ids", "elements", "parts", "kind", "lonlat")

    def __init__(self, fid, geom, attr, ids, elements, parts, kind, lonlat):
        self.fid = fid
        self.geom = geom
        self.attribution = attr
        self.ids = ids
        self.elements = elements
        self.parts = parts
        self.kind = kind
        self.lonlat = lonlat


def make_projector(loc):
    """Equirectangular projection about the data centre: degrees -> metres."""
    if not loc:
        return (lambda lon, lat: (0.0, 0.0)), (0.0, 0.0)
    lons = [v[0] for v in loc.values()]
    lats = [v[1] for v in loc.values()]
    lon0 = (min(lons) + max(lons)) / 2
    lat0 = (min(lats) + max(lats)) / 2
    kx = DEG_M * math.cos(math.radians(lat0))

    def proj(lon, lat):
        return ((lon - lon0) * kx, (lat - lat0) * DEG_M)

    return proj, (lon0, lat0)


def unproject(x, y, origin):
    lon0, lat0 = origin
    kx = DEG_M * math.cos(math.radians(lat0))
    return (lon0 + x / kx, lat0 + y / DEG_M)


def rings_lonlat(geom, origin):
    """Exterior + interior rings of a (multi)polygon, back in lon/lat."""
    out = []
    geoms = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    for g in geoms:
        if g.is_empty or g.geom_type != "Polygon":
            continue
        for r in [g.exterior] + list(g.interiors):
            out.append([[round(v, 7) for v in unproject(x, y, origin)]
                        for x, y in r.coords])
    return out


def ring(refs, loc, proj):
    pts = [proj(*loc[r]) for r in refs if r in loc]
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


def multipoly(mem, c, loc, proj):
    outer, inner = [], []
    for ty, ref, role in mem:
        if ty != "w":
            continue
        refs = c.way_refs.get(ref)
        if not refs:
            continue
        p = ring(refs, loc, proj)
        if p is None:
            continue
        (inner if role == "inner" else outer).append(p)
    if not outer:
        return None
    o = unary_union(outer)
    if inner:
        o = o.difference(unary_union(inner))
    return o if not o.is_empty else None


def _sig(tags, geom):
    h = hashlib.blake2b(digest_size=8)
    h.update(repr((sorted(tags.items()), geom)).encode())
    return h.digest()


def load(path, proj=None, origin=None):
    """One map version -> canonical building footprints, parts absorbed."""
    c = Collector()
    c.apply_file(path)
    needed = set()
    for _, refs in c.bways.values():
        needed.update(refs)
    for src in (c.bldrels, c.mprels):
        for _, mem in src.values():
            for ty, ref, _ in mem:
                if ty == "w":
                    needed.update(c.way_refs.get(ref, ()))
    lf = LocFill(needed)
    lf.apply_file(path)
    loc = lf.loc
    if proj is None:
        proj, origin = make_projector(loc)

    # which building:part ways belong to which outline (constitutive relation)
    part_of = {}
    outline_parts = collections.defaultdict(list)
    part_rel = {}
    for rid, (t, mem) in c.bldrels.items():
        outs = [ref for ty, ref, role in mem if role == "outline" and ty == "w"]
        prts = [ref for ty, ref, role in mem if role == "part" and ty == "w"]
        if not outs:
            continue
        o = outs[0]
        part_rel[o] = rid
        for p in prts:
            part_of[p] = o
            outline_parts[o].append(p)

    feats = {}
    orphan_parts = 0
    for wid, (t, refs) in c.bways.items():
        if wid in part_of:
            continue                      # absorbed as a feature part
        if "building:part" in t and "building" not in t:
            orphan_parts += 1             # a part no type=building relation claims
        g = ring(refs, loc, proj)
        if g is None:
            continue
        parts = [{"element": f"w{p}", "attribution": attribution(c.bways.get(p, ({}, []))[0])}
                 for p in outline_parts.get(wid, ())]
        elements = [f"w{wid}"] + [f"w{p}" for p in outline_parts.get(wid, ())]
        if wid in part_rel:
            elements.append(f"r{part_rel[wid]}")
        cen = g.centroid
        feats[f"w{wid}"] = Feature(f"w{wid}", g, attribution(t),
                                   {k: t[k] for k in ID_KEYS if k in t},
                                   elements, parts, "way",
                                   unproject(cen.x, cen.y, origin))

    mp_ok = 0
    for rid, (t, mem) in c.mprels.items():
        g = multipoly(mem, c, loc, proj)
        if g is None:
            continue
        mp_ok += 1
        cen = g.centroid
        feats[f"r{rid}"] = Feature(f"r{rid}", g, attribution(t),
                                   {k: t[k] for k in ID_KEYS if k in t},
                                   [f"r{rid}"] + [f"w{ref}" for ty, ref, _ in mem if ty == "w"],
                                   [], "multipolygon",
                                   unproject(cen.x, cen.y, origin))
    raw = {}
    for wid, (t, refs) in c.bways.items():
        raw[f"w{wid}"] = _sig(t, refs)
    for rid, (t, mem) in c.bldrels.items():
        raw[f"r{rid}"] = _sig(t, mem)
    for rid, (t, mem) in c.mprels.items():
        raw[f"r{rid}"] = _sig(t, mem)

    stats = {
        "building_ways": len(c.bways),
        "absorbed_parts": len(part_of),
        "orphan_parts": orphan_parts,
        "building_relations": len(c.bldrels),
        "multipolygon_buildings": len(c.mprels),
        "multipolygon_built": mp_ok,
        "features": len(feats),
        "locations": len(loc),
    }
    return feats, stats, proj, origin, raw


# ---------------- candidate A: dissolve ----------------

def dissolve(feats):
    """Touching + attribution-identical footprints become one feature."""
    fids = list(feats)
    polys = [feats[f].geom for f in fids]
    tree = STRtree(polys)
    parent = {f: f for f in fids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def key(f):
        return tuple(sorted(feats[f].attribution.items()))

    for i, p in enumerate(polys):
        for j in tree.query(p):
            if j <= i:
                continue
            if p.intersects(polys[j]) and key(fids[i]) == key(fids[j]):
                uni(fids[i], fids[j])
    comp = collections.defaultdict(list)
    for f in fids:
        comp[find(f)].append(f)
    out = {}
    for members in comp.values():
        if len(members) == 1:
            out[members[0]] = feats[members[0]]
            continue
        g = unary_union([feats[m].geom for m in members])
        ids = {}
        for m in members:
            for k, v in feats[m].ids.items():
                ids.setdefault(k, set()).add(v)
        ids = {k: sorted(v) for k, v in ids.items()}
        elements = [e for m in members for e in feats[m].elements]
        fid = "blob:" + min(members)
        out[fid] = Feature(fid, g, feats[members[0]].attribution, ids, elements,
                           [], "blob", feats[members[0]].lonlat)
    return out


# ---------------- matching (identity-first, per #7) ----------------

def match_groups(A, B):
    """Connected components of baseline+target features linked by identifier or overlap."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for f in A:
        find(("a", f))
    for f in B:
        find(("b", f))

    def values(ft, k):
        v = ft.ids.get(k)
        if v is None:
            return ()
        return (v,) if isinstance(v, str) else tuple(v)

    linked_a, linked_b = set(), set()
    for k in ID_KEYS:
        va, vb = collections.defaultdict(list), collections.defaultdict(list)
        for f, ft in A.items():
            for v in values(ft, k):
                va[v].append(f)
        for f, ft in B.items():
            for v in values(ft, k):
                vb[v].append(f)
        for v in set(va) & set(vb):
            # only where the value is unambiguous on each side (#11 finding)
            if len(va[v]) == 1 and len(vb[v]) == 1:
                uni(("a", va[v][0]), ("b", vb[v][0]))
                linked_a.add(va[v][0])
                linked_b.add(vb[v][0])

    rest_a = [f for f in A if f not in linked_a]
    rest_b = [f for f in B if f not in linked_b]
    if rest_a and rest_b:
        polys = [B[f].geom for f in rest_b]
        tree = STRtree(polys)
        for f in rest_a:
            g = A[f].geom
            for j in tree.query(g):
                h = polys[j]
                inter = g.intersection(h).area
                if inter <= 0:
                    continue
                # COVERAGE-based, not best-IoU. Best-IoU pairs a split parent with
                # only its largest child and drops the rest as spurious 'created';
                # measured on the NLD churn set, that turned the verified noise case
                # w4884694193 (Loo 22 / 22a) into a false 'real, 8.03 m geometry'
                # plus one spurious created footprint. Linking EVERY sufficiently
                # overlapping counterpart lets 1:N and N:M groups form natively,
                # which is what #7's match group is defined to be.
                if inter / min(g.area, h.area) >= OVERLAP_SHARE:
                    uni(("a", f), ("b", rest_b[j]))

    groups = collections.defaultdict(lambda: ([], []))
    for x in list(parent):
        root = find(x)
        side, f = x
        groups[root][0 if side == "a" else 1].append(f)
    return [(sorted(a), sorted(b)) for a, b in groups.values()]


# ---------------- per-group verdicts ----------------

def hausdorff_m(ga, gb):
    """Symmetric max deviation between the two unions' boundaries, in metres."""
    if ga.is_empty or gb.is_empty:
        return float("inf")
    try:
        ba, bb = ga.boundary, gb.boundary
        return max(ba.hausdorff_distance(bb), bb.hausdorff_distance(ba))
    except Exception:
        return float("inf")


def attr_sets(F, fs):
    """Distinct attribution dicts on one side of a match group."""
    return {frozenset(F[f].attribution.items()) for f in fs}


def attr_report(sa, sb):
    """Human-readable difference between two attribution sets."""
    if sa == sb:
        return []
    only_a = sa - sb
    only_b = sb - sa
    out = []
    if len(sa) == 1 and len(sb) == 1:
        da, db = dict(next(iter(sa))), dict(next(iter(sb)))
        for k in sorted(set(da) | set(db)):
            if da.get(k) != db.get(k):
                out.append((k, da.get(k), db.get(k)))
        return out
    for s in sorted(only_a, key=lambda x: sorted(x)):
        out.append(("<baseline-only variant>", dict(s), None))
    for s in sorted(only_b, key=lambda x: sorted(x)):
        out.append(("<target-only variant>", None, dict(s)))
    return out


def build_rows(A, B):
    rows = []
    for ga, gb in match_groups(A, B):
        if ga and not gb:
            rows.append({"a": ga, "b": [], "gone": True})
            continue
        if gb and not ga:
            rows.append({"a": [], "b": gb, "new": True})
            continue
        ua = unary_union([A[f].geom for f in ga])
        ub = unary_union([B[f].geom for f in gb])
        sa, sb = attr_sets(A, ga), attr_sets(B, gb)
        rows.append({
            "a": ga, "b": gb,
            "dev_m": hausdorff_m(ua, ub),
            "area_a": ua.area, "area_b": ub.area,
            "attr_same": sa == sb,
            "attr_diff": attr_report(sa, sb),
            "cardinality": (len(ga), len(gb)),
        })
    return rows


def classify(rows, candidate):
    """candidate in {A, B, C}. Returns counts and the real-change rows."""
    counts = collections.Counter()
    reals = []
    for r in rows:
        if r.get("gone"):
            counts["real:deleted"] += 1
            reals.append(dict(r, verdict="real", why="no target counterpart"))
            continue
        if r.get("new"):
            counts["real:created"] += 1
            reals.append(dict(r, verdict="real", why="no baseline counterpart"))
            continue
        na, nb = r["cardinality"]
        split_merge = (na, nb) != (1, 1)
        attr_changed = not r["attr_same"]
        dev = r["dev_m"]
        geom_real = dev > (T_ROUND_M if split_merge else T_DRIFT_M)
        union_preserved = dev <= T_ROUND_M

        if candidate == "C" and split_merge and union_preserved and not attr_changed:
            counts["noise:union-preserving split/merge"] += 1
            continue
        if not split_merge and not attr_changed and not geom_real:
            counts["noise:unchanged or sub-tolerance"] += 1
            continue
        if split_merge:
            why = f"{na}:{nb} re-partitioning"
            if union_preserved and not attr_changed:
                why += " (union preserved, attribution identical)"
            counts["real:split/merge"] += 1
            reals.append(dict(r, verdict="real", why=why))
            continue
        if geom_real and attr_changed:
            counts["real:geometry+attribution"] += 1
            reals.append(dict(r, verdict="real", why=f"geometry {dev:.2f} m and attribution"))
        elif geom_real:
            counts["real:geometry"] += 1
            reals.append(dict(r, verdict="real", why=f"geometry, {dev:.2f} m"))
        else:
            counts["real:attribution"] += 1
            reals.append(dict(r, verdict="real", why="attribution"))
    return counts, reals


def row_json(r, FA, FB, origin):
    def side(F, fs):
        return [{"fid": f, "elements": F[f].elements, "ids": F[f].ids,
                 "attribution": F[f].attribution,
                 "area_m2": round(F[f].geom.area, 1),
                 "centroid": [round(F[f].lonlat[0], 6), round(F[f].lonlat[1], 6)],
                 "rings": rings_lonlat(F[f].geom, origin),
                 "parts": F[f].parts}
                for f in fs]
    dev = r.get("dev_m")
    return {"verdict": r.get("verdict"), "why": r.get("why"),
            "dev_m": None if dev is None else round(dev, 3),
            "area_a_m2": None if r.get("area_a") is None else round(r["area_a"], 1),
            "area_b_m2": None if r.get("area_b") is None else round(r["area_b"], 1),
            "attr_diff": r.get("attr_diff"),
            "cardinality": r.get("cardinality"),
            "baseline": side(FA, r["a"]), "target": side(FB, r["b"])}


# ---------------- raw churn accounting (the flood / silent-loss check) ----------------

def churn_report(rawA, rawB, A, B, rows, reals):
    """Every in-scope element change must land somewhere: a real group, a noise
    group, or an absorbed feature part. Anything unaccounted for is a defect."""
    created = set(rawB) - set(rawA)
    deleted = set(rawA) - set(rawB)
    modified = {k for k in rawA.keys() & rawB.keys() if rawA[k] != rawB[k]}
    claim_a, claim_b = {}, {}
    for f, ft in A.items():
        for e in ft.elements:
            claim_a.setdefault(e, f)
    for f, ft in B.items():
        for e in ft.elements:
            claim_b.setdefault(e, f)
    real_fids = {("a", f) for r in reals for f in r["a"]} | \
                {("b", f) for r in reals for f in r["b"]}
    acct = collections.Counter()
    unaccounted = []
    for kind, ids in (("created", created), ("deleted", deleted), ("modified", modified)):
        for e in ids:
            side = "b" if kind == "created" else "a"
            claim = claim_b if side == "b" else claim_a
            f = claim.get(e)
            if f is None and kind == "modified":
                f = claim_b.get(e)
                side = "b"
            if f is None:
                acct[f"{kind}: no feature claims it"] += 1
                unaccounted.append((kind, e))
            elif (side, f) in real_fids:
                acct[f"{kind}: in a real change group"] += 1
            else:
                acct[f"{kind}: in a noise group"] += 1
    return {"created": len(created), "deleted": len(deleted), "modified": len(modified),
            "accounting": dict(acct), "unaccounted": unaccounted[:40]}


# ---------------- HTML ledger ----------------

MAP_W, MAP_H = 460, 320


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _merc_px(lon, lat, z):
    n = 256 * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    sn = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sn) / (1 - sn)) / (4 * math.pi)) * n
    return x, y


def poly_overlay(rings_a, rings_b):
    """Baseline outlines (purple) over target outlines (green) on faded OSM tiles."""
    pts = [p for rs in (rings_a + rings_b) for p in rs]
    if not pts:
        return ""
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    lon_mid, lat_mid = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    span_m = max((max(lons) - min(lons)) * DEG_M * math.cos(math.radians(lat_mid)),
                 (max(lats) - min(lats)) * DEG_M, 8.0)
    mpp_wanted = span_m / (MAP_W * 0.7)
    mpp_eq = 156543.03392 * math.cos(math.radians(lat_mid))
    z = min(19, max(13, int(math.log2(mpp_eq / mpp_wanted))))
    mpp = mpp_eq / (1 << z)
    cx, cy = _merc_px(lon_mid, lat_mid, z)
    left, top = cx - MAP_W / 2, cy - MAP_H / 2
    tiles = []
    for tx in range(int(left // 256), int((left + MAP_W) // 256) + 1):
        for ty in range(int(top // 256), int((top + MAP_H) // 256) + 1):
            tiles.append(
                f'<img src="https://tile.openstreetmap.org/{z}/{tx}/{ty}.png" '
                f'style="left:{tx * 256 - left:.0f}px;top:{ty * 256 - top:.0f}px" alt="">')

    def path(rs):
        out = []
        for r in rs:
            d = []
            for i, (lon, lat) in enumerate(r):
                x, y = _merc_px(lon, lat, z)
                d.append(f"{'M' if i == 0 else 'L'}{x - left:.1f} {y - top:.1f}")
            out.append(" ".join(d) + " Z")
        return " ".join(out)

    bar_m = next((m for m in (1, 2, 5, 10, 20, 50, 100, 200) if 50 <= m / mpp <= 150), 20)
    svg = (f'<svg width="{MAP_W}" height="{MAP_H}">'
           f'<path d="{path(rings_b)}" fill="#27ae6033" stroke="#27ae60" stroke-width="2"/>'
           f'<path d="{path(rings_a)}" fill="none" stroke="#8e44ad" stroke-width="2.5" '
           f'stroke-dasharray="6 4"/>'
           f'<rect x="10" y="{MAP_H - 26}" width="{bar_m / mpp:.0f}" height="4" fill="#333"/>'
           f'<text x="10" y="{MAP_H - 32}" font-size="11" fill="#333">{bar_m:g} m</text>'
           f'</svg>')
    return f'<div class=map style="width:{MAP_W}px;height:{MAP_H}px">{"".join(tiles)}{svg}</div>'


def entry_html(r):
    ra = [ring for f in r["baseline"] for ring in f["rings"]]
    rb = [ring for f in r["target"] for ring in f["rings"]]
    na, nb = len(r["baseline"]), len(r["target"])
    els = ", ".join(e for f in r["baseline"] + r["target"] for e in f["elements"])
    area = ""
    if r["area_a_m2"] is not None and r["area_b_m2"] is not None:
        area = (f" · area {r['area_a_m2']:.0f} → {r['area_b_m2']:.0f} m² "
                f"({r['area_b_m2'] - r['area_a_m2']:+.0f})")
    ids = set()
    for f in r["baseline"] + r["target"]:
        for k, v in f["ids"].items():
            ids.add(f"{k}={v if isinstance(v, str) else ','.join(v)}")
    diff = ""
    if r["attr_diff"]:
        rowsh = "".join(
            f"<tr><td class=k>{esc(k)}</td><td class=del>{esc(a) if a is not None else ''}</td>"
            f"<td class=add>{esc(b) if b is not None else ''}</td></tr>"
            for k, a, b in r["attr_diff"])
        diff = f"<table class=diff>{rowsh}</table>"
    attr = ""
    for f in r["baseline"] + r["target"]:
        if f["parts"]:
            attr += (f"<div class=meta>{esc(f['fid'])} has {len(f['parts'])} absorbed "
                     f"3D part(s): {esc([p['element'] for p in f['parts']])}</div>")
    return (f"<div class=card><div class=cardtext>"
            f"<span class='badge real'>real</span> <b>{esc(r['why'])}</b> "
            f"<span class=meta>{na}:{nb}{area}</span>"
            f"<div class=meta>elements: {esc(els)}</div>"
            f"<div class=meta>identifiers: {esc(sorted(ids)) if ids else 'none'}</div>"
            f"{attr}{diff}</div>{poly_overlay(ra, rb)}</div>")


def write_ledger(out, dest):
    cands = out["candidates"]
    kpi = "".join(
        f"<span class=kpi><b>{c['real_total']}</b>{esc(name)}</span>"
        for name, c in cands.items())
    ch = out["churn"]
    parts = [f"""<meta charset="utf-8">
<title>Building canonical unit — candidate ledgers</title>
<style>
 body {{ font:14px/1.5 system-ui,sans-serif; margin:2rem auto; max-width:74rem;
        padding:0 1rem; color:#222; }}
 h1 {{ font-size:1.4rem; }} h2 {{ font-size:1.1rem; margin-top:2rem; }}
 .badge {{ display:inline-block; padding:0 .5em; border-radius:.6em; font-size:.85em;
           color:#fff; background:#c0392b; }}
 .meta {{ color:#777; font-size:.85em; }}
 .kpi {{ display:inline-block; margin:.3rem 1.4rem .3rem 0; }}
 .kpi b {{ font-size:1.5rem; display:block; }}
 .card {{ display:flex; gap:1rem; align-items:flex-start; border:1px solid #eee;
          border-radius:6px; padding:.6rem .8rem; margin:.6rem 0; }}
 .cardtext {{ flex:1; min-width:16rem; }}
 .map {{ position:relative; overflow:hidden; border-radius:4px; flex:none; background:#f4f4f4; }}
 .map img {{ position:absolute; width:256px; height:256px;
             filter:saturate(.2) contrast(.75) brightness(1.3); }}
 .map svg {{ position:absolute; left:0; top:0; }}
 table.diff {{ border-collapse:collapse; margin:.4rem 0; }}
 table.diff td {{ border:1px solid #eee; padding:.15rem .5rem; vertical-align:top; }}
 td.k {{ font-family:monospace; white-space:nowrap; }}
 .del {{ background:#fdecea; text-decoration:line-through; }} .add {{ background:#eafaf1; }}
 table.cnt {{ border-collapse:collapse; margin:.6rem 0; }}
 table.cnt td, table.cnt th {{ border:1px solid #eee; padding:.2rem .6rem; text-align:left; }}
 table.cnt td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .legend span {{ display:inline-block; width:1.4em; height:.55em; margin:0 .3em 0 1em;
                 vertical-align:.1em; }}
</style>
<h1>Building canonical unit — candidate ledgers ({esc(out['label'])}, issue #12)</h1>
<p>Three candidate units over the same churn. <b>A dissolve</b>: touching
same-attribution footprints union into one feature. <b>B footprint</b>: one feature
per footprint, every split/merge is a real change. <b>C footprint+</b>: same features
as B, but a match group whose union is preserved (≤ {T_ROUND_M:g} m) and whose
attribution set is identical is noise.</p>
<p class=meta>In every candidate: <code>building:part</code> ways are absorbed into
their <code>type=building</code> outline as feature parts;
<code>type=multipolygon</code> relations carrying <code>building=*</code> are
reconstructed; matching is identity-first on <code>gers_identifier</code> then
<code>osm_identifier</code> (unambiguous values only), geometric fallback at
coverage ≥ {OVERLAP_SHARE:g} of the smaller footprint (every such counterpart, so
1:N and N:M groups form); 1:1 geometry above {T_DRIFT_M:g} m is real.</p>
<div>{kpi}</div>
<h2>Candidate counts</h2>
<table class=cnt><tr><th>verdict</th>{"".join(f"<th>{esc(n)}</th>" for n in cands)}</tr>"""]
    keys = sorted({k for c in cands.values() for k in c["counts"]})
    for k in keys:
        parts.append(f"<tr><td>{esc(k)}</td>" + "".join(
            f"<td class=n>{c['counts'].get(k, 0)}</td>" for c in cands.values()) + "</tr>")
    parts.append("<tr><td><b>real changes total</b></td>" + "".join(
        f"<td class=n><b>{c['real_total']}</b></td>" for c in cands.values()) + "</tr>")
    parts.append("<tr><td>canonical features (baseline / target)</td>" + "".join(
        f"<td class=n>{c['features'][0]} / {c['features'][1]}</td>" for c in cands.values())
        + "</tr></table>")

    parts.append(f"""<h2>Raw element churn accounting</h2>
<p class=meta>Every in-scope element change must land in a real group, a noise group,
or an absorbed feature part. <b>Anything unaccounted for is a defect.</b></p>
<table class=cnt><tr><th>bucket</th><th>count</th></tr>
<tr><td>raw created / deleted / modified</td>
    <td class=n>{ch['created']} / {ch['deleted']} / {ch['modified']}</td></tr>""")
    for k, v in sorted(ch["accounting"].items()):
        parts.append(f"<tr><td>{esc(k)}</td><td class=n>{v}</td></tr>")
    parts.append("</table>")
    if ch["unaccounted"]:
        parts.append(f"<p class=meta>unaccounted sample: {esc(ch['unaccounted'])}</p>")

    parts.append("""<p class="meta legend">
<span style="border:2.5px dashed #8e44ad"></span>baseline footprint
<span style="background:#27ae6033;border:2px solid #27ae60"></span>target footprint
· basemap © OpenStreetMap contributors, faded; needs internet to load tiles.</p>""")
    for name, c in cands.items():
        parts.append(f"<h2>{esc(name)} — real changes ({c['real_total']})</h2>")
        if not c["reals"]:
            parts.append("<p class=meta>none</p>")
        parts += [entry_html(r) for r in c["reals"]]

    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    pa, pb = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else "sample"
    t0 = time.time()
    A, sa, proj, origin, rawA = load(pa)
    print(f"[baseline] {pa}\n  {sa}  ({time.time()-t0:.0f}s)", flush=True)
    B, sb, _, _, rawB = load(pb, proj, origin)
    print(f"[target]   {pb}\n  {sb}  ({time.time()-t0:.0f}s)", flush=True)
    print()

    out = {"label": label, "origin": list(origin),
           "baseline_stats": sa, "target_stats": sb, "candidates": {}}

    DA, DB = dissolve(A), dissolve(B)
    print(f"dissolve: baseline {len(A)} footprints -> {len(DA)} features; "
          f"target {len(B)} -> {len(DB)}", flush=True)
    print()

    rows_fp = build_rows(A, B)
    rows_bl = build_rows(DA, DB)
    churn_for = None
    for name, rows, FA, FB in (("A dissolve", rows_bl, DA, DB),
                               ("B footprint", rows_fp, A, B),
                               ("C footprint+", rows_fp, A, B)):
        counts, reals = classify(rows, name[0])
        print(f"=== candidate {name} ===")
        print(f"  match groups: {len(rows)}")
        for k, v in sorted(counts.items()):
            print(f"    {k:42s} {v}")
        print(f"    {'REAL CHANGES TOTAL':42s} {len(reals)}")
        out["candidates"][name] = {
            "groups": len(rows),
            "counts": dict(counts),
            "real_total": len(reals),
            "features": [len(FA), len(FB)],
            "reals": [row_json(r, FA, FB, origin) for r in reals[:500]],
        }
        if name.startswith("C"):
            churn_for = (rows, reals)
        print()

    out["churn"] = churn_report(rawA, rawB, A, B, *churn_for)
    print("=== raw element churn accounting (candidate C) ===")
    print(f"  raw created={out['churn']['created']} deleted={out['churn']['deleted']} "
          f"modified={out['churn']['modified']}")
    for k, v in sorted(out["churn"]["accounting"].items()):
        print(f"    {k:42s} {v}")
    if out["churn"]["unaccounted"]:
        print(f"    unaccounted sample: {out['churn']['unaccounted']}")
    print()

    dest = f"prototypes/output/building_unit_{label}.json"
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1)
    html = f"prototypes/output/building_unit_{label}.html"
    write_ledger(out, html)
    print(f"wrote {dest}\nwrote {html}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
