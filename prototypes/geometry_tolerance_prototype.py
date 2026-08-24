"""PROTOTYPE — THROWAWAY (issue #9). Not production code.

Question: where is the line between a rounding shift and a real geometry
change between two map versions?

Builds merged roads (bivalent-node chains, geometry only) for two Amersfoort
clips, pairs them identity-first by identical GERS sets (the clean 1:1
population of #7's match groups), and computes geometry deviation metrics
per pair:

  max_dev  — symmetric max sampled point-to-polyline distance (~Hausdorff)
  mean_dev — symmetric mean sampled distance (~area-between-curves / length)

Renders the distribution on a log scale plus concrete before/after overlay
examples per deviation band, so the human can eyeball where noise stops and
real geometry change starts.

Run:  .venv-research/Scripts/python.exe prototypes/geometry_tolerance_prototype.py
Output: stdout summary + prototypes/output/geometry_tolerance_report.html
"""

import collections
import html
import math
import os
import sys

import osmium

sys.stdout.reconfigure(encoding="utf-8")

BASE_CLIP = sys.argv[1] if len(sys.argv) > 1 else "data/clips/amersfoort_26330.osm.pbf"
TARGET_CLIP = sys.argv[2] if len(sys.argv) > 2 else "data/clips/amersfoort_26340.osm.pbf"
OUT = "prototypes/output/geometry_tolerance_report.html"

SAMPLE_STEP_M = 2.0     # densification step along each polyline
MAX_SAMPLES = 400       # cap per polyline (long roads)

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


def densify(xy, step, cap):
    """Sample points along polyline every `step` m (>= vertices? no: pure
    arc-length samples incl. endpoints), capped at `cap` samples."""
    total = polyline_length(xy)
    n = min(cap, max(2, int(total / step) + 1))
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


# ---------------- run ----------------

base_roads = load_merged_roads(BASE_CLIP)
target_roads = load_merged_roads(TARGET_CLIP)

by_gers_base = collections.defaultdict(list)
by_gers_target = collections.defaultdict(list)
for i, r in enumerate(base_roads):
    for g in r["gers"]:
        by_gers_base[g].append(i)
for i, r in enumerate(target_roads):
    for g in r["gers"]:
        by_gers_target[g].append(i)

# clean 1:1 population: identical non-empty GERS set on exactly one road each side
base_by_set = collections.defaultdict(list)
target_by_set = collections.defaultdict(list)
for i, r in enumerate(base_roads):
    if r["gers"]:
        base_by_set[r["gers"]].append(i)
for i, r in enumerate(target_roads):
    if r["gers"]:
        target_by_set[r["gers"]].append(i)

pairs = []
stats = collections.Counter(
    base_roads=len(base_roads), target_roads=len(target_roads),
    base_no_gers=sum(1 for r in base_roads if not r["gers"]),
    target_no_gers=sum(1 for r in target_roads if not r["gers"]),
)
for gset, bidx in base_by_set.items():
    tidx = target_by_set.get(gset)
    if tidx and len(bidx) == 1 and len(tidx) == 1:
        pairs.append((bidx[0], tidx[0]))
stats["clean_pairs"] = len(pairs)
print(f"clean identical-GERS-set 1:1 pairs: {len(pairs)}", flush=True)

all_pts = [p for r in base_roads for p in r["pts"][:1]]
proj = make_projector(all_pts)

results = []  # (max_dev, mean_dev, bi, ti, len_base, len_target)
identical = 0
for bi, ti in pairs:
    pa, pb = base_roads[bi]["pts"], target_roads[ti]["pts"]
    if pa == pb:
        identical += 1
        results.append((0.0, 0.0, bi, ti, 0.0, 0.0))
        continue
    xa = [proj(p) for p in pa]
    xb = [proj(p) for p in pb]
    mx, mn = deviation(xa, xb)
    results.append((mx, mn, bi, ti, polyline_length(xa), polyline_length(xb)))
print(f"  {identical} pairs bit-identical geometry", flush=True)

# ---------------- distribution ----------------

BANDS = [
    ("= 0 (bit-identical)", lambda d: d == 0.0),
    ("0 – 1 cm", lambda d: 0 < d <= 0.01),
    ("1 – 10 cm", lambda d: 0.01 < d <= 0.10),
    ("10 – 25 cm", lambda d: 0.10 < d <= 0.25),
    ("25 – 50 cm", lambda d: 0.25 < d <= 0.50),
    ("0.5 – 1 m", lambda d: 0.50 < d <= 1.0),
    ("1 – 2 m", lambda d: 1.0 < d <= 2.0),
    ("2 – 5 m", lambda d: 2.0 < d <= 5.0),
    ("5 – 15 m", lambda d: 5.0 < d <= 15.0),
    ("> 15 m", lambda d: d > 15.0),
]

def band_counts(vals):
    return [sum(1 for v in vals if f(v)) for _, f in BANDS]

max_devs = [r[0] for r in results]
mean_devs = [r[1] for r in results]
bc_max = band_counts(max_devs)
bc_mean = band_counts(mean_devs)

print("\nmax_dev distribution:")
for (label, _), c in zip(BANDS, bc_max):
    print(f"  {label:>22}: {c}")

# ---------------- examples per band (nonzero bands) ----------------

