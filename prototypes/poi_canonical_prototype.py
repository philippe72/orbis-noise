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

DEFECTS from human review of the drift-only section (2026-08-26), all numbers
re-derived by prototypes/poi_review_probe.py. (1) and (2) are FIXED in this
script; (3) to (6) are recorded and still open.

(1) POSITION EXCHANGE AT EV CHARGING SITES. [FIXED] Drift cases 2/3 (9.48 m) and 4/5
(8.96 m) are not four moves. They are two unchanged charging sites where a
charging_location node and a charging_station_location node exchanged their
exact coordinates - verified exact at 1e-7 deg, with attribution identical on
all four nodes - while the station_location also changed which
charging_station relation holds it. Root cause: Collector.relation keeps a
relation only if it carries a POI class key, and 0 of 1397 charging_station
and 0 of 4144 charging_equipment relations carry one. All are therefore
invisible, and their member nodes fall through to 'plain POI node' as
top-level features. Per CONTEXT.md these are constitutive relations and their
members are feature parts.
 - Fix applied: Collector now keeps charging_station relations by type, and
   load() absorbs their charging_station_location members into the
   charging_location that owns them - the part is never a feature of its own,
   and the site's point is the centroid over its parts. An exchange of
   positions between two parts therefore leaves the centroid untouched and
   disappears without any distance rule. The charging_location keeps carrying
   the identity (its source_identifier is unique), so station_id was not
   needed as a key here; note that this makes the charging_location, not the
   charging_station, the top-level feature. That differs from the review's
   first framing and follows the data: one charging_location carries the
   name/address/operator and can own several charging_station relations.
 - Still open: station_id is present on 1397/1397 with 1397 distinct values
   and is a sound identity key if the station is ever promoted to a feature.
   evse_id is NOT: present on 4117/4144 charging_equipment with only 4114
   distinct values, so charging_equipment cannot simply be keyed on it, and
   whether it is a feature part or a child canonical feature is still open -
   charging_equipment members are NOT yet absorbed.
 - Still open: 7 of 8 'site' relations DO carry man_made=embankment, so the
   site loads as a feature AND its identifier-less member ways load again.
   Parent and parts both become features. Not addressed.
 - 'building' (427) is NOT this shape: all 1074 of its members are non-POI.
   Separate question, belongs to #12.

