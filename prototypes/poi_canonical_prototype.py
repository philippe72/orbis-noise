"""PROTOTYPE — THROWAWAY (issue #11). Not production code.

Question: do the POI working hypotheses from #5 hold on real clip churn?

Hypotheses under test (diff Amersfoort 26330 -> 26340):
 - canonical POI = point + attribution
 - identity via identifiers, geometry+tag matching as fallback
 - label/outline twins collapse (outline geometry + label attribution,
   joined via the is_same relation / shared osm_identifier)
 - node<->area representation flips are noise
 - chunked key#N# tags joined; list values treated as sets

Adequate iff: chunk re-flow invisible, stations-e-style real edits surface,
twin rewiring invisible, no false created/deleted floods.

Empirical corrections found while building (kept, they ARE the findings):
 - source_identifier:internal is NOT per-feature for EV charging POIs:
   [OperatorId|...] values are shared by up to 273 nodes. Identity therefore
   uses ALL identifier tags as match-group link keys, each key only where its
   value is unambiguous (one feature per side); gers_identifier /
   source_identifier / osm_identifier are unique where present.
 - POI-class selection: class keys amenity/shop/tourism/office/craft/
   healthcare/emergency/man_made/historic/sport, minus road ways
   (highway=* ways carry man_made etc.).
 - POI-class relations (man_made multipolygons) mostly carry NO identifiers,
   so they need member-derived centroid geometry or every one of them floods
   the ledger as a created+deleted pair even when unchanged.
 - node<->area flips carry two representation-artifact tags that make every
   flip 'real' by construction until classified: geometry_type (literally the
   storage form, area<->point) and zoomlevel_min (dropped on area->node).
   Verdict-setting candidates for docs/tag-classification.md.

Result (26330 -> 26340, run 2026-08-25): 406 raw element changes in POI scope
-> 328 real attribution/geometry changes, +23 created, -11 deleted, 14
noise-only groups. Chunk re-flow invisible (stations-e node 33246478484
surfaces as exactly 1 removed provider entry in each of 2 logical tags);
twin rewiring invisible; created/deleted collapse from raw +53/-37 with no
false floods (survivors are plausibly real: benches, recycling points,
chargers, a school). The big real blocks are EV feed sweeps:
payment:service_provider edits on 153 stations, charging_when_closed removed
on 86, vehicle_access:hgv:conditional added on 41.

Run:  .venv-research/Scripts/python.exe prototypes/poi_canonical_prototype.py
Output: stdout summary + prototypes/output/poi_canonical_ledger.html
"""

import collections
import html
import math
import re
import sys

import osmium

sys.stdout.reconfigure(encoding="utf-8")

CLIP_A = "data/clips/amersfoort_26330.osm.pbf"   # baseline map
CLIP_B = "data/clips/amersfoort_26340.osm.pbf"   # target map
OUT = "prototypes/output/poi_canonical_ledger.html"

POI_KEYS = ("amenity", "shop", "tourism", "office", "craft", "healthcare",
            "emergency", "man_made", "historic", "sport")
ID_KEYS = ("gers_identifier", "osm_identifier", "source_identifier",
           "source_identifier:internal")

# per-class geometry tolerance parameters (provisional, POI point class)
T_ROUND_M = 0.10   # <= rounding noise
T_DRIFT_M = 5.0    # <= drift noise ("POI node moved a meter"); above: real move
FALLBACK_MATCH_M = 50.0  # geometric fallback matching radius

STATIONS_E_NODE = 33246478484  # the #5 decisive chunk-re-flow case

CHUNK_RE = re.compile(r"^(.*)#(\d+)#$")


# ---------------- tag classification (same rules as #8, see docs/tag-classification.md) ----------------

def tag_class(k: str) -> str:
    if k in ID_KEYS:
        return "identity"
    if k in ("license_zone", "supported", "data_size_index"):
        return "meta"
    if k == "layer_id" or k.startswith("layer_id:"):
        return "meta"
    if k == "license" or k.startswith("license:"):
        return "meta"
    return "attribution"


# ---------------- canonical attribution ----------------

def join_chunks(tags: dict) -> dict:
    """Join key#N# chunk tags (in N order) into one logical tag per base key."""
    out, chunks = {}, collections.defaultdict(dict)
    for k, v in tags.items():
        m = CHUNK_RE.match(k)
        if m:
            chunks[m.group(1)][int(m.group(2))] = v
        else:
            out[k] = v
    for base, parts in chunks.items():
        joined = "".join(parts[i] for i in sorted(parts))
        if base in out:  # both bare key and chunked key present: keep both visible
            out[base + "#joined"] = joined
        else:
            out[base] = joined
    return out


