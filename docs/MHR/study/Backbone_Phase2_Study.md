# Backbone Phase 2 — Proaktiver Regions-/Backbone-Control-Plane: Nutzen vs. Verlust vs. NETTO-Airtime

*Mechanismus A der Phase 2. Ehrliche, getrennte Airtime-Bilanz auf der ECHTEN Topologie.
Skript: `backbone_sim.py` · Daten: `backbone_results.json` · Plots: `fig_bb_*.png`.
Reproduzierbar (Seed 42, 6 Seeds, 120 Paare/Seed).*

> **Die Kernfrage:** Spart ein proaktiver Backbone (stabile Repeater tauschen periodisch
> Distance-Vector-Updates aus, sodass DATA als **Unicast** statt netzweitem **Flood** läuft)
> **NETTO** Airtime — **nach Abzug** der Kontroll-Updates, die er selbst kostet? Und wenn ja:
> in welchem Fenster aus Backbone-Größe, DV-Periode und Adoption?

---

## 1. Methodik (ehrlich, getrennt gemessen)

**Topologie:** echte `neighbor_graph.json` (1956 Kanten, ambiguous verworfen) → Riesenkomponente
**632 Knoten / 1577 Kanten**, Ø-Grad 4,99. Per-Link-Reliability aus echtem `avg_snr` (logistisch
um die Empfangsschwelle), ETX = 1/p². Identisch zu `mhr_sim_real_v4.py`.

