"""PROBE 3 - THROWAWAY (issue #12). NLD-wide building churn, to find split/merge.

The Amersfoort clip has almost no building churn (probe 1: 26320->26340 = 15
created / 26 deleted / 2 modified over 152,387 footprints), so the ticket's
"real split/merge cases" are not in it. This probe widens to the whole of NLD
using building-only extracts made with osmium tags-filter -R (ways + relations,
NO node coordinates - 12.13M building ways each):

    osmium tags-filter -R -o data/buildings/nld_<ver>_buildings.osm.pbf \
        data/orbis/orbis_nexventura_<ver>_..._global_nld.osm.pbf \
        w/building w/building:part r/type=building r/building

Pass 1  : way id -> 8-byte content digest (tags + node refs), per map version.
Pass 2  : full tags + refs for the changed ids only.
Analysis: a split/merge shows up as a deleted (or shrunk) footprint whose node
          refs are re-used by created footprints. Node refs need no coordinates,
          so this runs on the coordinate-free extracts.

Output: prototypes/output/building_churn_nld.json (the changed set, with the
split/merge candidate components), for the candidate-unit comparison to consume.
"""
import collections
import hashlib
import json
import sys
import time

import osmium

BASE = "data/buildings/nld_26320_buildings.osm.pbf"
TARGET = "data/buildings/nld_26340_buildings.osm.pbf"
OUT = "prototypes/output/building_churn_nld.json"


def digest(tags, refs):
    h = hashlib.blake2b(digest_size=8)
    for k in sorted(tags):
        h.update(k.encode())
        h.update(b"\x00")
        h.update(tags[k].encode())
        h.update(b"\x01")
    h.update(b"\x02")
    for r in refs:
        h.update(r.to_bytes(8, "little", signed=True))
    return h.digest()


class Sig(osmium.SimpleHandler):
    """Pass 1: content digest for every building way and relation."""

    def __init__(self):
        super().__init__()
        self.w = {}
        self.r = {}

    def way(self, w):
        self.w[w.id] = digest({x.k: x.v for x in w.tags}, [n.ref for n in w.nodes])

    def relation(self, r):
        mem = []
        for m in r.members:
            mem.append(hash((m.type, m.ref, m.role)) & 0xFFFFFFFF)
        self.r[r.id] = digest({x.k: x.v for x in r.tags}, mem)


class Fetch(osmium.SimpleHandler):
    """Pass 2: full content for a small set of way / relation ids."""

    def __init__(self, wid, rid):
        super().__init__()
        self.wid = wid
        self.rid = rid
        self.ways = {}
        self.rels = {}

    def way(self, w):
        if w.id in self.wid:
            self.ways[w.id] = ({x.k: x.v for x in w.tags}, [n.ref for n in w.nodes])

    def relation(self, r):
        if r.id in self.rid:
            self.rels[r.id] = ({x.k: x.v for x in r.tags},
                               [(m.type, m.ref, m.role) for m in r.members])


t0 = time.time()
sig = {}
for label, path in (("base", BASE), ("target", TARGET)):
    s = Sig()
    s.apply_file(path)
    sig[label] = s
    print(f"[{label}] ways={len(s.w)} rels={len(s.r)}  ({time.time()-t0:.0f}s)", flush=True)

a, b = sig["base"], sig["target"]
w_created = set(b.w) - set(a.w)
w_deleted = set(a.w) - set(b.w)
w_modified = {k for k in a.w.keys() & b.w.keys() if a.w[k] != b.w[k]}
r_created = set(b.r) - set(a.r)
r_deleted = set(a.r) - set(b.r)
r_modified = {k for k in a.r.keys() & b.r.keys() if a.r[k] != b.r[k]}
print()
print("--- NLD-wide building churn 26320 -> 26340 ---")
print(f"  ways      : created={len(w_created)} deleted={len(w_deleted)} modified={len(w_modified)}")
print(f"  relations : created={len(r_created)} deleted={len(r_deleted)} modified={len(r_modified)}")
print(f"  total building ways: base={len(a.w)} target={len(b.w)}")
del sig, a, b
print(f"  ({time.time()-t0:.0f}s)", flush=True)

wids = w_created | w_deleted | w_modified
rids = r_created | r_deleted | r_modified
if not wids:
    print("no way churn at all - nothing to sample")
    sys.exit(0)

fetched = {}
for label, path in (("base", BASE), ("target", TARGET)):
    f = Fetch(wids, rids)
    f.apply_file(path)
    fetched[label] = f
    print(f"[{label}] fetched ways={len(f.ways)} rels={len(f.rels)}  ({time.time()-t0:.0f}s)", flush=True)

FA, FB = fetched["base"], fetched["target"]

# ---- split/merge candidates: shared node refs between the two sides ----
# node ref -> which changed ways use it, per side
use_a = collections.defaultdict(set)
use_b = collections.defaultdict(set)
for wid, (t, refs) in FA.ways.items():
    for r in refs:
        use_a[r].add(wid)
for wid, (t, refs) in FB.ways.items():
    for r in refs:
        use_b[r].add(wid)

# union-find over (side, way id) linked by a shared node ref
parent = {}


