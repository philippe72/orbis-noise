"""PROBE — THROWAWAY (issue #12). Survey: how are buildings stored in the clips?

Answers, before any candidate unit is built:
 - which element kinds carry building=* / building:part=*
 - identifier coverage (gers_identifier, osm_identifier, source_identifier, ref:bag)
 - tag key census on building footprints
 - raw element churn on building elements, per version pair (content-based:
   Orbis resets version/timestamp each release, see #3)
"""
import sys, collections, hashlib
import osmium

CLIPS = {
    "26320": "data/clips/amersfoort_26320.osm.pbf",
    "26330": "data/clips/amersfoort_26330.osm.pbf",
    "26340": "data/clips/amersfoort_26340.osm.pbf",
}
BLD_KEYS = ("building", "building:part")
ID_KEYS = ("gers_identifier", "osm_identifier", "source_identifier",
           "source_identifier:internal", "ref:bag")


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.nodes = {}   # building-tagged nodes: id -> tags
        self.ways = {}    # building-tagged ways:  id -> (tags, refs)
        self.rels = {}    # building-ish relations: id -> (tags, members)

    def node(self, n):
        t = {x.k: x.v for x in n.tags}
        if any(k in t for k in BLD_KEYS):
            self.nodes[n.id] = t

    def way(self, w):
        t = {x.k: x.v for x in w.tags}
        if any(k in t for k in BLD_KEYS):
            self.ways[w.id] = (t, [n.ref for n in w.nodes])

    def relation(self, r):
        t = {x.k: x.v for x in r.tags}
        if any(k in t for k in BLD_KEYS) or t.get("type") in ("building", "multipolygon"):
            self.rels[r.id] = (t, [(m.type, m.ref, m.role) for m in r.members])


def content_hash(tags, geom):
    h = hashlib.blake2b(digest_size=12)
    for k in sorted(tags):
        h.update(k.encode()); h.update(b"\x00"); h.update(tags[k].encode()); h.update(b"\x01")
    h.update(b"\x02")
    h.update(repr(geom).encode())
    return h.digest()


loaded = {}
for ver, path in CLIPS.items():
    c = Collector()
    c.apply_file(path)
    loaded[ver] = c
    print(f"[{ver}] building nodes={len(c.nodes)} ways={len(c.ways)} rels={len(c.rels)}")

print()
# ---- tag census on 26330 building ways ----
c = loaded["26330"]
keys = collections.Counter()
bvals = collections.Counter()
for wid, (t, refs) in c.ways.items():
    for k in t:
        keys[k] += 1
    bvals[t.get("building", t.get("building:part", "?"))] += 1
n = len(c.ways)
print(f"--- 26330: tag keys on {n} building ways (count, pct) ---")
for k, v in keys.most_common(60):
    print(f"  {v:7d} {100*v/n:6.2f}%  {k}")
print(f"--- building / building:part values ---")
for k, v in bvals.most_common(25):
    print(f"  {v:7d}  {k}")

print()
print("--- identifier coverage on 26330 building ways ---")
for k in ID_KEYS:
    have = [wid for wid, (t, _) in c.ways.items() if k in t]
    vals = collections.Counter(c.ways[w][0][k] for w in have)
    dup = sum(v for v in vals.values() if v > 1)
    print(f"  {k:32s} present={len(have):7d} ({100*len(have)/n:6.2f}%) distinct={len(vals):7d} in-dup-values={dup}")

print()
print("--- relation types among building-ish relations (26330) ---")
rt = collections.Counter(t.get("type", "<none>") for t, _ in c.rels.values())
for k, v in rt.most_common():
    print(f"  {v:7d}  type={k}")
rbld = sum(1 for t, _ in c.rels.values() if any(kk in t for kk in BLD_KEYS))
print(f"  of which carry a building key: {rbld}")

print()
# ---- churn per version pair, content-based ----
def sig(c):
    out = {}
    for wid, (t, refs) in c.ways.items():
        out[("w", wid)] = content_hash(t, refs)
    for nid, t in c.nodes.items():
        out[("n", nid)] = content_hash(t, None)
    for rid, (t, mem) in c.rels.items():
        out[("r", rid)] = content_hash(t, mem)
    return out

sigs = {v: sig(c) for v, c in loaded.items()}
for a, b in (("26320", "26330"), ("26330", "26340"), ("26320", "26340")):
    sa, sb = sigs[a], sigs[b]
    created = set(sb) - set(sa)
    deleted = set(sa) - set(sb)
    modified = {k for k in set(sa) & set(sb) if sa[k] != sb[k]}
    print(f"{a}->{b}: created={len(created)} deleted={len(deleted)} modified={len(modified)}")