(2) CROSSED MATCH GROUPS AT UNRECOGNIZED TWINS [FIXED] - the larger defect,
and the only one with observed harm beyond (1). Each of 3 man_made=pier locations
produces a symmetric pair of false 'real' entries: n41831966118 ->
w4159545699 ('moved 0.55 m, node<->area flip') AND w4159545699 ->
n63229308378 ('moved 1.80 m'); likewise via w4159606647 and w4162998054.
Every one of those ways has the same element id in both map versions, yet
appears on the baseline side of one group and the target side of another.
Root cause: the pier label node and outline way share one osm_identifier
(229098047 / 229925811 / 161989043) with NO is_same relation. load() builds a
twin only from is_same, so both elements stay separate features carrying the
same identifier value; that value is then ambiguous on each side and is
dropped as a link key; source_identifier:internal changes between versions
(Aqua ID) so it cannot link either; the pair falls to the geometric fallback
and crosses.
 - This accounts for 6 of the 11 node<->area flip entries, so the claim below
   that the flips are arguably real does NOT hold for at least 6 of them.
   2 further pier flips are single-direction (same root cause, unverified);
   the 3 sport entries are a twin being formed in the target, not a flip.
 - CONTEXT.md's Label/outline twin required an is_same relation AND a shared
   identifier. The pier case shows a shared identifier alone is enough; the
   glossary has been corrected, and an ambiguous identifier value must be
   resolved by forming the twin, not by discarding the key.
 - Fix applied: fold_implicit_twins() joins a label node and an outline area
   that share an identifier value with no is_same relation. It folds 80 pairs
   per map version - far more than the 3 piers. Crossed match groups go from
   3 to 0, and the piers now read as noise ('identity/meta-only tag change,
   element id churn'), which is what they are.

(3) FALLBACK MATCHING IS GREEDY AND ORDER-DEPENDENT (see :374). Nearest
unused same-class candidate within 50 m, attribution only as a tiebreak, so
wherever candidates are indistinguishable the assignment is arbitrary and a
reshuffle reads as movement. Position exchange is the two-member case. No
distance tier can express these verdicts - do NOT raise T_DRIFT_M to absorb
them.
 - Exposure census (transitive closure, both map versions): 246
   identifier-less POI nodes sit in 83 same-class components within 50 m,
   most with identical attribution (street_cabinet 34 of 40 components,
   flagpole 5 of 5, all 11 fuel_pump in one component). BUT only 3 of the 83
   components contain any element change, and all 3 are
   charging_station_location. So this is latent exposure, not observed harm:
   once feature parts are excluded, this clip has no realized
   interchangeable-set failure. The observed harm comes from (2), by a
   different route - identifier ambiguity, not indistinguishable siblings.
 - 'No identifier' is the wrong hazard test: 195 street_cabinet, 115 mast,
   46 siren, 38 surveillance nodes are identifier-less, in no relation, and
   legitimately geometry-matched.

(4) Clip-wide there are 2 true position exchanges, both EV charging. A naive
multiset detector also flags a parking_space pair, but that is w7081422869
moving 3 cm on its own and it drops out at an 11 cm tolerance: a multiset test
cannot tell an exchange from two co-located independent moves. A correct test
must check that identity crosses - baseline position of X equals target
position of Y and vice versa.

(5) road_access: 13 relations have an identifier-less POI node as access_to;
10 are absent in the target, 3 unchanged, 0 with changed members. The
interaction is deletion, not access points following the swap. No noise
verdict is established for those 10.

(6) located_in (137 resolvable) is NOT a twin variant despite its
label/outline roles: 0 of 137 share a class key with their outline (the
outlines are buildings), against is_same at 16387/16388 same-class and 16356
sharing an identifier. It is place membership -> annotating relation, to be
re-anchored.

Result (26330 -> 26340, run 2026-08-26, AFTER the (1)+(2) fixes). 406 raw
element changes in POI scope -> 315 real attribution/geometry changes,
+21 created, -8 deleted, 18 noise-only groups, 19804 unchanged.

                            before fix   after fix
  canonical features        20242/20255  20145/20159
  real                              328          315
  created / deleted               +23/-11      +21/-8
  noise-only groups                  14           18
  drift-only cases                   10            5
  node<->area flip entries           11            3
  crossed match groups                3            0
  implicit twins folded               0    80 per side

Chunk re-flow still invisible (stations-e node 33246478484 surfaces as exactly
1 removed provider entry in each of 2 logical tags). The big real blocks are
still EV feed sweeps: payment:service_provider edits on 153 stations,
charging_when_closed removed on 86, vehicle_access:hgv:conditional added on 41.

What the fixes changed in the ledger:
 - The 4 EV charging drift entries (9.48 m x2, 8.96 m x2) are gone. Each site
   is now one match group, verdict noise, reason 'element id churn'.
 - The previous top drift case, 66.31 m amenity=charging_location 'JustPlugIn',
   is also gone: it was an unfolded twin, and now reads as noise. Every one of
   the 5 largest 'drift' moves in the old ledger was an artifact.
 - The 8 pier node<->area flips are gone; the 3 remaining flips are sport=*
   twins being formed in the target, which is a real change, not a flip.
 - Drift-only is now 5 cases: 5.46 m restaurant (real, same element id both
   sides) and 4 noise at 0.33-3.70 m. So exactly one case sits above the
   provisional 5 m threshold. The threshold is now under-evidenced rather than
   mis-evidenced - 4 points below and 1 above is too thin to tune on, and a
   second clip is needed before T_DRIFT_M means anything.
 - fallback geometric matches: 4294, of which only 4 move more than 1 cm.
   That supports (3)'s conclusion that interchangeable-set exposure is latent
   in this clip rather than realized.

Cosmetic issue left alone: the noise classifier still labels any churn of a
relation id 'twin/outline rewiring', so the charging sites are reported with
that reason even though the relation is a constitutive charging_station.

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
        self.chst = {}       # id -> (tags, [(type, ref, role)]) — charging_station,
                             # a constitutive relation carrying NO POI class key

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
        elif t.get("type") == "charging_station":
            self.chst[r.id] = (t, [(m.type, m.ref, m.role) for m in r.members])
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

    # constitutive charging_station relations: absorb their feature parts into the
    # charging_location that owns them, so a station_location is never a feature of
    # its own and the site's geometry is the centroid over its parts. A position
    # exchange between two parts then leaves the centroid untouched.
    part_pts = collections.defaultdict(list)   # charging_location nid -> [(lon, lat)]
    part_els = collections.defaultdict(list)   # charging_location nid -> [(type, id)]
    part_nodes = set()                         # nodes that are feature parts
    for rid, (rt, members) in c.chst.items():
        locs = [ref for mt, ref, role in members
                if mt == "n" and role == "charging_location"]
        slocs = [ref for mt, ref, role in members
                 if mt == "n" and role == "charging_station_location"]
        part_nodes.update(slocs)
        for lid in locs:
            for sid in slocs:
                if sid in c.nodes:
                    part_pts[lid].append((c.nodes[sid][1], c.nodes[sid][2]))
                    part_els[lid].append(("n", sid))
            part_els[lid].append(("r", rid))

    # plain POI nodes (not twin labels, not feature parts)
    for nid, (t, lon, lat) in c.nodes.items():
        cls = poi_class_of(t)
        if cls is None or nid in twin_labels or nid in part_nodes:
            continue
        pts = [(lon, lat)] + part_pts.get(nid, [])
        clon = sum(p[0] for p in pts) / len(pts)
        clat = sum(p[1] for p in pts) / len(pts)
        feats.append(Feature(("n", nid), "node", cls, canonical_attribution(t),
                             clon, clat, [("n", nid)] + part_els.get(nid, []),
                             identifier_values(t), t))

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

    feats = fold_implicit_twins(feats)
    return feats, c


def fold_implicit_twins(feats):
    """Join a label node and an outline area that share an identifier but have no
    is_same relation (man_made=pier in the Amersfoort clip). Left unjoined, both
    keep the same identifier value, which makes the value ambiguous, drops it as a
    match-group link key, and lets the pair cross in the geometric fallback."""
    by_val = collections.defaultdict(list)
    for f in feats:
        if f.kind == "twin":
            continue
        for v in f.ids:
            by_val[v].append(f)
    merged, absorbed = {}, set()
    for v, fs in by_val.items():
        if len(fs) != 2:
            continue
        labels = [f for f in fs if f.kind == "node"]
        areas = [f for f in fs if f.kind == "area"]
        if len(labels) != 1 or len(areas) != 1:
            continue
        lab, out = labels[0], areas[0]
        if lab.cls != out.cls or lab.fid in absorbed or out.fid in absorbed:
            continue
        if out.lon is None:
            continue
        absorbed.add(lab.fid)
        absorbed.add(out.fid)
        merged[lab.fid] = Feature(("twin", lab.fid[1]), "twin", lab.cls,
                                  lab.attribution, out.lon, out.lat,
                                  lab.elements + out.elements,
                                  lab.ids | out.ids, lab.raw_tags)
    if not absorbed:
        return feats
    print(f"  implicit twins folded (shared identifier, no is_same): {len(merged)}",
          flush=True)
    return [f for f in feats if f.fid not in absorbed] + list(merged.values())


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
        na = col_a.is_same.get(i) or col_a.rels.get(i) or col_a.chst.get(i)
        nb = col_b.is_same.get(i) or col_b.rels.get(i) or col_b.chst.get(i)
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


# ---------------- drift-only entries: position moved, attribution identical ----------------
# Rendered over faded OSM tiles so a human can eyeball real-vs-noise moves and
# pick a better drift threshold than the provisional T_DRIFT_M.

MAP_W, MAP_H = 420, 300


def _merc_px(lon, lat, z):
    n = 256 * (1 << z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def map_overlay(lon_a, lat_a, lon_b, lat_b, move_m):
    lat_mid = (lat_a + lat_b) / 2
    lon_mid = (lon_a + lon_b) / 2
    # zoom so the two markers sit ~120 px apart, clamped to OSM's max
    mpp_wanted = max(move_m, 0.5) / 120.0
    mpp_equator = 156543.03392 * math.cos(math.radians(lat_mid))
    z = min(19, max(14, int(math.log2(mpp_equator / mpp_wanted))))
    mpp = mpp_equator / (1 << z)
    cx, cy = _merc_px(lon_mid, lat_mid, z)
    left, top = cx - MAP_W / 2, cy - MAP_H / 2
    tiles = []
    for tx in range(int(left // 256), int((left + MAP_W) // 256) + 1):
        for ty in range(int(top // 256), int((top + MAP_H) // 256) + 1):
            tiles.append(
                f'<img src="https://tile.openstreetmap.org/{z}/{tx}/{ty}.png" '
                f'style="left:{tx * 256 - left:.0f}px;top:{ty * 256 - top:.0f}px" alt="">')
    ax, ay = _merc_px(lon_a, lat_a, z)
    bx, by = _merc_px(lon_b, lat_b, z)
    ax, ay, bx, by = ax - left, ay - top, bx - left, by - top
    # scale bar: a "nice" length that maps to 40-120 px
    bar_m = next((m for m in (0.5, 1, 2, 5, 10, 20, 50, 100) if 40 <= m / mpp <= 120),
                 5)
    bar_px = bar_m / mpp
    svg = (f'<svg width="{MAP_W}" height="{MAP_H}">'
           f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
           f'stroke="#333" stroke-width="2" stroke-dasharray="4 3"/>'
           f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="9" fill="none" stroke="#8e44ad" stroke-width="3.5"/>'
           f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="5" fill="#27ae60" stroke="#fff" stroke-width="2"/>'
           f'<rect x="10" y="{MAP_H - 26}" width="{bar_px:.0f}" height="4" fill="#333"/>'
           f'<text x="10" y="{MAP_H - 32}" font-size="11" fill="#333">{bar_m:g} m</text>'
           f'</svg>')
    return f'<div class=map style="width:{MAP_W}px;height:{MAP_H}px">{"".join(tiles)}{svg}</div>'


def drift_entry_html(e):
    aa, lon_a, lat_a = merge_side(e["a"])
    ab, lon_b, lat_b = merge_side(e["b"])
    v = e["verdict"]
    ids = elements_str(e)
    id_note = " · new element id" if {i for f in e["a"] for (t, i) in f.elements} != \
                                     {i for f in e["b"] for (t, i) in f.elements} else ""
    return (f"<div class=driftcard><div><span class='badge {v}'>{v}</span> "
            f"<b>{e['move_m']:.2f} m</b> — {esc(group_title(e))}{id_note}"
            f"<div class=meta>{esc(ids)}</div></div>"
            f"{map_overlay(lon_a, lat_a, lon_b, lat_b, e['move_m'])}</div>")


drifts = [e for e in ledger
          if e["a"] and e["b"] and not e["attr_diff"]
          and (e["move_m"] or 0) > T_ROUND_M]
drifts.sort(key=lambda e: -e["move_m"])

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
 .driftcard {{ display:flex; gap:1rem; align-items:flex-start; border:1px solid #eee;
               border-radius:6px; padding:.6rem .8rem; margin:.6rem 0; }}
 .driftcard > div:first-child {{ flex:1; min-width:14rem; }}
 .map {{ position:relative; overflow:hidden; border-radius:4px; flex:none; background:#f4f4f4; }}
 .map img {{ position:absolute; width:256px; height:256px;
             filter:saturate(.2) contrast(.75) brightness(1.3); }}
 .map svg {{ position:absolute; left:0; top:0; }}
 .legend span {{ display:inline-block; width:.8em; height:.8em; border-radius:50%;
                 margin:0 .3em 0 1em; vertical-align:-.05em; border:2px solid #fff;
                 box-shadow:0 0 1px #888; }}
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

parts.append(f"""<h2>Drift-only moves ({len(drifts)}) — position changed, attribution identical</h2>
<p class="meta legend">Every match group whose only canonical difference is the point position
(element id churn may ride along). Sorted by distance, so a threshold is a horizontal cut.
Current rule: ≤ {T_DRIFT_M:g} m = drift noise, above = real.
<span style="background:#fff;border-color:#8e44ad"></span>26330 position (ring)
<span style="background:#27ae60"></span>26340 position (dot)
· basemap © OpenStreetMap contributors, faded; needs internet to load tiles.</p>""")
parts += [drift_entry_html(e) for e in drifts]

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