def svg_overlay(pa, pb, width=460, height=300):
    xs = [p[0] for p in pa + pb]
    ys = [p[1] for p in pa + pb]
    xa = [proj(p) for p in pa]
    # scale in meters for a scale bar
    pxa = [proj(p) for p in pa + pb]
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
    return (
        f'<svg viewBox="0 0 {width} {height}" style="background:#fafafa;border:1px solid #ddd">'
        f'<path d="{path(pa)}" fill="none" stroke="#888" stroke-width="4" stroke-linecap="round" opacity="0.8"/>'
        f'<path d="{path(pb)}" fill="none" stroke="#d33" stroke-width="1.8" stroke-linecap="round"/>'
        f'<line x1="{pad}" y1="{height-6}" x2="{pad+bar_px:.1f}" y2="{height-6}" stroke="#333" stroke-width="2"/>'
        f'<text x="{pad}" y="{height-10}" font-size="10" fill="#333">{bar_m} m</text>'
        "</svg>"
    )


EX_PER_BAND = 4
examples_by_band = []
for label, f in BANDS[1:]:
    in_band = [r for r in results if f(r[0])]
    in_band.sort(key=lambda r: r[0])
    picks = []
    if in_band:
        idxs = {0, len(in_band) // 3, 2 * len(in_band) // 3, len(in_band) - 1}
        picks = [in_band[i] for i in sorted(idxs)][:EX_PER_BAND]
    examples_by_band.append((label, len(in_band), picks))

# ---------------- HTML report ----------------

def hist_svg(counts, title, color):
    width, height = 940, 260
    pad_l, pad_b = 50, 58
    n = len(counts)
    bw = (width - pad_l - 10) / n
    peak = max(max(counts), 1)
    # log-count y-axis: bars scaled by log10(1+c)
    lpeak = math.log10(1 + peak)
    bars = []
    for i, c in enumerate(counts):
        h = (math.log10(1 + c) / lpeak) * (height - pad_b - 24) if c else 0
        x = pad_l + i * bw
        y = height - pad_b - h
        bars.append(
            f'<rect x="{x+4:.1f}" y="{y:.1f}" width="{bw-8:.1f}" height="{h:.1f}" fill="{color}" rx="2"/>'
            f'<text x="{x+bw/2:.1f}" y="{y-4:.1f}" font-size="11" text-anchor="middle" fill="#333">{c}</text>'
            f'<text x="{x+bw/2:.1f}" y="{height-pad_b+14:.1f}" font-size="10" text-anchor="middle" fill="#555" transform="rotate(-25 {x+bw/2:.1f} {height-pad_b+14:.1f})">{html.escape(BANDS[i][0])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" style="max-width:100%">'
        f'<text x="{pad_l}" y="16" font-size="13" font-weight="600" fill="#222">{html.escape(title)}</text>'
        + "".join(bars) +
        f'<line x1="{pad_l}" y1="{height-pad_b}" x2="{width-10}" y2="{height-pad_b}" stroke="#999"/>'
        "</svg>"
    )


rows = []
rows.append("<h1>Geometry noise tolerance — Amersfoort 26330 → 26340 (issue #9)</h1>")
rows.append(
    "<p><b>Population:</b> merged roads paired identity-first by <i>identical GERS set</i>, "
    "1:1 groups only — same road, same extent, both versions. "
    f"{stats['base_roads']} / {stats['target_roads']} merged roads (base/target); "
    f"{stats['clean_pairs']} clean pairs compared; "
    f"{identical} of them bit-identical. "
    f"Roads without any GERS id: {stats['base_no_gers']} base / {stats['target_no_gers']} target (excluded).</p>"
    "<p><b>Metrics:</b> <code>max_dev</code> = symmetric max sampled point-to-polyline distance (≈ Hausdorff); "
    "<code>mean_dev</code> = symmetric mean sampled distance (≈ area between curves per unit length). "
    f"Sampling step {SAMPLE_STEP_M} m, cap {MAX_SAMPLES} samples/line. Bar heights log-scaled.</p>"
)
rows.append(hist_svg(bc_max, "max_dev distribution (count per band, log-scaled bars)", "#4a7fb5"))
rows.append(hist_svg(bc_mean, "mean_dev distribution (count per band, log-scaled bars)", "#7a9e5f"))

rows.append("<h2>Examples per max_dev band</h2>"
            "<p>Gray thick = baseline 26330, red thin = target 26340. Spread across the band "
            "(smallest, ~33%, ~66%, largest). Click coords to check aerial imagery.</p>")
for label, count, picks in examples_by_band:
    if not count:
        continue
    rows.append(f"<h3>{html.escape(label)} — {count} pairs</h3><div class='exrow'>")
    for mx, mn, bi, ti, lb, lt in picks:
        pa, pb = base_roads[bi]["pts"], target_roads[ti]["pts"]
        cx = sum(p[0] for p in pa) / len(pa)
        cy = sum(p[1] for p in pa) / len(pa)
        g = sorted(base_roads[bi]["gers"])
        rows.append(
            "<div class='ex'>"
            + svg_overlay(pa, pb)
            + f"<div class='cap'>max {mx:.2f} m · mean {mn:.2f} m · len {lb:.0f}→{lt:.0f} m · "
            f"{base_roads[bi]['nways']}→{target_roads[ti]['nways']} ways<br>"
            f"<a href='https://www.google.com/maps/search/?api=1&query={cy:.6f},{cx:.6f}' target='_blank'>{cy:.5f}, {cx:.5f}</a>"
            f" · gers {html.escape(g[0][:16])}…({len(g)})</div></div>"
        )
    rows.append("</div>")

page = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>Geometry tolerance prototype (#9)</title>"
    "<style>body{font:14px/1.5 system-ui;margin:24px;max-width:1000px}"
    ".exrow{display:flex;flex-wrap:wrap;gap:14px}"
    ".ex{width:462px}.cap{font-size:12px;color:#444;margin-top:2px}"
    "code{background:#f0f0f0;padding:1px 4px;border-radius:3px}</style>"
    + "".join(rows)
)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(page)
print(f"\nwrote {OUT}", flush=True)