**Zwei Airtime-Einheiten** — der entscheidende methodische Schritt:
1. **TX-Ereignisse** (sendende Knoten) — vergleichbar mit der Vorarbeit (v4/Studie).
2. **Physische Time-on-Air (ms)** per LoRa-ToA (Semtech-Formel, SF11/BW250k „long-fast").
   Nötig, weil DATA-Pakete (≈40 B → **494 ms** ToA) und kleine DV-Kontroll-Pakete **unterschiedlich
   groß** sind. **Nur in ms lassen sich Daten-Ersparnis und Kontroll-Kosten ehrlich gegeneinander
   aufrechnen** — TX-Zählung allein würde die Kontroll-Kosten systematisch unterschätzen oder
   überschätzen.

**Backbone-Modell:**
- Auswahl stabiler Repeater nach `relay_count_24h` (aktiver Transit), Tie-Break Grad (Hub/Border).
  Companions sind per Definition **nicht** backbone-fähig (leiten nicht weiter). 563 Kandidaten.
- DATA: Endpunkte „kleben" via lokalem ≤2-Hop-Flood an den nächsten aktiven Backbone-Knoten
  (Ingress/Egress); dazwischen **ETX-kürzester, proaktiv bekannter Unicast-Pfad** (0 Discovery-Airtime).
  Nicht über den Backbone erreichbare Paare → **Fallback auf heutiges Flood-and-cache**.
- **Kontroll-Kosten (DV):** jeder aktive Backbone-Knoten sendet je Periode **einen Zero-Hop-Broadcast**
  (kein Flood, 1 TX). Paketgröße = Header + #Einträge × 4 B. ToA je Update per LoRa-Formel.

**NETTO** (über 24 h, Kernzahl):
```
NETTO = (Baseline-Daten-ToA − Backbone-Daten-ToA) × #Discovery-Floods  −  Kontroll-ToA
        └──────────────── NUTZEN (gespart) ────────────────┘             └── VERLUST ──┘
```

**Baseline (reines Flood-and-cache):** Lieferquote **0,929**, **617 TX-Ereignisse/Zustellung**
(= **304 s** ToA bei 494 ms/Hop), Ø 5,0 Hops.

**Zwei DV-Skalierungs-Modelle** (beide gezeigt — ehrlich in beide Richtungen):
- **FLACHES DV** (Default aller Sweeps): jeder Knoten trägt **alle** Ziele seiner Backbone-Komponente
  → O(N). Pessimistische, aber faire Annahme für einen naiven Backbone.
- **REGIONS-HIERARCHIE (H1)**: intra-Region auf ~20 Ziele gedeckelt + aggregierte Inter-Region-Einträge
  → O(Regionsgröße). Die design-intendierte Variante.

---

## 2. Daten- vs. Kontroll- vs. NETTO-Airtime (Zahlen)

### 2a. Über Backbone-Größe (FLACHES DV, DV 300 s, Adoption 100 %, 2000 Floods/24 h)

| Backbone | Knoten | % über BB | Daten-Ersparnis | Kontroll-Kosten | **NETTO** | Liefer |
|---|---|---|---|---|---|---|
| 5 % | 28 | 4 % | +29 194 s | −2 447 s | **+26 748 s** | 0,950 |
| 10 % | 56 | 24 % | +151 067 s | −9 871 s | **+141 196 s** | 0,967 |
| 20 % | 113 | 53 % | +333 689 s | −59 872 s | **+273 817 s** ✅ best | 0,958 |
| 35 % | 197 | 60 % | +381 189 s | −230 755 s | **+150 434 s** | 0,954 |
| 50 % | 282 | 67 % | +428 611 s | −564 887 s | **−136 277 s** ❌ | 0,949 |
| 70 % | 394 | 80 % | +497 560 s | −1 371 112 s | **−873 552 s** ❌ | 0,976 |
| 100 % | 563 | 98 % | +593 297 s | −3 045 451 s | **−2 452 154 s** ❌ | 0,999 |

→ **Die Daten-Ersparnis wächst sublinear (sättigt), die Kontroll-Kosten wachsen super­linear**
(mehr Knoten × größere DV-Tabellen). Beim flachen DV kippt NETTO **oberhalb ~35–40 %** ins Negative.
Plot: `fig_bb_net_airtime.png`.

### 2b. Über DV-Periode (Backbone 35 %, flach)

| DV-Periode | Kontroll-Kosten | **NETTO** |
|---|---|---|
| 30 s | −2 307 551 s | **−1 926 362 s** ❌ |
| 60 s | −1 153 775 s | **−772 587 s** ❌ |
| 120 s | −576 888 s | **−195 699 s** ❌ |
| **300 s** | −230 755 s | **+150 434 s** ✅ |
| 600 s | −115 378 s | **+265 811 s** ✅ |
| 1800 s | −38 459 s | **+342 730 s** ✅ |
| 3600 s | −19 230 s | **+361 959 s** ✅ |

→ **Kipppunkt bei flachem 35 %-Backbone liegt zwischen 120 s und 300 s.** Schneller als alle ~5 min
zu aktualisieren frisst der Kontroll-Traffic den gesamten Nutzen. Plot: `fig_bb_dv_period.png`.

### 2c. Flaches DV vs. Regions-Hierarchie (H1) — der Rettungsanker bei großem Backbone

| Backbone | FLACH Einträge | FLACH NETTO | H1 Einträge | **H1 NETTO** |
|---|---|---|---|---|
| 35 % | 114 | +150 434 s | 21 | **+331 150 s** |
| 50 % | 204 | −136 277 s ❌ | 27 | **+345 527 s** ✅ |
| 70 % | 354 | −873 552 s ❌ | 36 | **+345 672 s** ✅ |
| 100 % | 552 | −2 452 154 s ❌ | 47 | **+329 255 s** ✅ |

→ **Genau der vom Design (H1) vorhergesagte Effekt:** ohne Hierarchie skaliert ein flacher Backbone
nicht (DV-Tabellen O(N), Pakete fragmentieren, Kontroll-Airtime explodiert). **Mit** Regions-Deckelung
bleibt NETTO auch bei 100 % Backbone klar positiv und nahezu größeninvariant (~+330 000 s/24 h).

---

## 3. GO / NO-GO

### ✅ **GO — aber bedingt, kein Freifahrtschein.**

Der proaktive Backbone lohnt **netto**, **wenn alle vier Bedingungen** erfüllt sind:

1. **Regions-Hierarchie (H1) ist Pflicht, nicht optional.** Ein flacher netzweiter DV ist nur in einem
   engen Fenster (kleiner Backbone ≤ 35 % **und** DV-Periode ≥ 300 s) netto positiv und kippt sonst
   stark negativ. Mit H1 ist NETTO über **alle** Backbone-Größen positiv (~+330 000 s/24 h).
2. **DV-Periode ≥ 300 s** (5 min). Darunter frisst der Kontroll-Traffic den Nutzen (Kipppunkt 120–300 s
   bei flachem 35 %-Backbone).
3. **Ausreichend DATA-Traffic.** Der Backbone ist eine **fixe** Kontroll-Investition; sie amortisiert
   erst ab **~2000 netzweiten Discovery-Floods/24 h** (bei 35 %/300 s flach — siehe §4). Bei niedrigem
   Traffic (≤ 1000/24 h) ist NETTO negativ: man bezahlt einen Backbone, den kaum jemand nutzt.
4. **Moderate Backbone-Größe.** Ohne H1: 10–35 %. Mit H1: nahezu beliebig, optimal um 35–50 %
   (maximale Abdeckung bei noch tragbarer Tabellengröße).

**Bestes Fenster** (gemessen):
- *Ohne H1:* **Backbone 20 %, DV ≥ 300 s** → NETTO **+273 817 s/24 h** (= 4,6 h Funkbelegung gespart),
  53 % der Paare über Backbone, Lieferquote 0,958 ≥ Baseline 0,929.
- *Mit H1:* **Backbone 35–50 %, DV 300–600 s** → NETTO **+330 000–345 000 s/24 h**, 60–67 % über Backbone.

### Wann **NO-GO**:
- DV-Periode < 120 s (Kontroll-Traffic dominiert immer).
- Flacher Backbone > 40 % (ohne H1 stark negativ).
- Geringes Daten-Aufkommen (< ~1000 Floods/24 h) — fixe Kontroll-Kosten amortisieren nicht.

---

## 4. Amortisation (Traffic-Sensitivität, Backbone 35 %/300 s flach)

| Floods/24 h | Daten-Ersparnis | Kontroll (fix) | **NETTO** |
|---|---|---|---|
| 100 | +19 059 s | −230 755 s | **−211 696 s** ❌ |
| 500 | +95 297 s | −230 755 s | **−135 458 s** ❌ |
| 1000 | +190 594 s | −230 755 s | **−40 161 s** ❌ |
| **2000** | +381 189 s | −230 755 s | **+150 434 s** ✅ break-even |
| 4000 | +762 377 s | −230 755 s | **+531 622 s** ✅ |
| 8000 | +1 524 755 s | −230 755 s | **+1 294 000 s** ✅ |

→ **Break-even ≈ 2000 Discovery-Floods/24 h.** Die Kontroll-Kosten sind fix (traffic-unabhängig),
die Ersparnis skaliert linear mit dem Daten-Aufkommen. **Mit H1 sinkt der Break-even drastisch**
(Kontroll-Kosten bei 35 % nur ~50 000 s statt 230 755 s → Break-even bei ~260 Floods/24 h).
Plot: `fig_bb_traffic.png`.

---

## 5. Delivery, Pfad-Optimalität, Konvergenz, Mixed-Firmware

- **Lieferquote (Safety):** In allen NETTO-positiven Szenarien **≥ Baseline (0,929)** — Bereich
  0,947–0,999. Der Backbone verschlechtert die Zustellung nicht (Fallback-Flood garantiert das,
  wo keine Backbone-Route existiert). **Safety-Invariante erfüllt.**
- **Pfad-Optimalität:** Unicast über den ETX-kürzesten proaktiven Pfad → deterministisch, kein
  Zufalls-Umweg. Detour gegen Shortest-Path wird mitgeführt; der Backbone wählt metrisch, nicht
  „first-wins".
- **Konvergenz:** im analytischen DV-Modell nicht zeitlich simuliert; das Design (H2: Babel-Feasibility
  + Seqno) garantiert Schleifenfreiheit *während* der Konvergenz. Kosten der Konvergenz stecken in den
  periodischen Updates (hier voll bilanziert). Eine echte zeit-/churn-aufgelöste Konvergenz-Simulation
  ist die nächste offene Validierung (s. Limitierungen).
- **Mixed-Firmware (Adoption, Backbone 35 %/300 s, flach):**

  | Adoption | aktive BB-Knoten | % über BB | Kontroll | **NETTO** | Liefer |
  |---|---|---|---|---|---|
  | 10 % | 20 | 11 % | −1 703 s | **+67 071 s** ✅ | 0,947 |
  | 25 % | 49 | 14 % | −5 589 s | **+84 594 s** ✅ | 0,968 |
  | 50 % | 98 | 32 % | −26 951 s | **+171 009 s** ✅ | 0,961 |
  | 75 % | 148 | 54 % | −109 053 s | **+223 836 s** ✅ | 0,972 |
  | 100 % | 197 | 60 % | −230 755 s | **+150 434 s** ✅ | 0,954 |

  → **Graceful & monoton positiv ab dem ersten Knoten.** Schon bei 10 % Adoption NETTO positiv;
  Nicht-Adoptierer fluten weiter (sicherer Fallback), Lieferquote bleibt ≥ Baseline. Der Gewinn steigt
  mit Adoption, bis (bei flachem DV) die wachsenden Kontroll-Kosten ihn ab ~100 % wieder drücken — mit
  H1 träte dieser Rückgang nicht ein. Plot: `fig_bb_adoption.png`.

---

## 6. Bugs / Fehler während der Entwicklung

- **Einrückungs-Syntaxfehler** (führendes Leerzeichen vor `in_bb = attach_repeater(...)`) — beim ersten
  Schreiben eingebaut, sofort gefixt; Skript läuft seitdem fehlerfrei in einem Durchlauf.
- Keine Laufzeitfehler. `BB_FAST=1`-Smoke-Test und voller Lauf (6 Seeds) sauber.

---

## 7. Dateien

- `docs/MHR/study/backbone_sim.py` — Simulation (selbst ausgeführt, fehlerfrei).
- `docs/MHR/study/backbone_results.json` — alle Roh-Sweeps + Kennzahlen-Summary.
- `docs/MHR/study/fig_bb_net_airtime.png` — Daten/Kontroll/NETTO über Backbone-Größe (Kernplot).
- `docs/MHR/study/fig_bb_adoption.png` — Mixed-Firmware: NETTO & Abdeckung über Adoption.
- `docs/MHR/study/fig_bb_dv_period.png` — Kipppunkt der DV-Periode.
- `docs/MHR/study/fig_bb_traffic.png` — Amortisation über Daten-Aufkommen.
- `docs/MHR/study/fig_bb_grid.png` — 2D-NETTO-Fenster (Größe × Periode).

---

## 8. Limitierungen (ehrlich)

1. **Idealisierter Flood ohne Kollisionen.** Das Flood-Modell zählt sendende Knoten, modelliert aber
   **keine** CSMA-Kollisionen/Capture. Realer Flood ist durch Kollisionen *teurer* → die Daten-Ersparnis
   ist hier eher **konservativ** (untere Schranke des Nutzens).
2. **DV-Kosten als analytisches Modell.** Periode × ToA × #aktive Knoten, mit DV-Tabellengröße aus der
   Komponenten-/Regionsgröße. Keine simulierte Retransmits/Verluste der DV-Pakete (die Kontroll-Kosten
   wären real eher *höher* → NETTO-Schätzung hier eher **optimistisch** auf der Kostenseite). Diese
   beiden Effekte (1 konservativ, 2 optimistisch) wirken gegenläufig.
3. **neighbor-graph = nur GENUTZTE Links.** Die echte Topologie enthält nur beobachtete Kanten; latente
   Links fehlen. Backbone-Konnektivität und Attach-Hops könnten real günstiger sein.
4. **Traffic-Annahme (#Floods/24 h) ist der dominierende freie Parameter.** NETTO hängt linear davon ab;
   der reale netzweite Discovery-Flood-Rate ist nicht direkt aus den Daten messbar (nur relay_count als
   Proxy). Darum als expliziter Sweep geführt, nicht als feste Zahl behauptet.
5. **Konvergenzzeit / Churn-Flattern nicht zeit-aufgelöst.** Schleifenfreiheit & Stabilität sind
   Design-Argumente (H2/H3), hier nicht dynamisch simuliert. Offene nächste Validierung.
6. **Regions-Zuordnung approximiert** (region_cap als Deckel, nicht aus echten `region_map`-Grenzen) —
   die H1-Zahlen sind die Größenordnung der Skalierung, nicht eine exakte Deployment-Vorhersage.

---

## 9. Einordnung gegenüber der unsichtbaren Schicht

Die unsichtbare, node-lokale Schicht (`Invisible_Optimizing_Layer.md`) spart Airtime zu **0 Kontroll-Kosten**
und ist immer sicher. Der Backbone ist **mächtiger** (deterministische Unicast-Pfade, höhere Abdeckung),
aber **nicht gratis**: er ist NETTO nur in einem definierten Fenster (H1 + DV ≥ 300 s + genug Traffic)
ein Gewinn. **Empfehlung:** Backbone als optionale Phase-2-Kür **mit Pflicht-H1**, defaultmäßig konservative
DV-Periode (≥ 300 s), und nur dort aktivieren, wo das Daten-Aufkommen die fixe Kontroll-Investition trägt —
die unsichtbare Schicht bleibt das risikolose Fundament darunter.

---
## 🇬🇧 English Translation

# Backbone Phase 2 — Proactive Regional/Backbone Control Plane: Benefit vs. Cost vs. NET Airtime

*Mechanism A of Phase 2. Honest, separately measured airtime accounting on the REAL topology.
Script: `backbone_sim.py` · Data: `backbone_results.json` · Plots: `fig_bb_*.png`.
Reproducible (Seed 42, 6 seeds, 120 pairs/seed).*

> **The core question:** Does a proactive backbone (stable repeaters periodically exchange
> Distance-Vector updates, so that DATA runs as **unicast** instead of network-wide **flood**)
> save **NET** airtime — **after subtracting** the control updates it costs itself? And if so:
> in which window of backbone size, DV period, and adoption?

---

## 1. Methodology (honest, measured separately)

**Topology:** real `neighbor_graph.json` (1956 edges, ambiguous ones discarded) → giant component
**632 nodes / 1577 edges**, avg. degree 4.99. Per-link reliability from real `avg_snr` (logistic
around the reception threshold), ETX = 1/p². Identical to `mhr_sim_real_v4.py`.

**Two airtime units** — the decisive methodological step:
1. **TX events** (transmitting nodes) — comparable with prior work (v4/study).
2. **Physical time-on-air (ms)** per LoRa ToA (Semtech formula, SF11/BW250k "long-fast").
   Required because DATA packets (≈40 B → **494 ms** ToA) and small DV control packets are
   **differently sized**. **Only in ms can data savings and control costs be honestly compared** —
   TX counting alone would systematically underestimate or overestimate control costs.

**Backbone model:**
- Selection of stable repeaters by `relay_count_24h` (active transit), tie-break by degree (hub/border).
  Companions are by definition **not** backbone-capable (do not forward). 563 candidates.
- DATA: endpoints "attach" via local ≤2-hop flood to the nearest active backbone node
  (ingress/egress); between them **ETX-shortest, proactively known unicast path** (0 discovery airtime).
  Pairs not reachable via backbone → **fallback to current flood-and-cache**.
- **Control costs (DV):** each active backbone node sends **one zero-hop broadcast** per period
  (no flood, 1 TX). Packet size = header + #entries × 4 B. ToA per update via LoRa formula.

**NET** (over 24 h, key figure):
```
NET = (Baseline-Data-ToA − Backbone-Data-ToA) × #Discovery-Floods  −  Control-ToA
      └────────────────── BENEFIT (saved) ──────────────────┘        └── COST ──┘
```

**Baseline (pure flood-and-cache):** delivery rate **0.929**, **617 TX events/delivery**
(= **304 s** ToA at 494 ms/hop), avg. 5.0 hops.

**Two DV scaling models** (both shown — honest in both directions):
- **FLAT DV** (default of all sweeps): each node carries **all** destinations of its backbone component
  → O(N). Pessimistic but fair assumption for a naive backbone.
- **REGION HIERARCHY (H1)**: intra-region capped at ~20 destinations + aggregated inter-region entries
  → O(region size). The design-intended variant.

---

## 2. Data vs. Control vs. NET Airtime (Numbers)

### 2a. Over Backbone Size (FLAT DV, DV 300 s, adoption 100 %, 2000 floods/24 h)

| Backbone | Nodes | % via BB | Data savings | Control costs | **NET** | Delivery |
|---|---|---|---|---|---|---|
| 5 % | 28 | 4 % | +29,194 s | −2,447 s | **+26,748 s** | 0.950 |
| 10 % | 56 | 24 % | +151,067 s | −9,871 s | **+141,196 s** | 0.967 |
| 20 % | 113 | 53 % | +333,689 s | −59,872 s | **+273,817 s** ✅ best | 0.958 |
| 35 % | 197 | 60 % | +381,189 s | −230,755 s | **+150,434 s** | 0.954 |
| 50 % | 282 | 67 % | +428,611 s | −564,887 s | **−136,277 s** ❌ | 0.949 |
| 70 % | 394 | 80 % | +497,560 s | −1,371,112 s | **−873,552 s** ❌ | 0.976 |
| 100 % | 563 | 98 % | +593,297 s | −3,045,451 s | **−2,452,154 s** ❌ | 0.999 |

→ **Data savings grow sublinearly (saturating), control costs grow superlinearly**
(more nodes × larger DV tables). With flat DV, NET tips negative **above ~35–40 %**.
Plot: `fig_bb_net_airtime.png`.

### 2b. Over DV Period (backbone 35 %, flat)

| DV period | Control costs | **NET** |
|---|---|---|
| 30 s | −2,307,551 s | **−1,926,362 s** ❌ |
| 60 s | −1,153,775 s | **−772,587 s** ❌ |
| 120 s | −576,888 s | **−195,699 s** ❌ |
| **300 s** | −230,755 s | **+150,434 s** ✅ |
| 600 s | −115,378 s | **+265,811 s** ✅ |
| 1800 s | −38,459 s | **+342,730 s** ✅ |
| 3600 s | −19,230 s | **+361,959 s** ✅ |

→ **Tipping point for flat 35 % backbone lies between 120 s and 300 s.** Updating faster than every
~5 min consumes the entire benefit via control traffic. Plot: `fig_bb_dv_period.png`.

### 2c. Flat DV vs. Region Hierarchy (H1) — the rescue anchor for large backbones

| Backbone | FLAT entries | FLAT NET | H1 entries | **H1 NET** |
|---|---|---|---|---|
| 35 % | 114 | +150,434 s | 21 | **+331,150 s** |
| 50 % | 204 | −136,277 s ❌ | 27 | **+345,527 s** ✅ |
| 70 % | 354 | −873,552 s ❌ | 36 | **+345,672 s** ✅ |
| 100 % | 552 | −2,452,154 s ❌ | 47 | **+329,255 s** ✅ |

→ **Exactly the effect predicted by design (H1):** without hierarchy, a flat backbone does not scale
(DV tables O(N), packets fragment, control airtime explodes). **With** regional capping, NET remains
clearly positive even at 100 % backbone and is nearly size-invariant (~+330,000 s/24 h).

---

## 3. GO / NO-GO

### ✅ **GO — but conditional, not a blank check.**

The proactive backbone is worthwhile **net**, **when all four conditions** are met:

1. **Region hierarchy (H1) is mandatory, not optional.** A flat network-wide DV is net-positive only in
   a narrow window (small backbone ≤ 35 % **and** DV period ≥ 300 s) and otherwise tips sharply negative.
   With H1, NET is positive across **all** backbone sizes (~+330,000 s/24 h).
2. **DV period ≥ 300 s** (5 min). Below that, control traffic consumes the benefit (tipping point 120–300 s
   at flat 35 % backbone).
3. **Sufficient DATA traffic.** The backbone is a **fixed** control investment; it does not amortize until
   **~2000 network-wide discovery floods/24 h** (at 35 %/300 s flat — see §4). At low traffic
   (≤ 1000/24 h), NET is negative: you are paying for a backbone that barely anyone uses.
4. **Moderate backbone size.** Without H1: 10–35 %. With H1: almost arbitrary, optimal around 35–50 %
   (maximum coverage at still manageable table size).

**Best window** (measured):
- *Without H1:* **Backbone 20 %, DV ≥ 300 s** → NET **+273,817 s/24 h** (= 4.6 h radio occupancy saved),
  53 % of pairs via backbone, delivery rate 0.958 ≥ baseline 0.929.
- *With H1:* **Backbone 35–50 %, DV 300–600 s** → NET **+330,000–345,000 s/24 h**, 60–67 % via backbone.

### When **NO-GO**:
- DV period < 120 s (control traffic always dominates).
- Flat backbone > 40 % (without H1 strongly negative).
- Low data volume (< ~1000 floods/24 h) — fixed control costs do not amortize.

---

## 4. Amortization (Traffic Sensitivity, Backbone 35 %/300 s flat)

| Floods/24 h | Data savings | Control (fixed) | **NET** |
|---|---|---|---|
| 100 | +19,059 s | −230,755 s | **−211,696 s** ❌ |
| 500 | +95,297 s | −230,755 s | **−135,458 s** ❌ |
| 1000 | +190,594 s | −230,755 s | **−40,161 s** ❌ |
| **2000** | +381,189 s | −230,755 s | **+150,434 s** ✅ break-even |
| 4000 | +762,377 s | −230,755 s | **+531,622 s** ✅ |
| 8000 | +1,524,755 s | −230,755 s | **+1,294,000 s** ✅ |

→ **Break-even ≈ 2000 discovery floods/24 h.** Control costs are fixed (traffic-independent),
savings scale linearly with data volume. **With H1, break-even drops drastically**
(control costs at 35 % only ~50,000 s instead of 230,755 s → break-even at ~260 floods/24 h).
Plot: `fig_bb_traffic.png`.

---

## 5. Delivery, Path Optimality, Convergence, Mixed Firmware

- **Delivery rate (safety):** In all NET-positive scenarios **≥ baseline (0.929)** — range
  0.947–0.999. The backbone does not worsen delivery (fallback flood guarantees that where no
  backbone route exists). **Safety invariant satisfied.**
- **Path optimality:** Unicast over the ETX-shortest proactive path → deterministic, no random
  detour. Detour vs. shortest path is tracked; the backbone chooses by metric, not "first-wins".
- **Convergence:** not time-simulated in the analytical DV model; the design (H2: Babel-Feasibility
  + Seqno) guarantees loop-freedom *during* convergence. Convergence costs are embedded in the
  periodic updates (fully accounted here). A real time-/churn-resolved convergence simulation
  is the next open validation (see Limitations).
- **Mixed firmware (adoption, backbone 35 %/300 s, flat):**

  | Adoption | Active BB nodes | % via BB | Control | **NET** | Delivery |
  |---|---|---|---|---|---|
  | 10 % | 20 | 11 % | −1,703 s | **+67,071 s** ✅ | 0.947 |
  | 25 % | 49 | 14 % | −5,589 s | **+84,594 s** ✅ | 0.968 |
  | 50 % | 98 | 32 % | −26,951 s | **+171,009 s** ✅ | 0.961 |
  | 75 % | 148 | 54 % | −109,053 s | **+223,836 s** ✅ | 0.972 |
  | 100 % | 197 | 60 % | −230,755 s | **+150,434 s** ✅ | 0.954 |

  → **Graceful & monotonically positive from the first node.** Already NET-positive at 10 % adoption;
  non-adopters continue to flood (safe fallback), delivery rate stays ≥ baseline. The gain increases
  with adoption until (with flat DV) growing control costs push it back down from ~100 % — with
  H1 this decline would not occur. Plot: `fig_bb_adoption.png`.

---

## 6. Bugs / Errors During Development

- **Indentation syntax error** (leading space before `in_bb = attach_repeater(...)`) — introduced on
  first write, immediately fixed; script has run error-free in a single pass since then.
- No runtime errors. `BB_FAST=1` smoke test and full run (6 seeds) clean.

---

## 7. Files

- `docs/MHR/study/backbone_sim.py` — simulation (self-executed, error-free).
- `docs/MHR/study/backbone_results.json` — all raw sweeps + key metrics summary.
- `docs/MHR/study/fig_bb_net_airtime.png` — data/control/NET over backbone size (core plot).
- `docs/MHR/study/fig_bb_adoption.png` — mixed firmware: NET & coverage over adoption.
- `docs/MHR/study/fig_bb_dv_period.png` — tipping point of DV period.
- `docs/MHR/study/fig_bb_traffic.png` — amortization over data volume.
- `docs/MHR/study/fig_bb_grid.png` — 2D NET window (size × period).

---

## 8. Limitations (honest)

1. **Idealized flood without collisions.** The flood model counts transmitting nodes but models
   **no** CSMA collisions/capture. Real flooding is *more expensive* due to collisions → the data
   savings here are rather **conservative** (lower bound of benefit).
2. **DV costs as an analytical model.** Period × ToA × #active nodes, with DV table size from
   component/region size. No simulated retransmits/losses of DV packets (real control costs would
   be *higher* → NET estimate here rather **optimistic** on the cost side). These two effects
   (1 conservative, 2 optimistic) act in opposite directions.
3. **neighbor-graph = only USED links.** The real topology contains only observed edges; latent
   links are missing. Backbone connectivity and attach hops could be more favorable in reality.
4. **Traffic assumption (#floods/24 h) is the dominant free parameter.** NET depends linearly on it;
   the real network-wide discovery flood rate is not directly measurable from the data (only
   relay_count as proxy). Therefore presented as an explicit sweep, not asserted as a fixed number.
5. **Convergence time / churn flutter not time-resolved.** Loop-freedom & stability are design
   arguments (H2/H3), not dynamically simulated here. Next open validation.
6. **Region assignment approximated** (region_cap as a cap, not from real `region_map` boundaries) —
   the H1 numbers represent the order of magnitude of scaling, not an exact deployment prediction.

---

## 9. Classification vs. the Invisible Layer

The invisible, node-local layer (`Invisible_Optimizing_Layer.md`) saves airtime at **0 control costs**
and is always safe. The backbone is **more powerful** (deterministic unicast paths, higher coverage),
but **not free**: it is NET-positive only in a defined window (H1 + DV ≥ 300 s + sufficient traffic).
**Recommendation:** backbone as an optional Phase-2 enhancement **with mandatory H1**, conservative
DV period by default (≥ 300 s), and only activated where data volume justifies the fixed control
investment — the invisible layer remains the risk-free foundation beneath it.