def canonical_value(v: str):
    """Canonical list value: ';'-separated values become frozen multisets so
    re-ordering is noise; scalars stay strings."""
    if ";" in v:
        return frozenset(collections.Counter(p for p in v.split(";")).items())
    return v


def canonical_attribution(tags: dict) -> dict:
    joined = join_chunks(tags)
    return {k: canonical_value(v) for k, v in joined.items()
            if tag_class(k) == "attribution"}


def value_str(cv) -> str:
    if isinstance(cv, frozenset):
        return ";".join(sorted(e for e, c in cv for _ in range(c)))
    return cv


# ---------------- geometry ----------------

def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------- load one clip into canonical POI features ----------------

class Collector(osmium.SimpleHandler):
    """Pass 1: everything POI-shaped + is_same relations + way node refs."""

    def __init__(self):
        super().__init__()
        self.nodes = {}      # id -> (tags, lon, lat)  — POI-class or identified nodes
        self.ways = {}       # id -> (tags, [refs])    — POI-class closed/area ways
        self.way_refs = {}   # id -> [refs] for ALL ways (relation-member geometry)
        self.rels = {}       # id -> (tags, [(type, ref)]) — POI-class relations
        self.is_same = {}    # rel id -> (label node id, outline type, outline id, tags)

    def node(self, n):
        t = dict((x.k, x.v) for x in n.tags)
        if not t:
            return
        if any(k in t for k in POI_KEYS) or any(k in t for k in ID_KEYS):
            self.nodes[n.id] = (t, n.location.lon, n.location.lat)

    def way(self, w):
        refs = [n.ref for n in w.nodes]
        self.way_refs[w.id] = refs
        t = dict((x.k, x.v) for x in w.tags)
        if any(k in t for k in POI_KEYS) and "highway" not in t:
            self.ways[w.id] = (t, refs)

    def relation(self, r):
        t = dict((x.k, x.v) for x in r.tags)
        if t.get("type") == "is_same":
            lab = next((m.ref for m in r.members if m.role == "label" and m.type == "n"), None)
            out = next(((m.type, m.ref) for m in r.members if m.role == "outline"), None)
            if lab is not None and out is not None:
                self.is_same[r.id] = (lab, out[0], out[1], t)
        elif any(k in t for k in POI_KEYS):
            self.rels[r.id] = (t, [(m.type, m.ref) for m in r.members])


class LocFill(osmium.SimpleHandler):
    """Pass 2: locations for outline-way geometry nodes."""

    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.loc = {}

    def node(self, n):
        if n.id in self.needed:
            self.loc[n.id] = (n.location.lon, n.location.lat)


class Feature:
    __slots__ = ("fid", "kind", "cls", "attribution", "lon", "lat",
                 "elements", "ids", "raw_tags")

    def __init__(self, fid, kind, cls, attribution, lon, lat, elements, ids, raw_tags):
        self.fid = fid                  # ('n'|'w'|'r'|'twin', primary element id)
        self.kind = kind                # 'node' | 'area' | 'twin'
        self.cls = cls                  # (class key, class value)
        self.attribution = attribution  # canonical attribution dict
        self.lon, self.lat = lon, lat   # canonical point
        self.elements = elements        # [(type, id)] raw elements this feature explains
        self.ids = ids                  # {identifier values} for match-group linking
        self.raw_tags = raw_tags


def poi_class_of(tags):
    for k in POI_KEYS:
        if k in tags:
            return (k, tags[k])
    return None


def identifier_values(tags):
    vals = set()
    joined = join_chunks(tags)
    for k in ID_KEYS:
        if k in joined:
            vals.add(joined[k])
    return vals