def find(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(x, y):
    rx, ry = find(x), find(y)
    if rx != ry:
        parent[ry] = rx


for wid in FA.ways:
    find(("a", wid))
for wid in FB.ways:
    find(("b", wid))
for ref in set(use_a) | set(use_b):
    members = [("a", w) for w in use_a.get(ref, ())] + [("b", w) for w in use_b.get(ref, ())]
    for m in members[1:]:
        union(members[0], m)

comp = collections.defaultdict(list)
for x in list(parent):
    comp[find(x)].append(x)

shape = collections.Counter()
interesting = []
for root, members in comp.items():
    na = sum(1 for s, _ in members if s == "a")
    nb = sum(1 for s, _ in members if s == "b")
    shape[(na, nb)] += 1
    if na >= 1 and nb >= 1 and (na > 1 or nb > 1):
        interesting.append(members)

print()
print("--- node-ref-linked components over the changed footprints ---")
print("  (baseline count, target count) -> how many components")
for k, v in sorted(shape.items(), key=lambda kv: -kv[1])[:20]:
    print(f"    {k} -> {v}")
print(f"  candidate split/merge components (1:N or N:M with both sides): {len(interesting)}")


def attr(t):
    skip = {"layer_id", "license", "license_zone", "supported", "data_size_index"}
    return {k: v for k, v in t.items()
            if k not in skip and not k.startswith("layer_id:") and not k.startswith("license:")}


for members in sorted(interesting, key=len, reverse=True)[:8]:
    na = [w for s, w in members if s == "a"]
    nb = [w for s, w in members if s == "b"]
    print(f"\n  component {len(na)}:{len(nb)}")
    for w in na[:6]:
        t, refs = FA.ways[w]
        print(f"    base   w{w} nodes={len(refs)} {attr(t)}")
    for w in nb[:6]:
        t, refs = FB.ways[w]
        print(f"    target w{w} nodes={len(refs)} {attr(t)}")

# ---- how the created/deleted footprints look identity-wise ----
def idcount(ways, key):
    return sum(1 for t, _ in ways if key in t)


print()
print("--- identity on the churned footprints ---")
for name, F, ids in (("deleted", FA, w_deleted), ("created", FB, w_created),
                     ("modified/base", FA, w_modified), ("modified/target", FB, w_modified)):
    got = [F.ways[w] for w in ids if w in F.ways]
    if not got:
        continue
    print(f"  {name:16s} n={len(got):6d} gers={idcount(got,'gers_identifier'):6d} "
          f"osm={idcount(got,'osm_identifier'):6d} "
          f"parts={sum(1 for t,_ in got if 'building:part' in t):5d}")

# gers survival: deleted gers values reappearing on created footprints
gers_del = {FA.ways[w][0].get("gers_identifier"): w for w in w_deleted if w in FA.ways}
gers_new = {FB.ways[w][0].get("gers_identifier"): w for w in w_created if w in FB.ways}
gers_del.pop(None, None)
gers_new.pop(None, None)
shared = set(gers_del) & set(gers_new)
print(f"  gers values on both a deleted and a created footprint (id churn): {len(shared)}")
osm_del = collections.Counter(FA.ways[w][0].get("osm_identifier") for w in w_deleted if w in FA.ways)
osm_new = collections.Counter(FB.ways[w][0].get("osm_identifier") for w in w_created if w in FB.ways)
osm_del.pop(None, None)
osm_new.pop(None, None)
print(f"  osm_identifier values on both sides of the churn: {len(set(osm_del) & set(osm_new))}")

# ---- what changed on the modified footprints: tags only, geometry only, both ----
tags_only = geom_only = both = 0
for w in w_modified:
    if w not in FA.ways or w not in FB.ways:
        continue
    ta, ra = FA.ways[w]
    tb, rb = FB.ways[w]
    dt = ta != tb
    dg = ra != rb
    if dt and dg:
        both += 1
    elif dt:
        tags_only += 1
    elif dg:
        geom_only += 1
print()
print("--- what changed on modified footprints (node refs, not coordinates) ---")
print(f"  tags only={tags_only}  node-refs only={geom_only}  both={both}")
keych = collections.Counter()
for w in w_modified:
    if w not in FA.ways or w not in FB.ways:
        continue
    ta, tb = FA.ways[w][0], FB.ways[w][0]
    for k in set(ta) | set(tb):
        if ta.get(k) != tb.get(k):
            keych[k] += 1
print(f"  changed tag keys: {dict(keych.most_common(15))}")

payload = {
    "counts": {
        "base_ways": len(FA.ways) and None or None,
        "w_created": len(w_created), "w_deleted": len(w_deleted), "w_modified": len(w_modified),
        "r_created": len(r_created), "r_deleted": len(r_deleted), "r_modified": len(r_modified),
    },
    "w_created": sorted(w_created), "w_deleted": sorted(w_deleted),
    "w_modified": sorted(w_modified),
    "r_created": sorted(r_created), "r_deleted": sorted(r_deleted),
    "r_modified": sorted(r_modified),
    "base": {str(w): [FA.ways[w][0], FA.ways[w][1]] for w in FA.ways},
    "target": {str(w): [FB.ways[w][0], FB.ways[w][1]] for w in FB.ways},
    "base_rels": {str(r): [FA.rels[r][0], FA.rels[r][1]] for r in FA.rels},
    "target_rels": {str(r): [FB.rels[r][0], FB.rels[r][1]] for r in FB.rels},
    "components": [[[s, w] for s, w in m] for m in interesting],
}
with open(OUT, "w") as fh:
    json.dump(payload, fh)
print(f"\nwrote {OUT}  ({time.time()-t0:.0f}s)")
