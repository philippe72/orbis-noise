"""PROTOTYPE — THROWAWAY (issue #9). Not production code.

Question: where is the line between a rounding shift and a real geometry
change between two map versions?

Calibration pass (v2). Builds merged roads (bivalent-node chains, geometry
only) for two Amersfoort clips, groups them into match groups (connected
components over shared GERS identifiers, per #7), and for every 1:1 group
whose endpoint topology is comparable across versions (same neighbor match
groups at both ends) decomposes the geometry deviation on the trimmed
common extent into:

  raw      — symmetric max sampled point-to-polyline distance (~Hausdorff)
  bulk     — best-fit translation of target onto baseline (corridor shift)
  residual — max deviation left after removing the bulk shift (shape change)
  overhang — extent trimmed off either side (choice-point drift, in meters)

Tiered classification under calibration (all thresholds configurable):
  raw <= T_ROUND                     -> noise (rounding)
  bulk > T_BULK                      -> REAL (bulk correction)
  residual > T_SHAPE                 -> REAL (shape change)
  otherwise                          -> noise (drift)
Topology-incomparable pairs are routed to the change-group / created-feature
channel, not judged on geometry.

Renders distributions plus boundary-band galleries (examples near T_SHAPE and
T_BULK, extent-drift confirmations, routed-out pairs) for human eyeballing.

Run:  .venv-research/Scripts/python.exe prototypes/geometry_tolerance_prototype.py
Output: stdout summary + prototypes/output/geometry_tolerance_report.html
"""

import base64
import collections
import html
import io
import math
import os
import sys
import time

import osmium
import requests
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

BASE_CLIP = sys.argv[1] if len(sys.argv) > 1 else "data/clips/amersfoort_26330.osm.pbf"
TARGET_CLIP = sys.argv[2] if len(sys.argv) > 2 else "data/clips/amersfoort_26340.osm.pbf"
OUT = "prototypes/output/geometry_tolerance_report.html"

SAMPLE_STEP_M = 2.0     # densification step along each polyline
MAX_SAMPLES = 400       # cap per polyline (long roads)

# thresholds under calibration
T_ROUND = 0.10
T_BULK = 5.0
T_SHAPE = 1.5

# ---------------- load one clip -> merged road geometries + GERS sets ----------------

class WayPass(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}  # id -> (gers or None, [node refs])

    def way(self, w):
        if "highway" not in w.tags:
            return
        refs = [n.ref for n in w.nodes]
        if len(refs) < 2:
            return
        self.ways[w.id] = (w.tags.get("gers_identifier"), refs)


class NodePass(osmium.SimpleHandler):
    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.loc = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)


def load_merged_roads(clip):
    """-> list of dicts {pts: [(lon,lat)...], gers: frozenset, nways: int}"""
    print(f"loading {clip} ...", flush=True)
    wp = WayPass()
    wp.apply_file(clip)
    ways = wp.ways

    endpoint_at = collections.defaultdict(list)
    interior_count = collections.Counter()
    needed = set()
    for wid, (_, refs) in ways.items():
        endpoint_at[refs[0]].append(wid)
        endpoint_at[refs[-1]].append(wid)
        for r in refs[1:-1]:
            interior_count[r] += 1
        needed.update(refs)

    bivalent = {}
    for node, wids in endpoint_at.items():
        if len(wids) == 2 and wids[0] != wids[1] and interior_count[node] == 0:
            bivalent[node] = tuple(wids)

    np_ = NodePass(needed)
    np_.apply_file(clip)
    loc = np_.loc

    def other_end(wid, node):
        refs = ways[wid][1]
        return refs[-1] if refs[0] == node else refs[0]

    visited = set()
    chains = []

    def walk(start_wid, start_node):
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
            if nw in visited:
                return chain
            wid, node = nw, nxt

    for wid, (_, refs) in ways.items():
        if wid in visited:
            continue
        a, b = refs[0], refs[-1]
        if a not in bivalent:
            chains.append(walk(wid, a))
        elif b not in bivalent:
            chains.append(walk(wid, b))
    for wid in ways:
        if wid not in visited:
            chains.append(walk(wid, ways[wid][1][0]))

    roads = []
    for chain in chains:
        pts = []
        gers = set()
        for wid, rev in chain:
            g, refs = ways[wid]
            if g:
                gers.add(g)
            seq = [loc[r] for r in (reversed(refs) if rev else refs)]
            if pts and pts[-1] == seq[0]:
                seq = seq[1:]
            pts.extend(seq)
        roads.append({"pts": pts, "gers": frozenset(gers), "nways": len(chain)})
    print(f"  {len(ways)} ways -> {len(roads)} merged roads", flush=True)
    return roads


