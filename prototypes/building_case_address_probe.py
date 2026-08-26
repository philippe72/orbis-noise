"""PROBE 7 - THROWAWAY (issue #12). Do the four candidate-C cases change reality?

Probe 6 printed the only four cases in all of NLD where candidate C's rule fires:
the drawn shape is unchanged to millimetres, attribution is identical, only the
internal cut between footprints moved.

  SPLIT w4884694193  265.3 m2 -> 174.8 + 90.4 m2, deviation 0.007 m
  SPLIT w6200200231  309.5 m2 -> 216.4 + 93.1 m2, deviation 0.007 m
  SPLIT w7758867195   39.0 m2 ->   5.3 + 32.9 m2, deviation 0.064 m
  MERGE w7896416502   16.1 m2 <-   9.6 +  6.5 m2, deviation 0.007 m

Whether those are noise or real turns on one question: was the reality already
two buildings before the split? Addresses answer it. If the baseline footprint
already contained two distinct addresses, then reality held two buildings all
along, and the split is the model catching up to it. If the address count moves
with the footprint count, the modeled reality moved too.

Inputs, all made with osmium (no node index needed, so no out-of-memory):
  data/buildings/{deleted_26320,created_26340}.osm.pbf   - osmium getid -r
  data/buildings/nld_{26320,26340}_addr.osm.pbf          - osmium tags-filter -R
      n/addr:housenumber n/address_point   (12.2M address nodes per version)
"""
import math

import osmium
from shapely.geometry import Polygon, Point

BLD_KEYS = ("building", "building:part")
ID_KEYS = ("gers_identifier", "osm_identifier")
DEG_M = 111320.0
PAD = 0.0012          # bbox pad around each case, in degrees (~90-130 m)

# case name -> (kind, source way, counterpart ways)
CASES = [
    ("case1 SPLIT", "SPLIT", 4884694193, [7898103733, 7899075768]),
    ("case2 SPLIT", "SPLIT", 6200200231, [7897429778, 7894994925]),
    ("case3 SPLIT", "SPLIT", 7758867195, [7898452300, 7896023326]),
    ("case4 MERGE", "MERGE", 7896416502, [7800708716, 7798292724]),
]
WANT = {w for _, _, s, ds in CASES for w in [s] + ds}


def attr(t):
    skip = {"layer_id", "license", "license_zone", "supported", "data_size_index"}
    return {k: v for k, v in t.items()
            if k not in skip and k not in ID_KEYS
            and not k.startswith("layer_id:") and not k.startswith("license:")}


class Ways(osmium.SimpleHandler):
    """The case footprints and their node coordinates."""

    def __init__(self):
        super().__init__()
        self.ways = {}
        self.loc = {}

    def node(self, n):
        self.loc[n.id] = (n.location.lon, n.location.lat)

    def way(self, w):
        if w.id in WANT:
            self.ways[w.id] = ({x.k: x.v for x in w.tags}, [n.ref for n in w.nodes])


class Addr(osmium.SimpleHandler):
    """Address nodes inside any case bbox."""

    def __init__(self, boxes):
        super().__init__()
        self.boxes = boxes
        self.addr = {}

    def node(self, n):
        lon, lat = n.location.lon, n.location.lat
        for lo, la, hi, ha in self.boxes:
            if lo <= lon <= hi and la <= lat <= ha:
                self.addr[n.id] = ({x.k: x.v for x in n.tags}, lon, lat)
                return


wd = Ways()
wd.apply_file("data/buildings/deleted_26320.osm.pbf")
wc = Ways()
wc.apply_file("data/buildings/created_26340.osm.pbf")
side = {}
for wid, v in wd.ways.items():
    side.setdefault(wid, {})["26320"] = (v, wd.loc)
for wid, v in wc.ways.items():
    side.setdefault(wid, {})["26340"] = (v, wc.loc)
missing = WANT - set(side)
print(f"case footprints found: {len(side)}/{len(WANT)}"
      + (f"  MISSING {sorted(missing)}" if missing else ""))


def ll_ring(v, loc):
    (t, refs) = v
    ll = [loc[r] for r in refs if r in loc]
    if len(ll) < 3:
        return None
    if ll[0] != ll[-1]:
        ll.append(ll[0])
    return ll


def poly_of(ll):
    lat0 = sum(p[1] for p in ll) / len(ll)
    kx = DEG_M * math.cos(math.radians(lat0))
    p = Polygon([(lon * kx, lat * DEG_M) for lon, lat in ll])
    return (p if p.is_valid else p.buffer(0)), kx, lat0


boxes = []
centres = {}
for name, kind, src, dsts in CASES:
    ver = "26320" if kind == "SPLIT" else "26340"
    v, loc = side[src][ver]
    ll = ll_ring(v, loc)
    lons = [p[0] for p in ll]
    lats = [p[1] for p in ll]
    boxes.append((min(lons) - PAD, min(lats) - PAD, max(lons) + PAD, max(lats) + PAD))
    centres[name] = (sum(lons) / len(lons), sum(lats) / len(lats))
print(f"scanning {len(boxes)} case bboxes for address nodes", flush=True)

ADDR = {}
for ver, path in (("26320", "data/buildings/nld_26320_addr.osm.pbf"),
                  ("26340", "data/buildings/nld_26340_addr.osm.pbf")):
    a = Addr(boxes)
    a.apply_file(path)
    ADDR[ver] = a.addr
    print(f"  [{ver}] {len(a.addr)} address nodes in the case bboxes", flush=True)
print()


def label(t):
    return (t.get("addr:street:nl-Latn", t.get("addr:street", "")),
            t.get("addr:housenumber", t.get("addr:housenumber:nl-Latn", "")),
            t.get("addr:housenumber:suffix", ""))


def addrs_in(ll, ver):
    p, kx, _ = poly_of(ll)
    out = []
    for nid, (t, lon, lat) in ADDR[ver].items():
        if p.covers(Point(lon * kx, lat * DEG_M)):
            out.append((nid, label(t)))
    return out


for name, kind, src, dsts in CASES:
    vsrc, vdst = ("26320", "26340") if kind == "SPLIT" else ("26340", "26320")
    lls = ll_ring(*side[src][vsrc])
    lon, lat = centres[name]
    print(f"=== {name} w{src} ===")
    print(f"  https://www.openstreetmap.org/#map=19/{lat:.6f}/{lon:.6f}")
    p, _, _ = poly_of(lls)
    print(f"  {vsrc} source w{src} area={p.area:.1f} m2 "
          f"attr={attr(side[src][vsrc][0][0])}")
    # addresses inside the source footprint, in BOTH versions
    for ver in ("26320", "26340"):
        aa = addrs_in(lls, ver)
        print(f"    addresses inside that outline in {ver}: {len(aa)} "
              f"-> {sorted(set(l for _, l in aa))}")
    for d in dsts:
        if vdst not in side.get(d, {}):
            print(f"  !! counterpart w{d} not loaded for {vdst}")
            continue
        lld = ll_ring(*side[d][vdst])
        pd, _, _ = poly_of(lld)
        print(f"  {vdst} counterpart w{d} area={pd.area:.1f} m2 "
              f"attr={attr(side[d][vdst][0][0])}")
        for ver in ("26320", "26340"):
            ad = addrs_in(lld, ver)
            print(f"    addresses inside it in {ver}: {len(ad)} "
                  f"-> {sorted(set(l for _, l in ad))}")
    print()