def load(clip):
    c = Collector()
    c.apply_file(clip)
    # geometry nodes needed for standalone POI areas and twin outlines
    needed = set()
    for wid, (t, refs) in c.ways.items():
        needed.update(refs)
    rel_geom_nodes = {}  # rel id -> [node refs] from members (transitive via ways)
    for rid, (t, members) in c.rels.items():
        refs = []
        for mt, mr in members:
            if mt == "n":
                refs.append(mr)
            elif mt == "w":
                refs.extend(c.way_refs.get(mr, []))
        rel_geom_nodes[rid] = refs
        needed.update(refs)
    lf = LocFill(needed)
    lf.apply_file(clip)

    feats = []
    label_of, outline_of = {}, {}
    for rid, (lab, ot, oi, rt) in c.is_same.items():
        label_of[lab] = rid
        outline_of[(ot[0] if ot in ("n", "w", "r") else ot, oi)] = rid
    outline_ids = {(ot, oi) for (lab, ot, oi, rt) in c.is_same.values()}

    def centroid(refs):
        pts = [lf.loc[r] for r in refs if r in lf.loc]
        if not pts:
            return None, None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    # twins: label node + outline (+ is_same rel) -> one canonical feature
    for rid, (lab, ot, oi, rt) in c.is_same.items():
        if lab not in c.nodes:
            continue
        lt, lon, lat = c.nodes[lab]
        cls = poi_class_of(lt)
        if cls is None or cls[0] not in POI_KEYS[:len(POI_KEYS)]:
            continue
        if cls is None:
            continue
        elements = [("n", lab), ("r", rid), (ot, oi)]
        ids = identifier_values(lt)
        out_tags = c.ways.get(oi, ({},))[0] if ot == "w" else c.rels.get(oi, ({}, []))[0]
        ids |= identifier_values(out_tags)
        feats.append(Feature(("twin", lab), "twin", cls, canonical_attribution(lt),
                             lon, lat, elements, ids, lt))

    twin_labels = {f.fid[1] for f in feats}

    # plain POI nodes (not twin labels)
    for nid, (t, lon, lat) in c.nodes.items():
        cls = poi_class_of(t)
        if cls is None or nid in twin_labels:
            continue
        feats.append(Feature(("n", nid), "node", cls, canonical_attribution(t),
                             lon, lat, [("n", nid)], identifier_values(t), t))

    # standalone POI areas (not twin outlines)
    for wid, (t, refs) in c.ways.items():
        if ("w", wid) in outline_ids:
            continue
        cls = poi_class_of(t)
        lon, lat = centroid(refs)
        feats.append(Feature(("w", wid), "area", cls, canonical_attribution(t),
                             lon, lat, [("w", wid)], identifier_values(t), t))

    # POI-class relations that are not is_same (rare; centroid from members)
    for rid, (t, members) in c.rels.items():
        if ("r", rid) in outline_ids:
            continue
        cls = poi_class_of(t)
        lon, lat = centroid(rel_geom_nodes.get(rid, []))
        feats.append(Feature(("r", rid), "area", cls, canonical_attribution(t),
                             lon, lat, [("r", rid)], identifier_values(t), t))

    return feats, c


print(f"loading baseline {CLIP_A} ...", flush=True)
feats_a, col_a = load(CLIP_A)
print(f"  {len(feats_a)} canonical POI features", flush=True)
print(f"loading target {CLIP_B} ...", flush=True)
feats_b, col_b = load(CLIP_B)
print(f"  {len(feats_b)} canonical POI features", flush=True)


# ---------------- identity-first matching into match groups ----------------

def by_id_value(feats):
    m = collections.defaultdict(list)
    for f in feats:
        for v in f.ids:
            m[v].append(f)
    return m

ids_a, ids_b = by_id_value(feats_a), by_id_value(feats_b)

# a link key is usable only where unambiguous on both sides
link_keys = {v for v in (set(ids_a) | set(ids_b))
             if len(ids_a.get(v, [])) <= 1 and len(ids_b.get(v, [])) <= 1}
ambiguous_keys = (set(ids_a) | set(ids_b)) - link_keys
amb_feats_a = {f.fid for v in ambiguous_keys for f in ids_a.get(v, [])}
amb_feats_b = {f.fid for v in ambiguous_keys for f in ids_b.get(v, [])}
print(f"identifier link keys: {len(link_keys)} usable, {len(ambiguous_keys)} ambiguous "
      f"(touching {len(amb_feats_a)}/{len(amb_feats_b)} features)", flush=True)

# union-find over (side, fid)
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
        parent[rx] = ry

fa = {f.fid: f for f in feats_a}
fb = {f.fid: f for f in feats_b}
for f in feats_a:
    find(("a", f.fid))
for f in feats_b:
    find(("b", f.fid))