# ---------------- local metric space (equirectangular around clip center) ----------------

def make_projector(all_pts):
    lat0 = sum(p[1] for p in all_pts) / len(all_pts)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    return lambda p: (p[0] * kx, p[1] * ky)


def polyline_length(xy):
    return sum(math.dist(xy[i], xy[i + 1]) for i in range(len(xy) - 1))


def densify_n(xy, n):
    """Exactly n arc-length-equidistant samples along the polyline."""
    total = polyline_length(xy)
    out = []
    seg_i, seg_pos = 0, 0.0
    seglens = [math.dist(xy[i], xy[i + 1]) for i in range(len(xy) - 1)]
    for k in range(n):
        target = total * k / (n - 1)
        while seg_i < len(seglens) - 1 and seg_pos + seglens[seg_i] < target:
            seg_pos += seglens[seg_i]
            seg_i += 1
        sl = seglens[seg_i] or 1.0
        t = (target - seg_pos) / sl
        t = min(max(t, 0.0), 1.0)
        a, b = xy[seg_i], xy[seg_i + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def densify(xy, step, cap):
    n = min(cap, max(2, int(polyline_length(xy) / step) + 1))
    return densify_n(xy, n)


def pt_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd == 0:
        return math.dist(p, a)
    t = ((px - ax) * dx + (py - ay) * dy) / dd
    t = min(max(t, 0.0), 1.0)
    return math.dist(p, (ax + dx * t, ay + dy * t))


def pt_line_dist(p, xy):
    return min(pt_seg_dist(p, xy[i], xy[i + 1]) for i in range(len(xy) - 1))


def deviation(xy_a, xy_b):
    """Symmetric sampled deviation. -> (max_dev, mean_dev)"""
    sa = densify(xy_a, SAMPLE_STEP_M, MAX_SAMPLES)
    sb = densify(xy_b, SAMPLE_STEP_M, MAX_SAMPLES)
    ds = [pt_line_dist(p, xy_b) for p in sa] + [pt_line_dist(p, xy_a) for p in sb]
    return max(ds), sum(ds) / len(ds)


# ---------------- deviation decomposition on the trimmed common extent ----------------

def arc_pos(p, xy):
    """Arc position (m from start) of the nearest point on polyline xy to p."""
    best_d, best_s = float("inf"), 0.0
    acc = 0.0
    for i in range(len(xy) - 1):
        a, b = xy[i], xy[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        dd = dx * dx + dy * dy
        seg = math.sqrt(dd)
        t = 0.0 if dd == 0 else min(max(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd, 0.0), 1.0)
        d = math.dist(p, (a[0] + dx * t, a[1] + dy * t))
        if d < best_d:
            best_d, best_s = d, acc + seg * t
        acc += seg
    return best_s


def subline(xy, s0, s1):
    """Sub-polyline between arc positions s0 < s1 (meters). None if degenerate."""
    out = []
    acc = 0.0
    for i in range(len(xy) - 1):
        a, b = xy[i], xy[i + 1]
        seg = math.dist(a, b) or 1e-12
        lo, hi = acc, acc + seg
        if hi > s0 and lo < s1:
            t0 = max((s0 - lo) / seg, 0.0)
            t1 = min((s1 - lo) / seg, 1.0)
            if not out:
                out.append((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0))
            out.append((a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1))
        acc = hi
    return out if len(out) >= 2 and polyline_length(out) > 1e-6 else None


def orient(xa, xb):
    """Flip xb if its ends pair better with xa's ends the other way round."""
    if (math.dist(xa[0], xb[0]) + math.dist(xa[-1], xb[-1])
            > math.dist(xa[0], xb[-1]) + math.dist(xa[-1], xb[0])):
        return xb[::-1], True
    return xb, False


def decompose(xa, xb):
    """xa, xb projected polylines (xb pre-oriented).
    -> dict(raw, bulk, residual, overhang)"""
    la, lb = polyline_length(xa), polyline_length(xb)
    sa0, sa1 = sorted((arc_pos(xb[0], xa), arc_pos(xb[-1], xa)))
    sb0, sb1 = sorted((arc_pos(xa[0], xb), arc_pos(xa[-1], xb)))
    at = subline(xa, sa0, sa1)
    bt = subline(xb, sb0, sb1)
    if not at or not bt or polyline_length(at) < 2.0 or polyline_length(bt) < 2.0:
        at, bt = xa, xb  # degenerate trim (disjoint / tiny): compare full lines
        sa0, sa1, sb0, sb1 = 0.0, la, 0.0, lb
    overhang = (la - (sa1 - sa0)) + (lb - (sb1 - sb0))
    raw_max, _ = deviation(at, bt)
    n = max(20, min(200, int(max(polyline_length(at), polyline_length(bt)) / 2) + 1))
    pa_s = densify_n(at, n)
    pb_s = densify_n(bt, n)
    vx = sum(q[0] - p[0] for p, q in zip(pa_s, pb_s)) / n
    vy = sum(q[1] - p[1] for p, q in zip(pa_s, pb_s)) / n
    bulk = math.hypot(vx, vy)
    bt_shift = [(x - vx, y - vy) for x, y in bt]
    res_max, _ = deviation(at, bt_shift)
    return {"raw": raw_max, "bulk": bulk, "residual": res_max, "overhang": overhang}




def multi_deviation(As, Bs):
    """Symmetric sampled deviation between two SETS of polylines (union geometry)."""
    ds = []
    for A in As:
        for p in densify(A, SAMPLE_STEP_M, MAX_SAMPLES):
            ds.append(min(pt_line_dist(p, B) for B in Bs))
    for B in Bs:
        for p in densify(B, SAMPLE_STEP_M, MAX_SAMPLES):
            ds.append(min(pt_line_dist(p, A) for A in As))
    return max(ds)


# ---------------- run ----------------

base_roads = load_merged_roads(BASE_CLIP)
target_roads = load_merged_roads(TARGET_CLIP)

all_pts = [r["pts"][0] for r in base_roads]
proj = make_projector(all_pts)

# --- match groups: connected components over shared GERS ids (per #7) ---

parent = {}

def find(x):
    while parent.setdefault(x, x) != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x

def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra

for i, r in enumerate(base_roads):
    for g in r["gers"]:
        union(("g", g), ("b", i))
for i, r in enumerate(target_roads):
    for g in r["gers"]:
        union(("g", g), ("t", i))

comps = collections.defaultdict(lambda: ([], []))
comp_of_base, comp_of_target = {}, {}
for i, r in enumerate(base_roads):
    root = find(("b", i)) if r["gers"] else ("b", i)
    comps[root][0].append(i)
    comp_of_base[i] = root
for i, r in enumerate(target_roads):
    root = find(("t", i)) if r["gers"] else ("t", i)
    comps[root][1].append(i)
    comp_of_target[i] = root

MAX_GROUP_SIDE = 12

shape_counts = collections.Counter()
size_hist = collections.Counter()
groups = []  # (bl, tl) with roads on both sides, judgeable
for root, (bl, tl) in comps.items():
    nb, nt = len(bl), len(tl)
    if nb and not nt:
        shape_counts["base-only"] += 1
    elif nt and not nb:
        shape_counts["target-only"] += 1
    elif nb == 1 and nt == 1:
        shape_counts["1:1"] += 1
        groups.append((bl, tl))
    elif nb > MAX_GROUP_SIDE or nt > MAX_GROUP_SIDE:
        shape_counts["oversize (skipped)"] += 1
    else:
        shape_counts["1:N / N:M"] += 1
        size_hist[(nb, nt) if nb <= nt else (nt, nb)] += 1
        groups.append((bl, tl))
print(f"match groups: {dict(shape_counts)}", flush=True)
print("N:M size histogram (top):",
      dict(sorted(size_hist.items(), key=lambda kv: -kv[1])[:8]), flush=True)

# --- endpoint topology (outer profile of a group) ---

def endpoint_index(roads):
    idx = collections.defaultdict(list)
    for i, r in enumerate(roads):
        for end in (0, -1):
            p = r["pts"][end]
            idx[(round(p[0], 7), round(p[1], 7))].append((i, end))
    return idx


ep_base = endpoint_index(base_roads)
ep_target = endpoint_index(target_roads)


def outer_profile(roads, idx, comp_of, members):
    """Multiset of external-neighbor match-group tuples at the group's
    endpoints. Internal endpoints (only group members meet) contribute
    nothing — those are the drifting choice points."""
    memberset = set(members)
    prof = []
    for i in members:
        for end in (0, -1):
            p = roads[i]["pts"][end]
            ext = sorted(str(comp_of[j])
                         for j, _ in idx[(round(p[0], 7), round(p[1], 7))]
                         if j not in memberset)
            if ext:
                prof.append(tuple(ext))
    return sorted(prof)


# --- judge every group ---

records = []
identical = 0
n_rings = 0
routed_out = []
done = 0
for bl, tl in groups:
    done += 1
    if done % 2000 == 0:
        print(f"  judged {done}/{len(groups)} groups ...", flush=True)
    one2one = len(bl) == 1 and len(tl) == 1
    kind = "1:1" if one2one else f"{len(bl)}:{len(tl)}"
    rec = {"bis": bl, "tis": tl, "kind": kind,
           "bulk": None, "residual": None, "overhang": None}
    if (outer_profile(base_roads, ep_base, comp_of_base, bl)
            != outer_profile(target_roads, ep_target, comp_of_target, tl)):
        routed_out.append(rec)
        continue
    b_pts = [base_roads[i]["pts"] for i in bl]
    t_pts = [target_roads[i]["pts"] for i in tl]
    if one2one and b_pts[0] == t_pts[0]:
        identical += 1
        rec.update(raw=0.0, bulk=0.0, residual=0.0, overhang=0.0)
        records.append(rec)
        continue
    # re-partition fast path: same vertex set -> same union geometry
    if {p for pts in b_pts for p in pts} == {p for pts in t_pts for p in pts}:
        rec["raw"] = 0.0
        records.append(rec)
        continue
    xb_all = [[proj(p) for p in pts] for pts in b_pts]
    xt_all = [[proj(p) for p in pts] for pts in t_pts]
    rec["raw"] = multi_deviation(xb_all, xt_all)
    if one2one:
        pa, pb = b_pts[0], t_pts[0]
        if pa[0] == pa[-1] or pb[0] == pb[-1]:
            n_rings += 1  # ring: union deviation only, no decomposition
        else:
            d = decompose(xb_all[0], orient(xb_all[0], xt_all[0])[0])
            rec.update(bulk=d["bulk"], residual=d["residual"],
                       overhang=d["overhang"])
    records.append(rec)

print(f"groups judged: {len(records)}  routed out (outer topology differs): "
      f"{len(routed_out)}  bit-identical: {identical}  rings (raw only): {n_rings}",
      flush=True)


def verdict(d):
    if d["raw"] <= T_ROUND:
        return "noise (rounding / re-partition)"
    if d["bulk"] is not None:
        if d["bulk"] > T_BULK:
            return "REAL (bulk correction)"
        if d["residual"] > T_SHAPE:
            return "REAL (shape change)"
        return "noise (drift)"
    return "N:M above rounding — eyeball"


verdict_counts = collections.Counter(verdict(d) for d in records)
print("verdicts:", dict(verdict_counts), flush=True)

# ---------------- distributions ----------------

def make_bands(edges, unit="m"):
    bands = [("= 0", lambda d: d == 0.0)]
    lo = 0.0
    for hi in edges:
        bands.append((f"{lo:g} – {hi:g} {unit}",
                      lambda d, lo=lo, hi=hi: lo < d <= hi))
        lo = hi
    bands.append((f"> {lo:g} {unit}", lambda d, lo=lo: d > lo))
    return bands


RAW_BANDS = make_bands([0.01, 0.10, 0.25, 0.50, 1.0, 2.0, 5.0, 15.0])
BULK_BANDS = make_bands([0.10, 1.0, 2.0, 3.5, 5.0, 8.0, 20.0])
RES_BANDS = make_bands([0.10, 0.30, 0.75, 1.5, 3.0, 6.0])
OVH_BANDS = make_bands([1.0, 5.0, 15.0, 50.0])


def band_counts(vals, bands):
    return [sum(1 for v in vals if f(v)) for _, f in bands]


for name, key, bands in (("raw (union max_dev)", "raw", RAW_BANDS),
                         ("bulk offset (1:1)", "bulk", BULK_BANDS),
                         ("shape residual (1:1)", "residual", RES_BANDS),
                         ("extent overhang (1:1)", "overhang", OVH_BANDS)):
    vals = [d[key] for d in records if d[key] is not None]
    print(f"\n{name} distribution ({len(vals)} pairs):")
    for (label, _), c in zip(bands, band_counts(vals, bands)):
        print(f"  {label:>16}: {c}")

# ---------------- OSM static screenshots (slippy tiles, embedded as data URIs) ----------------

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_HEADERS = {"User-Agent": "orbis-noise-prototype/0.1 (geometry tolerance report, issue #9)"}
_tile_cache = {}


def _fetch_tile(z, x, y):
    key = (z, x, y)
    if key not in _tile_cache:
        r = requests.get(TILE_URL.format(z=z, x=x, y=y), headers=TILE_HEADERS, timeout=20)
        r.raise_for_status()
        _tile_cache[key] = Image.open(io.BytesIO(r.content)).convert("RGB")
        time.sleep(0.1)
    return _tile_cache[key]


def _merc_px(lon, lat, z):
    n = 256 * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def osm_snapshot(layers, bbox_pts, width=460, height=300):
    """<img> of the current OSM map framed on bbox_pts (plus padding) with
    `layers` = [(pts, rgb, line_width), ...] superimposed, as a data URI."""
    lons = [p[0] for p in bbox_pts]
    lats = [p[1] for p in bbox_pts]
    lon0, lon1 = min(lons), max(lons)
    lat0, lat1 = min(lats), max(lats)
    pad_lon = max((lon1 - lon0) * 0.15, 0.0003)
    pad_lat = max((lat1 - lat0) * 0.15, 0.0002)
    lon0, lon1 = lon0 - pad_lon, lon1 + pad_lon
    lat0, lat1 = lat0 - pad_lat, lat1 + pad_lat

    z = 19
    while z > 12:
        x0, y0 = _merc_px(lon0, lat1, z)
        x1, y1 = _merc_px(lon1, lat0, z)
        if x1 - x0 <= width and y1 - y0 <= height:
            break
        z -= 1
    x0, y0 = _merc_px(lon0, lat1, z)
    x1, y1 = _merc_px(lon1, lat0, z)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0, x1 = cx - width / 2, cx + width / 2
    y0, y1 = cy - height / 2, cy + height / 2

    try:
        tx0, tx1 = int(x0 // 256), int(x1 // 256)
        ty0, ty1 = int(y0 // 256), int(y1 // 256)
        stitched = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                stitched.paste(_fetch_tile(z, tx, ty), ((tx - tx0) * 256, (ty - ty0) * 256))
        crop = stitched.crop((int(x0 - tx0 * 256), int(y0 - ty0 * 256),
                              int(x0 - tx0 * 256) + width, int(y0 - ty0 * 256) + height))
        # fade the map 50% so the superimposed roads stand out
        crop = Image.blend(crop, Image.new("RGB", crop.size, (255, 255, 255)), 0.5)
        draw = ImageDraw.Draw(crop)
        for pts, color, w in layers:
            px = [tuple(c - o for c, o in zip(_merc_px(lon, lat, z), (x0, y0)))
                  for lon, lat in pts]
            draw.line(px, fill=color, width=w, joint="curve")
        buf = io.BytesIO()
        crop.save(buf, "PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return (f'<img src="data:image/png;base64,{b64}" width="{width}" height="{height}" '
                f'style="border:1px solid #ddd" alt="OSM map z{z}">')
    except Exception as e:
        return (f'<div style="width:{width}px;height:{height}px;border:1px solid #ddd;'
                f'display:flex;align-items:center;justify-content:center;color:#999">'
                f'OSM tiles unavailable: {html.escape(str(e)[:80])}</div>')


def svg_overlay(layers, width=460, height=300):
    """Plain-background overlay of [(pts, css_color, stroke_w)] layers."""
    pxa = [proj(p) for pts, _, _ in layers for p in pts]
    mx0, mx1 = min(p[0] for p in pxa), max(p[0] for p in pxa)
    my0, my1 = min(p[1] for p in pxa), max(p[1] for p in pxa)
    w_m, h_m = max(mx1 - mx0, 1e-6), max(my1 - my0, 1e-6)
    pad = 15
    scale = min((width - 2 * pad) / w_m, (height - 2 * pad) / h_m)

    def path(pts):
        d = []
        for p in pts:
            x, y = proj(p)
            px = pad + (x - mx0) * scale
            py = height - pad - (y - my0) * scale
            d.append(f"{'M' if not d else 'L'}{px:.1f},{py:.1f}")
        return "".join(d)

    bar_m = 10 ** max(0, round(math.log10(w_m / 4))) if w_m > 4 else 1
    bar_px = bar_m * scale
    paths = "".join(
        f'<path d="{path(pts)}" fill="none" stroke="{color}" stroke-width="{sw}" '
        f'stroke-linecap="round" opacity="0.85"/>' for pts, color, sw in layers)
    return (
        f'<svg viewBox="0 0 {width} {height}" style="background:#fafafa;border:1px solid #ddd">'
        + paths +
        f'<line x1="{pad}" y1="{height-6}" x2="{pad+bar_px:.1f}" y2="{height-6}" stroke="#333" stroke-width="2"/>'
        f'<text x="{pad}" y="{height-10}" font-size="10" fill="#333">{bar_m} m</text>'
        "</svg>"
    )


NEIGHBOR_TRIM_M = 75.0

def trim_from(pts, meters):
    out = [pts[0]]
    acc = 0.0
    for i in range(len(pts) - 1):
        a, b = proj(pts[i]), proj(pts[i + 1])
        d = math.dist(a, b)
        if acc + d >= meters:
            t = (meters - acc) / (d or 1.0)
            p, q = pts[i], pts[i + 1]
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            return out
        acc += d
        out.append(pts[i + 1])
    return out


def group_stubs(roads, idx, members):
    """Trimmed polylines of roads connecting to the group from outside."""
    memberset = set(members)
    stubs = []
    for i in members:
        for end in (0, -1):
            p = roads[i]["pts"][end]
            for j, jend in idx[(round(p[0], 7), round(p[1], 7))]:
                if j in memberset:
                    continue
                pts = roads[j]["pts"]
                stubs.append(trim_from(pts if jend == 0 else pts[::-1], NEIGHBOR_TRIM_M))
    return stubs


# ---------------- example galleries ----------------

BLUE, RED = (29, 78, 216), (221, 51, 51)


def spread(items, n=4):
    if not items:
        return []
    idxs = sorted({0, len(items) // 3, 2 * len(items) // 3, len(items) - 1})
    return [items[i] for i in idxs][:n]


def gallery(title, note, groups_):
    out = [f"<h2>{html.escape(title)}</h2><p>{note}</p>"]
    global ex_no
    for subtitle, items in groups_:
        if not items:
            continue
        out.append(f"<h3>{html.escape(subtitle)} — {len(items)} groups</h3><div class='exrow'>")
        for d in spread(items):
            ex_no += 1
            b_pts = [base_roads[i]["pts"] for i in d["bis"]]
            t_pts = [target_roads[i]["pts"] for i in d["tis"]]
            pair_layers = ([(pts, BLUE, 5) for pts in b_pts]
                           + [(pts, RED, 2) for pts in t_pts])
            svg_layers = ([(pts, "#1d4ed8", 4) for pts in b_pts]
                          + [(pts, "#d33", 1.8) for pts in t_pts])
            nbrs_b = group_stubs(base_roads, ep_base, d["bis"])
            nbrs_t = group_stubs(target_roads, ep_target, d["tis"])
            ctx_layers = ([(s, BLUE, 5) for s in nbrs_b]
                          + [(s, RED, 2) for s in nbrs_t] + pair_layers)
            allp = [p for pts in b_pts + t_pts for p in pts]
            ctx_bbox = allp + [q for s in nbrs_b + nbrs_t for q in s]
            cx = sum(p[0] for p in allp) / len(allp)
            cy = sum(p[1] for p in allp) / len(allp)
            lb = sum(polyline_length([proj(p) for p in pts]) for pts in b_pts)
            lt = sum(polyline_length([proj(p) for p in pts]) for pts in t_pts)
            extra = ""
            if d["bulk"] is not None:
                extra = (f" · bulk {d['bulk']:.2f} m · residual {d['residual']:.2f} m"
                         f" · overhang {d['overhang']:.1f} m")
            out.append(
                "<div class='ex'><div class='side'>"
                + svg_overlay(svg_layers)
                + osm_snapshot(pair_layers, allp)
                + osm_snapshot(ctx_layers, ctx_bbox)
                + f"</div><div class='cap'><b>Example {ex_no}</b> · {d['kind']} · "
                f"{html.escape(verdict(d))} · raw {d['raw']:.2f} m{extra}<br>"
                f"Σlen {lb:.0f}→{lt:.0f} m · nbrs {len(nbrs_b)}→{len(nbrs_t)} · "
                f"<a href='https://www.google.com/maps/search/?api=1&query={cy:.6f},{cx:.6f}' "
                f"target='_blank'>{cy:.5f}, {cx:.5f}</a></div></div>"
            )
        out.append("</div>")
    return "".join(out)


def in_range(items, key, lo, hi):
    sel = [d for d in items if d[key] is not None and lo < d[key] <= hi]
    sel.sort(key=lambda d: d[key])
    return sel


one2one = [d for d in records if d["bulk"] is not None and d["raw"] > T_ROUND]
nm_above = [d for d in records if d["bulk"] is None and d["raw"] > T_ROUND]
nm_noise = [d for d in records
            if d["kind"] != "1:1" and 0 < d["raw"] <= T_ROUND]

# ---------------- HTML report ----------------

def hist_svg(bands, counts, title, color):
    width, height = 940, 260
    pad_l, pad_b = 50, 58
    n = len(counts)
    bw = (width - pad_l - 10) / n
    peak = max(max(counts), 1)
    lpeak = math.log10(1 + peak)
    bars = []
    for i, c in enumerate(counts):
        h = (math.log10(1 + c) / lpeak) * (height - pad_b - 24) if c else 0
        x = pad_l + i * bw
        y = height - pad_b - h
        bars.append(
            f'<rect x="{x+4:.1f}" y="{y:.1f}" width="{bw-8:.1f}" height="{h:.1f}" fill="{color}" rx="2"/>'
            f'<text x="{x+bw/2:.1f}" y="{y-4:.1f}" font-size="11" text-anchor="middle" fill="#333">{c}</text>'
            f'<text x="{x+bw/2:.1f}" y="{height-pad_b+14:.1f}" font-size="10" text-anchor="middle" fill="#555" transform="rotate(-25 {x+bw/2:.1f} {height-pad_b+14:.1f})">{html.escape(bands[i][0])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" style="max-width:100%">'
        f'<text x="{pad_l}" y="16" font-size="13" font-weight="600" fill="#222">{html.escape(title)}</text>'
        + "".join(bars) +
        f'<line x1="{pad_l}" y1="{height-pad_b}" x2="{width-10}" y2="{height-pad_b}" stroke="#999"/>'
        "</svg>"
    )


rows = []
rows.append("<h1>Geometry noise tolerance — calibration pass (issue #9)</h1>")
rows.append(
    f"<p>Amersfoort 26330 → 26340. <b>Population:</b> all match groups (GERS overlap "
    f"components, per #7) with roads on both sides: {shape_counts['1:1']} 1:1 + "
    f"{shape_counts['1:N / N:M']} 1:N/N:M judged on <i>union</i> geometry "
    f"({shape_counts.get('oversize (skipped)', 0)} oversize skipped; "
    f"{shape_counts['base-only']} base-only / {shape_counts['target-only']} target-only "
    f"go to the created/deleted channel). Groups whose outer endpoint topology differs "
    f"are routed to the change-group channel: {len(routed_out)}. "
    f"<b>Judged on geometry: {len(records)}</b>, of which {identical} bit-identical.</p>"
    "<p><b>Metrics:</b> <code>raw</code> = symmetric max sampled deviation between the two "
    "sides' union geometries; for 1:1 pairs additionally, on the trimmed common extent: "
    "<code>bulk</code> = best-fit translation, <code>residual</code> = max deviation after "
    "removing bulk, <code>overhang</code> = extent trimmed off (choice-point drift). "
    f"<b>Tiers:</b> raw ≤ {T_ROUND:g} m → noise; bulk &gt; {T_BULK:g} m → REAL (bulk "
    f"correction); residual &gt; {T_SHAPE:g} m → REAL (shape change); else noise (drift). "
    "N:M groups above the rounding tier are the eyeball set.</p>"
)
rows.append("<h2>Verdicts under proposed thresholds</h2><table border='1' cellpadding='4' "
            "style='border-collapse:collapse'><tr><th>verdict</th><th>groups</th></tr>"
            + "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>"
                      for k, v in sorted(verdict_counts.items()))
            + "</table>")

rows.append(hist_svg(RAW_BANDS, band_counts([d["raw"] for d in records], RAW_BANDS),
                     "raw union max_dev, all groups (log-scaled bars)", "#4a7fb5"))
rows.append(hist_svg(BULK_BANDS,
                     band_counts([d["bulk"] for d in records if d["bulk"] is not None],
                                 BULK_BANDS),
                     "bulk offset, 1:1 pairs (log-scaled bars)", "#7a9e5f"))
rows.append(hist_svg(RES_BANDS,
                     band_counts([d["residual"] for d in records if d["residual"] is not None],
                                 RES_BANDS),
                     "shape residual, 1:1 pairs (log-scaled bars)", "#b0713f"))
rows.append(hist_svg(OVH_BANDS,
                     band_counts([d["overhang"] for d in records if d["overhang"] is not None],
                                 OVH_BANDS),
                     "extent overhang, 1:1 pairs (log-scaled bars)", "#8a6fb0"))

rows.append("<p>Blue thick = baseline 26330, red thin = target 26340 (all group members), "
            "on the current OSM map (faded 50%). Third image zooms out to the externally "
            f"connecting merged roads (first {NEIGHBOR_TRIM_M:.0f} m, same colors). Examples "
            "spread across each band (smallest, ~33%, ~66%, largest). Click coords for "
            "aerial imagery.</p>")

ex_no = 0
rows.append(gallery(
    "A · Shape residual around T_SHAPE (1:1)",
    f"Pairs with bulk ≤ {T_BULK:g} m, ordered by residual. The noise/real line for "
    "local shape change sits somewhere in here.",
    [(f"residual {lo:g} – {hi:g} m",
      [d for d in in_range(one2one, "residual", lo, hi) if d["bulk"] <= T_BULK])
     for lo, hi in ((0.3, 0.75), (0.75, 1.5), (1.5, 3.0), (3.0, 6.0), (6.0, 1e9))]))
rows.append(gallery(
    "B · Bulk offset around T_BULK (1:1)",
    f"Pairs with residual ≤ {T_SHAPE:g} m, ordered by bulk. The noise/real line for "
    "whole-corridor shift sits somewhere in here.",
    [(f"bulk {lo:g} – {hi:g} m",
      [d for d in in_range(one2one, "bulk", lo, hi) if d["residual"] <= T_SHAPE])
     for lo, hi in ((1.0, 2.0), (2.0, 3.5), (3.5, 5.0), (5.0, 8.0), (8.0, 1e9))]))
rows.append(gallery(
    "C · Re-partitioned N:M groups classified noise",
    "Union geometry unchanged within the rounding tier, extent re-shuffled between "
    "roads (choice-point drift). Confirm these read as noise.",
    [("raw ≤ rounding tolerance, largest groups first",
      sorted(nm_noise, key=lambda d: -(len(d["bis"]) + len(d["tis"])))[:16])]))
rows.append(gallery(
    "D · N:M groups above the rounding tier — the eyeball set",
    "Union geometries genuinely differ. These need your noise/real verdicts to decide "
    "how the tiers extend to N:M groups.",
    [(f"raw {lo:g} – {hi:g} m", in_range(nm_above, "raw", lo, hi))
     for lo, hi in ((0.1, 0.5), (0.5, 1.5), (1.5, 5.0), (5.0, 1e9))]))
routed_out.sort(key=lambda d: -(len(d["bis"]) + len(d["tis"])))
for d in routed_out:
    if "raw" not in d:
        d["raw"] = multi_deviation([[proj(p) for p in base_roads[i]["pts"]] for i in d["bis"]],
                                   [[proj(p) for p in target_roads[i]["pts"]] for i in d["tis"]])
rows.append(gallery(
    "E · Outer topology differs (routed to change-group channel)",
    "Not judged on geometry — shown for sanity: the group connects to different "
    "match groups across versions.",
    [("largest raw deviation first",
      sorted(routed_out, key=lambda d: -d["raw"])[:12])]))

page = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>Geometry tolerance prototype (#9)</title>"
    "<style>body{font:14px/1.5 system-ui;margin:24px;max-width:1000px}"
    ".exrow{display:flex;flex-wrap:wrap;gap:18px}"
    ".ex{width:940px}.side{display:flex;flex-wrap:wrap;gap:12px}.side svg{width:460px;flex:none}"
    ".cap{font-size:12px;color:#444;margin-top:2px}"
    "code{background:#f0f0f0;padding:1px 4px;border-radius:3px}"
    "table{font-size:13px}</style>"
    + "".join(rows)
)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(page)
print(f"\nwrote {OUT}", flush=True)
