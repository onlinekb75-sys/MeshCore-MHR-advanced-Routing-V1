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