matched_pairs = 0
for v in link_keys:
    la, lb = ids_a.get(v, []), ids_b.get(v, [])
    if la and lb:
        union(("a", la[0].fid), ("b", lb[0].fid))
        matched_pairs += 1
    # same-side linking (one feature carrying two ids is already one node)

groups = collections.defaultdict(lambda: ([], []))
for f in feats_a:
    groups[find(("a", f.fid))][0].append(f)
for f in feats_b:
    groups[find(("b", f.fid))][1].append(f)

# fallback: geometry+tag matching for identifier-less leftovers
unmatched_a = [g[0][0] for g in groups.values() if g[0] and not g[1] and len(g[0]) == 1]
unmatched_b = [g[1][0] for g in groups.values() if g[1] and not g[0] and len(g[1]) == 1]
fallback_pairs = []
used_b = set()
for a in unmatched_a:
    if a.lon is None:
        continue
    best, best_d = None, FALLBACK_MATCH_M
    for b in unmatched_b:
        if b.fid in used_b or b.lon is None or b.cls != a.cls:
            continue
        d = haversine_m(a.lon, a.lat, b.lon, b.lat)
        if d < best_d or (best is not None and abs(d - best_d) < 1e-9):
            # attribution as tiebreak between geometric near-ties
            if best is not None and abs(d - best_d) < 1e-9:
                sim_new = len(set(b.attribution.items()) & set(a.attribution.items()))
                sim_old = len(set(best.attribution.items()) & set(a.attribution.items()))
                if sim_new <= sim_old:
                    continue
            best, best_d = b, d
    if best is not None:
        used_b.add(best.fid)
        fallback_pairs.append((a, best, best_d))
        union(("a", a.fid), ("b", best.fid))
print(f"identity-matched link keys: {matched_pairs}; fallback geometric matches: {len(fallback_pairs)}", flush=True)

groups = collections.defaultdict(lambda: ([], []))
for f in feats_a:
    groups[find(("a", f.fid))][0].append(f)
for f in feats_b:
    groups[find(("b", f.fid))][1].append(f)


# ---------------- per-group canonical diff ----------------

def merge_side(fs):
    """A match-group side as one canonical view (usually a single feature)."""
    attribution = {}
    for f in fs:
        attribution.update(f.attribution)
    pts = [(f.lon, f.lat) for f in fs if f.lon is not None]
    lon = sum(p[0] for p in pts) / len(pts) if pts else None
    lat = sum(p[1] for p in pts) / len(pts) if pts else None
    return attribution, lon, lat


def diff_attribution(aa, ab):
    entries = []
    for k in sorted(set(aa) | set(ab)):
        va, vb = aa.get(k), ab.get(k)
        if va == vb:
            continue
        if isinstance(va, frozenset) or isinstance(vb, frozenset):
            sa = collections.Counter(dict(va)) if isinstance(va, frozenset) else \
                (collections.Counter({va: 1}) if va is not None else collections.Counter())
            sb = collections.Counter(dict(vb)) if isinstance(vb, frozenset) else \
                (collections.Counter({vb: 1}) if vb is not None else collections.Counter())
            removed = list((sa - sb).elements())
            added = list((sb - sa).elements())
            entries.append(("list", k, removed, added))
        else:
            entries.append(("scalar", k, va, vb))
    return entries


ledger = []            # dicts, one per match group with any raw churn or diff
verdict_counts = collections.Counter()
noise_reasons = collections.Counter()

raw_a = {(t, i) for f in feats_a for (t, i) in f.elements}
raw_b = {(t, i) for f in feats_b for (t, i) in f.elements}


def raw_element_changed(el):
    """Did this raw element change between the clips (create/delete/modify)?"""
    t, i = el
    if t == "n":
        na, nb = col_a.nodes.get(i), col_b.nodes.get(i)
    elif t == "w":
        na, nb = col_a.ways.get(i), col_b.ways.get(i)
    else:
        na = col_a.is_same.get(i) or col_a.rels.get(i)
        nb = col_b.is_same.get(i) or col_b.rels.get(i)
    return na != nb


