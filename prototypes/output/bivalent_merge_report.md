# Bivalent merge + linear referencing — prototype report (issue #8)

Clip: `data/clips/amersfoort_26340.osm.pbf` — road ways = ways with `highway=*`.
Bivalency counted over road ways only; a node is a join iff exactly two road
ways end there and no road way passes through it. Merging fires on pure
topology; tag changes at the join are carried by linear references.

## 1. How often merging fires
- road ways: **87,876**
- bivalent join nodes: **34,199**
- merged roads: **53,677**  (reduction ×1.64)
- merged roads made of >1 way: **18,169** (33.8%)
- chain size distribution (ways per merged road): 1:35,508, 2:8,536, 3:6,425, 4:1,795, 5:706, 6:337, 7:141, 8:80, 9:56, 10:31, 11:16, 12:10, max 21

## 2. What sits at a topologically-bivalent node
- attribution diff (needs linear referencing): **29,983** (87.7%)
- identical tags: **1,943** (5.7%)
- only way-relative tag values differ (likely sectioning artifact): **1,894** (5.5%)
- identity/meta-only diff (pure sectioning signature): **379** (1.1%)

Way-relative tags (`gradient:linear`, `curvature:linear`) embed their own
along-the-way offsets, so sectioning rewrites their values without any
real-world change; the canonical model re-bases them into merged-road offset
space (with sign flip on reversal). `house_numbers:range:*` values are only
direction-normalized (from/to swap on reversal) and stay adjacent blocks:
each block's placement is real spatial information, never merged away.

Boundary flavors at joins (overlapping categories):
- bridge boundary: **1,535**
- highway class changes: **1,898**
- layer boundary: **1,697**
- name changes: **1,001**
- oneway value differs: **56**
- tunnel boundary: **355**

Join nodes carrying their own attribution tags: **4,523** — top keys: [('connector', 3258), ('barrier', 1409), ('supported:barrier', 1409), ('split', 1372), ('highway', 488), ('supported:highway', 488), ('crossing:markings', 465), ('supported:crossing:markings', 465)]

Top attribution keys that differ across a join:
- `gradient:linear`: 20,455
- `house_numbers:range:right`: 10,549
- `house_numbers:range:left`: 9,868
- `house_numbers:list:right`: 4,572
- `curvature:linear`: 4,142
- `house_numbers:list:left`: 4,035
- `postal_code:main`: 2,725
- `postal_code:main:right`: 2,653
- `postal_code:main:left`: 2,644
- `highway`: 1,898
- `layer`: 1,697
- `supported:layer`: 1,686
- `bridge`: 1,535
- `supported:bridge`: 1,518
- `zoomlevel_min`: 1,204

Example joins with attribution diffs (lat, lon → paste in a map):
- node 4829523424 (52.13529, 5.30334) ways 359861548/583427014: `access`: 'private' → None; `foot`: 'permissive' → None; `highway`: 'path' → 'footway'; `supported:foot`: 'no' → None
- node 6604395671 (52.16186, 5.34837) ways 360901151/7359189633: `gradient:linear`: '0#20;1869#20;11981#-8' → '0#-8;658#-10'; `house_numbers:range:left`: '15|37|numeric_mixed|' → None; `house_numbers:range:right`: None → '2|6|even|'; `name`: 'Larixstraat' → 'Enk'; `name:nl-Latn`: 'Larixstraat' → 'Enk'; `name:nl:pronunciation:en`: None → 'ˈɛŋk'
- node 4741651257 (52.13835, 5.39940) ways 360901161/7859055682: `gradient:linear`: '0#10;2592#10;3934#7' → '0#10;1244#10'; `house_numbers:list:left`: None → '49'; `parking:right`: 'street_side' → None; `supported:parking:right`: 'no' → None
- node 2773393789 (52.13524, 5.43763) ways 360901174/626941599: `bridge`: None → 'yes'; `layer`: None → '1'; `supported:bridge`: None → 'no'; `supported:layer`: None → 'no'
- node 2492227349 (52.13647, 5.44581) ways 360901177/7858112614: `derivedmaxspeed`: '10' → None; `derivedmaxspeed:bus`: '10' → None; `derivedmaxspeed:hgv`: '10' → None; `derivedmaxspeed:motorcar`: '10' → None; `derivedmaxspeed:motorcar:conditional`: '10 @ trailer' → None; `hazmat`: 'delivery' → None
- node 4706500842 (52.15129, 5.40032) ways 360901183/470436350: `crossing:island`: None → 'yes'; `footway`: None → 'crossing'; `supported:crossing:island`: None → 'no'; `supported:footway`: None → 'no'
- node 5550015484 (52.15514, 5.38598) ways 360901191/541976274: `supported:tunnel`: None → 'no'; `tunnel`: None → 'building_passage'
- node 4671353152 (52.14224, 5.41302) ways 360901196/7448382561: `access`: None → 'private'; `bicycle`: 'use_sidepath' → 'no'; `foot`: None → 'yes'; `gradient:linear`: '0#-3;2077#-3' → None; `highway`: 'unclassified' → 'service'; `maxspeed`: '30' → None