for root, (ga, gb) in groups.items():
    aa, lon_a, lat_a = merge_side(ga)
    ab, lon_b, lat_b = merge_side(gb)
    elements = [("a",) + e for f in ga for e in f.elements] + \
               [("b",) + e for f in gb for e in f.elements]
    churned = [e for e in {(t, i) for (_, t, i) in elements} if raw_element_changed(e)]

    entry = {
        "a": ga, "b": gb, "root": root,
        "attr_diff": [], "move_m": None, "verdict": None,
        "noise_notes": [], "raw_churn": churned,
        "kind_flip": bool(ga and gb and {f.kind for f in ga} != {f.kind for f in gb}),
    }

    if ga and gb:
        entry["attr_diff"] = diff_attribution(aa, ab)
        if lon_a is not None and lon_b is not None:
            entry["move_m"] = haversine_m(lon_a, lat_a, lon_b, lat_b)
        move = entry["move_m"] or 0.0
        real_move = move > T_DRIFT_M
        if entry["attr_diff"] or real_move:
            entry["verdict"] = "real"
        elif churned:
            entry["verdict"] = "noise"
            # classify what the raw churn was, for the ledger's noise breakdown
            joined_a = {k: canonical_value(v) for k, v in join_chunks(
                {k: v for f in ga for k, v in f.raw_tags.items()}).items()}
            joined_b = {k: canonical_value(v) for k, v in join_chunks(
                {k: v for f in gb for k, v in f.raw_tags.items()}).items()}
            raw_keys_a = {k for f in ga for k in f.raw_tags}
            raw_keys_b = {k for f in gb for k in f.raw_tags}
            chunk_keys = {k for k in (raw_keys_a ^ raw_keys_b) if CHUNK_RE.match(k)}
            chunk_mod = {k for k in (raw_keys_a & raw_keys_b) if CHUNK_RE.match(k)
                         and any(f.raw_tags.get(k) != g2.raw_tags.get(k)
                                 for f in ga for g2 in gb)}
            if chunk_keys or chunk_mod:
                entry["noise_notes"].append("chunk re-flow")
            ids_churn = {i for (t, i) in churned if t in ("w", "r")}
            if ids_churn and not entry["kind_flip"]:
                entry["noise_notes"].append("twin/outline rewiring")
            meta_mod = [k for k in set(joined_a) | set(joined_b)
                        if tag_class(k) != "attribution" and joined_a.get(k) != joined_b.get(k)]
            if meta_mod:
                entry["noise_notes"].append("identity/meta-only tag change")
            if 0 < move <= T_ROUND_M:
                entry["noise_notes"].append("rounding move")
            elif T_ROUND_M < move <= T_DRIFT_M:
                entry["noise_notes"].append(f"drift move {move:.2f} m")
            id_a = {i for f in ga for (t, i) in f.elements if t == "n"}
            id_b = {i for f in gb for (t, i) in f.elements if t == "n"}
            if id_a and id_b and id_a != id_b:
                entry["noise_notes"].append("element id churn")
            # order-only list change detection
            order_only = []
            for k in set(joined_a) & set(joined_b):
                if joined_a[k] == joined_b[k]:
                    ra = {k2: v for f in ga for k2, v in join_chunks(f.raw_tags).items()}
                    rb = {k2: v for f in gb for k2, v in join_chunks(f.raw_tags).items()}
                    if ra.get(k) != rb.get(k):
                        order_only.append(k)
            if order_only:
                entry["noise_notes"].append("list order / value re-flow only")
            if not entry["noise_notes"]:
                entry["noise_notes"].append("untagged geometry-node or member churn")
        else:
            entry["verdict"] = "unchanged"
        if entry["kind_flip"] and entry["verdict"] == "noise":
            entry["noise_notes"].append("node<->area representation flip")
    elif ga:
        entry["verdict"] = "deleted"
    else:
        entry["verdict"] = "created"

    verdict_counts[entry["verdict"]] += 1
    for n in entry["noise_notes"]:
        noise_reasons[n] += 1
    if entry["verdict"] != "unchanged":
        ledger.append(entry)

print(f"verdicts: {dict(verdict_counts)}", flush=True)
print(f"noise reasons: {noise_reasons.most_common()}", flush=True)


# ---------------- raw churn accounting (the flood check) ----------------

def raw_diff_counts():
    """Raw element diff within the POI scope, for the summary header."""
    c = collections.Counter()
    all_n = set(col_a.nodes) | set(col_b.nodes)
    for i in all_n:
        a, b = col_a.nodes.get(i), col_b.nodes.get(i)
        pa = poi_class_of(a[0]) if a else None
        pb = poi_class_of(b[0]) if b else None
        if not (pa or pb):
            continue
        if a is None:
            c["node created"] += 1
        elif b is None:
            c["node deleted"] += 1
        elif a != b:
            c["node modified"] += 1
    for i in set(col_a.ways) | set(col_b.ways):
        a, b = col_a.ways.get(i), col_b.ways.get(i)
        if a is None:
            c["way created"] += 1
        elif b is None:
            c["way deleted"] += 1
        elif a != b:
            c["way modified"] += 1
    for i in set(col_a.rels) | set(col_b.rels):
        a, b = col_a.rels.get(i), col_b.rels.get(i)
        if a is None:
            c["relation created"] += 1
        elif b is None:
            c["relation deleted"] += 1
        elif a != b:
            c["relation modified"] += 1
    poi_twin_rels = {i for c in (col_a, col_b) for i, (lab, ot, oi, rt) in c.is_same.items()
                     if (lab in c.nodes and poi_class_of(c.nodes[lab][0]))}
    for i in poi_twin_rels:
        a, b = col_a.is_same.get(i), col_b.is_same.get(i)
        if a is None:
            c["is_same created"] += 1
        elif b is None:
            c["is_same deleted"] += 1
        elif a != b:
            c["is_same modified"] += 1
    return c

raw_counts = raw_diff_counts()
print(f"raw POI-scope churn: {dict(raw_counts)}", flush=True)


# ---------------- HTML mini-ledger ----------------

def esc(s):
    return html.escape(str(s))


def feat_label(f):
    name = f.raw_tags.get("name", "")
    return f"{f.cls[0]}={f.cls[1]}" + (f" “{name}”" if name else "")


def group_title(e):
    fs = e["a"] or e["b"]
    return feat_label(fs[0])


def elements_str(e):
    parts = []
    for side, fs in (("26330", e["a"]), ("26340", e["b"])):
        if fs:
            els = ", ".join(f"{t}{i}" for f in fs for (t, i) in f.elements)
            parts.append(f"{side}: {els}")
    return " → ".join(parts)


def render_diff(e):
    rows = []
    for d in e["attr_diff"]:
        if d[0] == "list":
            _, k, removed, added = d
            bits = []
            if removed:
                bits.append("<span class=del>− " + esc("; ".join(removed)) + "</span>")
            if added:
                bits.append("<span class=add>+ " + esc("; ".join(added)) + "</span>")
            rows.append(f"<tr><td class=k>{esc(k)}</td><td>{'<br>'.join(bits)} <span class=hint>(set diff of joined list)</span></td></tr>")
        else:
            _, k, va, vb = d
            va = value_str(va) if va is not None else "∅"
            vb = value_str(vb) if vb is not None else "∅"
            rows.append(f"<tr><td class=k>{esc(k)}</td><td><span class=del>{esc(va)}</span> → <span class=add>{esc(vb)}</span></td></tr>")
    return "<table class=diff>" + "".join(rows) + "</table>" if rows else ""


def entry_html(e, open_=False):
    v = e["verdict"]
    badge = {"real": "real", "noise": "noise", "created": "created", "deleted": "deleted"}[v]
    move = f" · moved {e['move_m']:.2f} m" if e.get("move_m") else ""
    star = " ⭐" if any(i == STATIONS_E_NODE for f in (e["a"] + e["b"]) for (t, i) in f.elements) else ""
    notes = " · ".join(e["noise_notes"])
    churn = len(e["raw_churn"])
    body = render_diff(e)
    if v == "created":
        f = e["b"][0]
        body = f"<div class=meta>new {f.kind} at ({f.lon:.5f}, {f.lat:.5f})</div>" if f.lon else ""
    if v == "deleted":
        f = e["a"][0]
        body = f"<div class=meta>was a {f.kind} at ({f.lon:.5f}, {f.lat:.5f})</div>" if f.lon else ""
    flip = " · node↔area flip" if e["kind_flip"] else ""
    return (f"<details{' open' if open_ else ''}><summary><span class='badge {badge}'>{badge}</span> "
            f"{esc(group_title(e))}{star}{move}{flip}"
            f"{(' · <i>' + esc(notes) + '</i>') if notes else ''}"
            f" <span class=hint>({churn} raw element changes)</span></summary>"
            f"<div class=meta>{esc(elements_str(e))}</div>{body}</details>")


real = [e for e in ledger if e["verdict"] == "real"]
noise = [e for e in ledger if e["verdict"] == "noise"]
created = [e for e in ledger if e["verdict"] == "created"]
deleted = [e for e in ledger if e["verdict"] == "deleted"]
real.sort(key=lambda e: -len(e["raw_churn"]))
noise.sort(key=lambda e: -len(e["raw_churn"]))