## 3. Direction handling
- constituent ways stored reversed relative to merged-road direction: **41,452**
- flips applied: `oneway` yes↔-1, `forward`↔`backward` and `left`↔`right` key-segment swaps, `|`-list reversal for `:lanes` keys
- unflippable tags on reversed ways (all way-relative value referencing): **26,112**
  - top keys: [('gradient:linear', 21064), ('curvature:linear', 4955), ('gradient:linear#1#', 41), ('gradient:linear#2#', 41), ('gradient:linear#3#', 6), ('gradient:linear#4#', 3), ('gradient:linear#5#', 2)]

## 4. Round-trip (merged road + linear refs → original way tags)
- exact reconstructions: **87,876**
- failures: **0**
- zero-length constituent ways (span carries no interval): **0**

## 5. Way-relative canonicalization (#15)
Semantics established empirically: `gradient:linear`/`curvature:linear` offsets
are **cm along the way** (first 0, last = way length; `a-b#null` = no data);
values are continuous across joins in travel direction and **negate on
reversal** (opposing joins: median |Δ| = 0 flipped vs 6 unflipped).
`house_numbers:range` is `from|to|scheme|` with from/to in way direction —
direction-normalized but deliberately kept as adjacent blocks (real spatial
placement), never merged into one range.

Seam verdicts (each seam = one bivalent join crossed by a run):
- `curvature:linear` — continuous: **5,495**
- `curvature:linear` — discontinuity: **8**
- `curvature:linear` — extent boundary: **1**
- `gradient:linear` — continuous: **21,536**
- `gradient:linear` — discontinuity: **3**
- `gradient:linear` — extent boundary: **806**
- `gradient:linear` — mixed pt/null seam: **1**
- discontinuity sizes: median 6, p90 123, max 127

Join-level rollup of the 'only way-relative tag values differ' category:
- reconcile in canonical form (pure sectioning artifact, confirmed): **1,894** (100.0%)
- do not reconcile (visible in canonical diff): **0**
- => **4,216/34,199** joins (12.3%) are now pure sectioning artifacts (identical + meta-only + reconciled way-relative)

Canonical round-trip (sliced run functions -> original per-way value strings):
- exact: **53,285**, failures: **0**
(house-number ranges ride the ordinary tag round-trip in section 4: the
from/to swap on reversal is a symmetric flip like oneway.)

## 6. Example merged roads

### 21 ways, 565 m, starts at (52.19487, 5.43275)
ways: [7657815869, 7658920650, 7636518692, 7633387639, 7603242772, 7613467519, 834888732, 1272319024, 1271114443, 875132036, 7621480234, 7610297944, 7622030417, 7650914365, 481615719, 7608663362, 7635102550, 7615926429, 7650973266, 7638744851, 7716993248]
- `covered`: [193–210m]='yes' | [233–242m]='yes' | [247–265m]='yes' | [277–281m]='yes'
- `curvature:linear`: [0–21m]='0#0;2092#0' | [21–145m]='0#1;12448#0' | [145–171m]='0#1;2623#1' | [171–193m]='0#1;2109#1' | [193–201m]='0#1;858#1' | [201–210m]='0#2;936#1'
- `cutting`: [145–193m]='yes' | [210–233m]='yes' | [242–247m]='yes' | [265–277m]='yes' | [281–371m]='yes'
- `foot`: [0–145m]='use_sidepath' | [145–565m]='no'
- `gradient:linear`: [0–21m]='0#18;2092#18' | [21–145m]='0#15;3076#18;12448#18' | [145–171m]='0#13;2623#15' | [171–193m]='0#11;2109#13' | [193–201m]='0#10;858#11' | [201–210m]='0#9;936#10'
- `layer`: [233–242m]='-1' | [247–265m]='-1' | [277–281m]='-1'
- `name`: [0–237m]='Laakboulevard' | [265–565m]='Boerderijenboulevard'
- `name:nl-Latn`: [0–237m]='Laakboulevard' | [265–565m]='Boerderijenboulevard'