raw_total = sum(raw_counts.values())
canon_visible = len(real) + len(created) + len(deleted)

parts = [f"""<meta charset="utf-8">
<title>POI canonical mini-ledger</title>
<style>
 body {{ font: 14px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color:#222; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 .badge {{ display:inline-block; padding:0 .5em; border-radius:.6em; font-size:.85em; color:#fff; }}
 .badge.real {{ background:#c0392b; }} .badge.noise {{ background:#7f8c8d; }}
 .badge.created {{ background:#27ae60; }} .badge.deleted {{ background:#8e44ad; }}
 details {{ border-left: 3px solid #ddd; margin:.3rem 0; padding:.2rem .8rem; }}
 summary {{ cursor:pointer; }}
 .meta {{ color:#777; font-size:.85em; }}
 .hint {{ color:#999; font-size:.85em; }}
 table.diff {{ border-collapse: collapse; margin:.4rem 0; }}
 table.diff td {{ border:1px solid #eee; padding:.15rem .5rem; vertical-align:top; }}
 td.k {{ font-family:monospace; white-space:nowrap; }}
 .del {{ background:#fdecea; text-decoration:line-through; }} .add {{ background:#eafaf1; }}
 .kpi {{ display:inline-block; margin:.3rem 1.2rem .3rem 0; }}
 .kpi b {{ font-size:1.5rem; display:block; }}
</style>
<h1>POI canonical mini-ledger — Amersfoort 26330 → 26340 (issue #11)</h1>
<p>Canonical POI = point + attribution. Chunked <code>key#N#</code> tags joined;
';'-lists compared as sets; label/outline twins collapsed; matching identity-first
over all identifier tags (unambiguous values only), geometric fallback within
{FALLBACK_MATCH_M:.0f} m; moves ≤ {T_DRIFT_M:.0f} m are drift noise.</p>
<div>
 <span class=kpi><b>{raw_total}</b>raw element changes (POI scope)</span>
 <span class=kpi><b>{len(real)}</b>real attribute/geometry changes</span>
 <span class=kpi><b>{len(created)}</b>created</span>
 <span class=kpi><b>{len(deleted)}</b>deleted</span>
 <span class=kpi><b>{len(noise)}</b>noise-only match groups</span>
</div>
<p class=meta>raw churn: {esc(dict(raw_counts))}<br>
canonical features: {len(feats_a)} (26330) / {len(feats_b)} (26340); noise reasons: {esc(noise_reasons.most_common())}</p>
"""]

parts.append(f"<h2>Real changes ({len(real)})</h2>")
parts += [entry_html(e, open_=any(i == STATIONS_E_NODE for f in (e['a'] + e['b'])
                                  for (t, i) in f.elements)) for e in real]
parts.append(f"<h2>Created ({len(created)})</h2>")
parts += [entry_html(e) for e in created]
parts.append(f"<h2>Deleted ({len(deleted)})</h2>")
parts += [entry_html(e) for e in deleted]
parts.append(f"<h2>Noise-only groups ({len(noise)}) — raw churn with no canonical change</h2>")
parts += [entry_html(e) for e in noise]

import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
print(f"ledger written to {OUT}", flush=True)

# ---------------- stdout verdict summary ----------------
changed_keys = collections.Counter()
for e in real:
    for d in e["attr_diff"]:
        changed_keys[d[1]] += 1
print(f"\ntags driving 'real' verdicts: {changed_keys.most_common(30)}")
flips = [e for e in ledger if e["kind_flip"]]
print(f"node<->area flips in churned groups: {len(flips)} "
      f"(verdicts: {collections.Counter(e['verdict'] for e in flips)})")
fb_moved = [d for (_, _, d) in fallback_pairs if d > 0.01]
print(f"fallback matches with movement >1cm: {len(fb_moved)} of {len(fallback_pairs)}")

se = [e for e in ledger if any(i == STATIONS_E_NODE for f in (e["a"] + e["b"])
                               for (t, i) in f.elements)]
if se:
    e = se[0]
    print(f"\nstations-e case (node {STATIONS_E_NODE}): verdict={e['verdict']}")
    for d in e["attr_diff"]:
        if d[0] == "list":
            print(f"  {d[1]}: -{d[2]} +{d[3]}")
        else:
            print(f"  {d[1]}: {d[2]!r} -> {d[3]!r}")