### 18 ways, 1591 m, starts at (52.19329, 5.40376)
ways: [7068349449, 7066150475, 7632383949, 7614844983, 7609922245, 7646483692, 419026853, 7621126170, 7650421758, 7602336813, 7610554441, 1260515144, 799113363, 7606592927, 7650303765, 7609705901, 7601687253, 7064793560]
- `curvature:linear`: [0–294m]='0#0;29489#0' | [294–334m]='0#0;3974#0' | [334–434m]='0#0;10001#0' | [434–437m]='0#0;351#0' | [437–465m]='0#0;2721#0' | [465–618m]='0#0;15337#0'
- `divider:lanes`: [0–334m]='long_dash|long_dash|long_dash' | [334–662m]='long_dash|long_dash' | [662–1591m]='long_dash|long_dash|long_dash'
- `exit_entrance:lanes`: [0–334m]='no|no|no|yes' | [662–1591m]='no|no|yes|yes'
- `gradient:linear`: [0–294m]='0#0;3000#0;4000#1;12000#1;13000#2;14000#1;18000#1;19000#0;20000#1;25000#1;26000#0;27000#-1;29489#-1' | [294–334m]='0#-1;2511#-1;3511#0;3974#0' | [334–434m]='0#0;2537#0;3537#1;4537#1;5537#0;10001#0' | [434–437m]='0#0;351#0' | [437–465m]='0#0;2721#0' | [465–618m]='0#0;2464#0;3464#1;12464#1;13464#0;14464#0;15337#-1'
- `lanes`: [0–334m]='4' | [334–662m]='3' | [662–1591m]='4'
- `maxspeed:conditional`: [0–1162m]='130 @ (19:00-06:00)' | [1162–1591m]='120 @ (19:00-06:00)'
- `maxspeed:motorcar:conditional`: [0–1162m]='130 @ (19:00-06:00);80 @ trailer;90 @ (trailer AND trailer_weight<3.5)' | [1162–1591m]='120 @ (19:00-06:00);80 @ trailer;90 @ (trailer AND trailer_weight<3.5)'
- `maxspeed:variable`: [0–437m]='yes' | [1162–1591m]='yes'

### 18 ways, 1088 m, starts at (52.16296, 5.41969)
ways: [7066171986, 7436313333, 7608072342, 7611421724, 7623094255, 7616733236, 7607914169, 6788784553, 7615356483, 7651663058, 7820900385, 7610711815, 7615120610, 7618781612, 7634490879, 7603203122, 7644516450, 7815636375]
- `bridge`: [328–354m]='yes' | [848–894m]='yes'
- `curvature:linear`: [0–26m]='0#5;225#0;2616#0' | [26–27m]='0#0;100#0' | [27–49m]='0#0;2161#0' | [49–50m]='0#0;88#0' | [50–184m]='0#0;13414#0' | [184–201m]='0#0;1759#0'
- `divider:lanes`: [0–27m]='long_dash|double_solid' | [27–50m]='solid'
- `foot`: [0–328m]='no' | [354–848m]='no'
- `gradient:linear`: [0–26m]='0#19;1000#21;2000#20;2616#19' | [26–27m]='0#19;100#19' | [27–49m]='0#19;284#19;1284#19;2161#18' | [49–50m]='0#18;88#18' | [50–184m]='0#18;35#18;1035#17;2035#16;3035#16;4035#15;5035#14;6035#13;7035#11;8035#10;9035#9;10035#8;11035#7;12035#6;13035#5;13414#5' | [184–201m]='0#5;621#4;1621#3;1759#3'
- `lanes`: [0–27m]='3' | [27–50m]='2'
- `lanes:backward`: [0–27m]='2' | [27–50m]='1'
- `layer`: [328–354m]='1' | [848–894m]='1'

## 7. Visual examples
Before/after geometry + attribution: `prototypes/output/bivalent_merge_examples.html`
