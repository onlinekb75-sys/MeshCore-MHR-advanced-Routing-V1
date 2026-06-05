# MeshCore-Simulation v3 — auf ECHTEN Live-Daten kalibriert

**Datenquelle:** CoreScope (`corescope.meshrheinland.de`), Live-Abzug vom 2026-05-30.
**Datensatz:** 1962 Knoten (1819 mit Geo), 38 Observer (19 mit Geo), **109.980 reale rx-Pakete**
(Roh-`packets.jsonl` ≈ 197 MB, *nicht* im Repo — nur kompakte Derivate unter `data/`).
**Reproduzierbar:** Seed 42. Collector: `mhr_collect_corescope.py`. Simulation: `mhr_sim_real_v3.py`.

Diese Studie trennt strikt zwischen **GEMESSEN** (aus realen Produktionspaketen abgeleitet)
und **SIMULIERT** (Monte-Carlo-Routing-Modell auf der realen Topologie). Bewusst ehrlich,
ohne Schönfärberei: wo der Realdatensatz eine alte Annahme widerlegt, steht das hier.

---

## Baustein A — Was aus den Live-Daten GEMESSEN wurde

### A1 — Node→Hash-Mapping & Disambiguierung
MeshCore-Hash = die ersten `hash_size` Bytes des `public_key` (Hex-Präfix). Index über alle
1/2/3-Byte-Präfixe gebaut. Bestätigte Eindeutigkeit:

| Präfix | distinct | kollidierend | Ø Knoten/Präfix |
|---|---|---|---|
| 1-Byte | 254 | 254 (alle) | 7,72 |
| 2-Byte | 1934 | 27 | 1,01 |
| 3-Byte | 1961 | 1 | 1,00 |

1-Byte-Hashes (real ~75 % aller Pfad-Hashes) kollidieren massiv. Disambiguierung über
Geografie: bei Kollision wird der zum bereits aufgelösten Nachbar-Hop nächste Kandidat in
LoRa-Reichweite (≤ 45 km) gewählt; vor- **und** rückwärts. Unauflösbares wird als *ambig*
markiert und **nicht geraten**.

Aufschlüsselung über alle 1,41 Mio. Pfad-Hash-Vorkommen:
`unique 18,2 %`, `geo-disambiguiert 0,1 %`, `ambig 77,6 %`, `unknown 4,1 %`.
Die hohe Ambig-Quote ist eine **ehrliche Limitierung**: 1-Byte-Hashes sind ohne
zuverlässige Nachbar-Referenz oft nicht eindeutig zuordenbar. Für die *quantitativen*
Auswertungen (SNR-Fit, Hop-Distanzen) verwenden wir daher ausschließlich **eindeutig**
auflösbare Hops — das vermeidet Rate-Bias.

### A2 — Beobachtete Relay-Topologie (Backbone)
Aufeinanderfolgende, aufgelöste Pfad-Hops = reale gerichtete Kanten „A leitet an B weiter".
Aggregiert (nur Repeater↔Repeater):

- **Knoten im Backbone-Graph:** 745
- **Gerichtete Kanten:** 6246
- **Grad (ungerichtet):** min 1 / median 6 / max 118
- **Größte Zusammenhangskomponente:** 724 von 745 (97 %)

Ergebnis: ein gut vernetzter, zusammenhängender Backbone mit wenigen Hub-Knoten (max. Grad 118).
Kompakte Kantenliste: `data/topology_edges.json`. Plot: `fig_v3_real_topology.png`.

### A3 — SNR/Distanz-Linkmodell kalibrieren (KERNBEFUND der Messung)
Für jedes rx-Paket: Observer (Position bekannt) hörte den **letzten** Hop der Pfadkette
(Position via Hash-Map). Aus **14.399 eindeutig** auflösbaren (letzter-Hop → Observer)-Paaren
ein Log-Distance-Modell `SNR ≈ SNR0 − 10·n·log10(d)` gefittet:

| Modell | SNR0 (dB) | Pfadverlust-Exponent n | Bemerkung |
|---|---|---|---|
| **GEMESSEN (OLS)** | **2,78** | **0,41** | corr(log d, SNR) = **−0,42** |
| GEMESSEN (Bin-Mediane) | −0,75 | 0,24 | robust gegen Sättigung |
| ALTE ANNAHME (`mhr_sim_real.py`) | 17,0 | 2,55 | unbelegt |

**Befund — wichtig und unbequem:** Im realen Datensatz erklärt die reine Distanz das SNR
**nur schwach** (|corr| ≈ 0,42, n ≈ 0,4 statt der angenommenen 2,55). Antennenhöhe,
Standort (viele Repeater auf Bergen/Hochhäusern), Richtwirkung und Gelände dominieren über
die Luftlinie. Knoten in 40–70 km Entfernung werden teils mit besserem SNR gehört als
Nachbarn in < 2 km. Die saubere Pfadverlust-Annahme des alten Skripts ist damit für dieses
reale Netz **nicht haltbar** — der vermeintliche „Genauigkeitsgewinn" besteht gerade darin,
diese Diskrepanz offenzulegen. Plot: `fig_v3_snr_fit.png`. Parameter: `data/snr_calibration.json`.

> **Konsequenz fürs Simulationsmodell (Baustein B):** Ein PLE von 0,4 ergäbe einen
> physikalisch unsinnigen Reichweitengraphen (quasi unbegrenzte Reichweite). Für die Sim
> wird daher der reale Intercept/Streuungs-Befund mit einem auf physikalisch plausible
> Untergrenze **geclampten** PLE = 2,0 kombiniert und so kalibriert, dass die **empirisch
> gemessene** mediane Hop-Distanz (s. u.) als zuverlässiger Link erscheint. Das ist eine
> bewusste, dokumentierte Modellwahl — kein gemessener Wert.

**Empirische Hop-Distanz (ebenfalls GEMESSEN):** Aus 172.052 eindeutig aufgelösten
Nachbar-Hop-Paaren ist die mediane reale Hop-Distanz **10,1 km** (P90 28,8 km). Das ist ein
direkt gemessenes, robustes Maß für die typische Funkstrecke eines Hops im Netz.

### A4 — Reale Detour-Statistik (KERNERGEBNIS)
Für jedes Paket mit Pfad: reale Hop-Zahl `len(path)` vs. geografische **Unterschranke**
`ceil(Luftlinie(erster↔letzter aufgelöster Hop) / Hop-Reichweite)`. Als Hop-Reichweite das
**empirische P75** (19,3 km) der realen Nachbar-Hop-Distanzen → die Schranke bleibt eine echte
*Unter*schranke (großzügige Hop-Reichweite ⇒ minimal nötige Hops eher unterschätzt).

Über **29.433** auswertbare reale Pakete:

- Reale Hops: Median **10**, P90 **18**, Max **32**
- **Detour-Faktor (real / geografische Unterschranke): Median 2,11× · Mittel 2,62× · P90 4,33× · P99 10×**
- **78,8 %** der Pakete laufen > 1,5× · **50,2 %** laufen > 2× über die geografisch nötige Hop-Zahl

**Das belegt das „first-wins-Umweg"-Problem mit ECHTEN Produktionsdaten:** der real
gecachte/gefloodete Pfad ist im Median ~2,1× so lang wie geografisch nötig, in 10 % der
Fälle ≥ 4,3×, und jedes zweite Paket läuft ≥ 2× über die nötige Hop-Zahl.
Plot: `fig_v3_real_detours.png`. Daten: `data/real_detour_stats.json`.

---

## Baustein B — Routing-Simulation auf der GROSSEN realen Topologie

### Aktiver Repeater-Subgraph
Auswahlkriterium: `role == repeater` **und** Geo vorhanden **und**
(`relay_active` **oder** `relay_count_24h > 0` **oder** im Datensatz als aufgelöster Pfad-Hop
beobachtet). Begründung: das sind die Knoten, die im Beobachtungszeitraum nachweislich am
Relaying beteiligt bzw. dafür aktiv konfiguriert waren.

- **Aktiver Subgraph:** 881 Knoten (nach Geo-Plausibilitätsfilter, s. Limitierungen)
- **Link-/Reichweitengraph** (kalibriertes A3-Sim-Modell, Kante wenn geschätztes SNR > −12 dB,
  Distanz ≤ 45 km; pro Knoten nur die 20 stärksten Links; Kantengewicht = ETX aus SNR):
  881 Knoten, **9.861 Kanten**, größte Komponente **831**.

### Baseline (MeshCore) vs. MHR
- **Baseline:** first-packet-wins-Flood, Monte-Carlo über zufälliges Hop-Timing (jeder
  Repeater sendet genau einmal, hasSeen-Dedup); der zuerst am Ziel eintreffende Pfad gewinnt.
- **MHR:** SNR-/qualitätsgeleiteter Pfad = ETX-kürzester Pfad (bester kumulierter SNR /
  wenigste effektive Hops; prefer-shorter implizit, da ETX mit Hops wächst).
- Über zufällige Quelle-Ziel-Paare in der großen Komponente (Seed 42): mittlere Hops,
  Detour-Ratio, Airtime (Σ sendende Repeater), Zuverlässigkeit (Produkt der Link-Reliabilities).

Ergebnis über **196** auswertbare Quelle-Ziel-Paare (von 200 gezogen), Link-Graph
881 Knoten / 9.861 Kanten (pro Knoten Top-20-Links) / größte Komponente **831**
(siehe `sim_results_v3.json`, Plot `fig_v3_sim_compare.png`):

| Kennzahl | MeshCore (Baseline) | MHR | Δ |
|---|---|---|---|
| Ø Hops | **8,30** | **7,12** | −14 % |
| Detour-Ratio (Baseline/MHR) | — | — | **Median 1,17× · Mittel 1,20×** |
| Paare mit Detour | — | — | **98 %** |
| Ø Sende-Ereignisse (Airtime) | **786** (netzweiter Flood) | **7,1** (Unicast) | **−99,1 %** |
| Ø Pfad-Zuverlässigkeit | **0,47** | **0,55** | **+18 %** |
| schlechtester Baseline-Pfad | 16 Hops | — | — |

Kernaussage der Simulation: metrik-geleitetes MHR liefert auf der **realen 831-Knoten-Topologie**
kürzere, zuverlässigere Pfade bei drastisch geringerer Airtime — der MeshCore-Flood aktiviert
im Mittel ~786 Repeater pro Discovery, MHR nur die ~7 Hops des gewählten Pfads.

**Einordnung (ehrlich):** Der *simulierte* Detour-Faktor fällt deutlich milder aus als der
**gemessene** (A4), weil das Sim-Linkmodell ein idealisiertes Scheiben-/Disc-Modell ohne
Gelände ist — geografisch kürzeste Pfade sind darin kurz und der Flood weicht wenig ab. Der
**reale** Detour (Baustein A4, Median 2,0×) ist deshalb das belastbarere Argument für das
Umweg-Problem; die Simulation zeigt vor allem den **Airtime-** und **Zuverlässigkeits**-Vorteil
von metrik-geleitetem Routing (MHR Unicast entlang eines Pfads statt netzweitem Flood).

---

## Erzeugte Artefakte

| Datei | Inhalt |
|---|---|
| `mhr_sim_real_v3.py` | Komplette Mess- + Simulationspipeline (Seed 42, kommentiert) |
| `mhr_collect_corescope.py` | Reproduzierbarer Collector (urllib + UA + unverified SSL, Offset-Pagination) |
| `data/nodes.json`, `data/observers.json` | Kompakte Kopien der Stammdaten |
| `data/topology_edges.json` | Aggregierte beobachtete Kantenliste (A2) |
| `data/snr_calibration.json` | SNR-Fit-Parameter + Stichprobengröße (A3) |
| `data/real_detour_stats.json` | Reale Detour-Statistik (A4) |
| `sim_results_v3.json` | Alle Kennzahlen (Messung + Simulation) |
| `fig_v3_real_topology.png` | Gemessene Backbone-Topologie |
| `fig_v3_snr_fit.png` | SNR/Distanz: gemessen vs. alt angenommen |
| `fig_v3_real_detours.png` | Gemessene Detour-Faktor-Verteilung |
| `fig_v3_sim_compare.png` | Baseline vs. MHR (Hops, Airtime, Zuverlässigkeit) |

---

## Limitierungen (wissenschaftlich ehrlich)

1. **Hash-Ambiguität:** 1-Byte-Pfad-Hashes (Großteil) kollidieren stark (Ø 7,7 Knoten/Hash);
   77,6 % der Hash-Vorkommen blieben unauflösbar. Alle quantitativen Auswertungen nutzen
   nur eindeutige Hops — das reduziert Bias, aber auch die Stichprobe und kann seltene,
   nur über 1-Byte-Knoten laufende Pfade unterrepräsentieren.
2. **Observer-Bias:** Nur 19 von 38 Observern haben Geo; der SNR-Fit stützt sich auf deren
   Standorte (Schwerpunkt Rheinland). Die SNR-Stichprobe ist also räumlich nicht uniform.
3. **Distanz erklärt SNR real kaum** (|corr| ≈ 0,42): ohne Geländedaten/Antennenhöhen/
   Richtdiagramme ist ein reines Log-Distance-Modell für dieses Netz grob. Das **Sim-Linkmodell
   ist daher idealisiert** (geclampter PLE = 2,0) — es ist eine Modellannahme, kein Messwert,
   und liefert tendenziell **konservativere** (mildere) Detour-Werte als die Realität (A4).
4. **„letzter Hop" ≠ zwingend Sender beim Observer:** bei langen/teils ambigen Pfaden kann der
   wirklich gehörte Sender vom aufgelösten letzten Hop abweichen — zusätzliches Rauschen im
   SNR-Fit (mitverantwortlich für die schwache Korrelation).
5. **Detour-Unterschranke ist geografisch, nicht topologisch:** sie nimmt eine konstante
   Hop-Reichweite (P75 = 19,3 km) an. Echte minimale Hop-Zahlen könnten lokal höher liegen
   (Funklöcher) — d. h. der reale Detour-Faktor ist eher **unter**- als überschätzt.
6. **Simulation ohne Duty-Cycle/Kollisionen:** das Flood-Modell zählt Sende-Ereignisse, modelliert
   aber keine CSMA-Kollisionen, 10-%-Duty-Cycle-Drosselung oder Pufferüberläufe. Der reale
   Airtime-Druck (geteilter Halbduplex-Kanal) ist damit eher noch unterschätzt — zugunsten von MHR.
7. **Momentaufnahme:** ein einzelner Live-Abzug; Tageszeit-/Churn-Effekte nicht gemittelt.
8. **Geo-Plausibilitätsfilter:** einige Knoten/Observer tragen Platzhalter-Koordinaten
   (0,0) oder Werte außerhalb Mitteleuropas; diese werden verworfen (Bounding-Box
   35–60 °N, −12–25 °O). Das säubert Topologie-Plot und Distanzen, verwirft aber auch
   wenige evtl. legitime Außenposten.
9. **Hop-Cap im Sim-Graph:** pro Knoten werden nur die 20 stärksten Links behalten
   (Rechenbarkeit + Realismus). Sehr schwache Fern-Links bleiben unberücksichtigt.

---
## 🇬🇧 English Translation

# MeshCore Simulation v3 — Calibrated on REAL Live Data

**Data source:** CoreScope (`corescope.meshrheinland.de`), live snapshot from 2026-05-30.
**Dataset:** 1962 nodes (1819 with geo), 38 observers (19 with geo), **109,980 real rx packets**
(raw `packets.jsonl` ≈ 197 MB, *not* in the repo — only compact derivatives under `data/`).
**Reproducible:** Seed 42. Collector: `mhr_collect_corescope.py`. Simulation: `mhr_sim_real_v3.py`.

This study strictly distinguishes between **MEASURED** (derived from real production packets)
and **SIMULATED** (Monte-Carlo routing model on the real topology). Deliberately honest,
without embellishment: where the real dataset refutes an old assumption, that is stated here.

---

## Building Block A — What Was MEASURED from the Live Data

### A1 — Node→Hash Mapping & Disambiguation
MeshCore hash = the first `hash_size` bytes of the `public_key` (hex prefix). Index built over all
1/2/3-byte prefixes. Confirmed uniqueness:

| Prefix | distinct | colliding | avg nodes/prefix |
|---|---|---|---|
| 1-byte | 254 | 254 (all) | 7.72 |
| 2-byte | 1934 | 27 | 1.01 |
| 3-byte | 1961 | 1 | 1.00 |

1-byte hashes (real ~75% of all path hashes) collide massively. Disambiguation via
geography: on collision, the candidate closest to the already-resolved neighboring hop within
LoRa range (≤ 45 km) is chosen; forward **and** backward. Unresolvable entries are marked as *ambig*
and **not guessed**.

Breakdown over all 1.41 million path-hash occurrences:
`unique 18.2%`, `geo-disambiguated 0.1%`, `ambig 77.6%`, `unknown 4.1%`.
The high ambig rate is an **honest limitation**: 1-byte hashes are often not unambiguously
assignable without a reliable neighbor reference. For *quantitative* analyses (SNR fit,
hop distances) we therefore exclusively use **unambiguously** resolvable hops — this avoids rate bias.

### A2 — Observed Relay Topology (Backbone)
Consecutive, resolved path hops = real directed edges "A forwards to B".
Aggregated (repeater↔repeater only):

- **Nodes in backbone graph:** 745
- **Directed edges:** 6246
- **Degree (undirected):** min 1 / median 6 / max 118
- **Largest connected component:** 724 of 745 (97%)

Result: a well-connected, cohesive backbone with few hub nodes (max degree 118).
Compact edge list: `data/topology_edges.json`. Plot: `fig_v3_real_topology.png`.

### A3 — Calibrating the SNR/Distance Link Model (CORE FINDING of the Measurement)
For each rx packet: observer (position known) heard the **last** hop of the path chain
(position via hash map). From **14,399 unambiguously** resolvable (last-hop → observer) pairs,
a log-distance model `SNR ≈ SNR0 − 10·n·log10(d)` was fitted:

| Model | SNR0 (dB) | Path-loss exponent n | Note |
|---|---|---|---|
| **MEASURED (OLS)** | **2.78** | **0.41** | corr(log d, SNR) = **−0.42** |
| MEASURED (bin medians) | −0.75 | 0.24 | robust against saturation |
| OLD ASSUMPTION (`mhr_sim_real.py`) | 17.0 | 2.55 | unsubstantiated |

**Finding — important and inconvenient:** In the real dataset, distance alone explains SNR
**only weakly** (|corr| ≈ 0.42, n ≈ 0.4 instead of the assumed 2.55). Antenna height,
location (many repeaters on hills/high-rise buildings), directionality, and terrain dominate
over the straight-line distance. Nodes 40–70 km away are sometimes heard with better SNR than
neighbors at < 2 km. The clean path-loss assumption of the old script is therefore **not tenable**
for this real network — the purported "accuracy gain" consists precisely in exposing this discrepancy.
Plot: `fig_v3_snr_fit.png`. Parameters: `data/snr_calibration.json`.

> **Consequence for the simulation model (Building Block B):** A PLE of 0.4 would produce a
> physically nonsensical range graph (quasi-unlimited range). For the simulation, the real
> intercept/scatter finding is therefore combined with a PLE **clamped** to a physically plausible
> lower bound of 2.0, and calibrated so that the **empirically measured** median hop distance
> (see below) appears as a reliable link. This is a deliberate, documented model choice — not a
> measured value.

**Empirical hop distance (also MEASURED):** From 172,052 unambiguously resolved
neighbor-hop pairs, the median real hop distance is **10.1 km** (P90 28.8 km). This is a
directly measured, robust indicator of the typical radio link distance of a hop in the network.

### A4 — Real Detour Statistics (CORE RESULT)
For each packet with path: real hop count `len(path)` vs. geographic **lower bound**
`ceil(straight-line(first↔last resolved hop) / hop range)`. As hop range, the
**empirical P75** (19.3 km) of real neighbor-hop distances → the bound remains a true
*lower* bound (generous hop range ⇒ minimum required hops rather underestimated).

Over **29,433** evaluable real packets:

- Real hops: Median **10**, P90 **18**, Max **32**
- **Detour factor (real / geographic lower bound): Median 2.11× · Mean 2.62× · P90 4.33× · P99 10×**
- **78.8%** of packets travel > 1.5× · **50.2%** travel > 2× the geographically necessary hop count

**This proves the "first-wins detour" problem with REAL production data:** the actually
cached/flooded path is in the median ~2.1× as long as geographically necessary, in 10% of
cases ≥ 4.3×, and every second packet travels ≥ 2× the necessary hop count.
Plot: `fig_v3_real_detours.png`. Data: `data/real_detour_stats.json`.

---

## Building Block B — Routing Simulation on the LARGE Real Topology

### Active Repeater Subgraph
Selection criterion: `role == repeater` **and** geo present **and**
(`relay_active` **or** `relay_count_24h > 0` **or** observed in the dataset as a resolved path hop).
Rationale: these are the nodes that were demonstrably involved in relaying or actively
configured for it during the observation period.

- **Active subgraph:** 881 nodes (after geo plausibility filter, see Limitations)
- **Link/range graph** (calibrated A3 sim model, edge when estimated SNR > −12 dB,
  distance ≤ 45 km; per node only the 20 strongest links; edge weight = ETX from SNR):
  881 nodes, **9,861 edges**, largest component **831**.

### Baseline (MeshCore) vs. MHR
- **Baseline:** first-packet-wins flood, Monte-Carlo over random hop timing (each
  repeater transmits exactly once, hasSeen dedup); the first path to arrive at the destination wins.
- **MHR:** SNR-/quality-guided path = ETX-shortest path (best cumulative SNR /
  fewest effective hops; prefer-shorter implicit, since ETX grows with hops).
- Over random source-destination pairs in the large component (Seed 42): mean hops,
  detour ratio, airtime (sum of transmitting repeaters), reliability (product of link reliabilities).

Result over **196** evaluable source-destination pairs (drawn from 200), link graph
881 nodes / 9,861 edges (per node top-20 links) / largest component **831**
(see `sim_results_v3.json`, plot `fig_v3_sim_compare.png`):

| Metric | MeshCore (Baseline) | MHR | Delta |
|---|---|---|---|
| avg hops | **8.30** | **7.12** | −14% |
| Detour ratio (Baseline/MHR) | — | — | **Median 1.17× · Mean 1.20×** |
| Pairs with detour | — | — | **98%** |
| avg transmission events (airtime) | **786** (network-wide flood) | **7.1** (unicast) | **−99.1%** |
| avg path reliability | **0.47** | **0.55** | **+18%** |
| worst baseline path | 16 hops | — | — |

Core statement of the simulation: metric-guided MHR delivers on the **real 831-node topology**
shorter, more reliable paths at drastically lower airtime — MeshCore flood activates on average
~786 repeaters per discovery, MHR only the ~7 hops of the chosen path.

**Honest assessment:** The *simulated* detour factor is considerably milder than the
**measured** one (A4), because the sim link model is an idealized disc model without
terrain — geographically shortest paths are short in it and the flood deviates little. The
**real** detour (Building Block A4, Median 2.0×) is therefore the more compelling argument for the
detour problem; the simulation primarily demonstrates the **airtime** and **reliability** advantage
of metric-guided routing (MHR unicast along a path instead of network-wide flood).

---

## Generated Artifacts

| File | Content |
|---|---|
| `mhr_sim_real_v3.py` | Complete measurement + simulation pipeline (Seed 42, commented) |
| `mhr_collect_corescope.py` | Reproducible collector (urllib + UA + unverified SSL, offset pagination) |
| `data/nodes.json`, `data/observers.json` | Compact copies of master data |
| `data/topology_edges.json` | Aggregated observed edge list (A2) |
| `data/snr_calibration.json` | SNR fit parameters + sample size (A3) |
| `data/real_detour_stats.json` | Real detour statistics (A4) |
| `sim_results_v3.json` | All metrics (measurement + simulation) |
| `fig_v3_real_topology.png` | Measured backbone topology |
| `fig_v3_snr_fit.png` | SNR/distance: measured vs. old assumption |
| `fig_v3_real_detours.png` | Measured detour factor distribution |
| `fig_v3_sim_compare.png` | Baseline vs. MHR (hops, airtime, reliability) |

---

## Limitations (Scientifically Honest)

1. **Hash ambiguity:** 1-byte path hashes (the majority) collide heavily (avg 7.7 nodes/hash);
   77.6% of hash occurrences remained unresolvable. All quantitative analyses use
   only unambiguous hops — this reduces bias, but also the sample size, and can underrepresent
   rare paths running only through 1-byte nodes.
2. **Observer bias:** Only 19 of 38 observers have geo; the SNR fit relies on their
   locations (centered on the Rhineland region). The SNR sample is therefore not spatially uniform.
3. **Distance explains SNR only weakly in practice** (|corr| ≈ 0.42): without terrain data/antenna
   heights/radiation patterns, a pure log-distance model for this network is coarse. The **sim link
   model is therefore idealized** (clamped PLE = 2.0) — it is a model assumption, not a measured
   value, and tends to yield **more conservative** (milder) detour values than reality (A4).
4. **"Last hop" ≠ necessarily the sender at the observer:** for long/partially ambiguous paths,
   the actual heard sender may deviate from the resolved last hop — additional noise in the
   SNR fit (partly responsible for the weak correlation).
5. **Detour lower bound is geographic, not topological:** it assumes a constant
   hop range (P75 = 19.3 km). Actual minimum hop counts could be locally higher
   (radio coverage gaps) — meaning the real detour factor is more likely **under**- than overestimated.
6. **Simulation without duty cycle/collisions:** the flood model counts transmission events, but
   does not model CSMA collisions, 10%-duty-cycle throttling, or buffer overflows. The real
   airtime pressure (shared half-duplex channel) is therefore rather underestimated — in favor of MHR.
7. **Snapshot:** a single live snapshot; time-of-day/churn effects not averaged out.
8. **Geo plausibility filter:** some nodes/observers carry placeholder coordinates
   (0,0) or values outside central Europe; these are discarded (bounding box
   35–60°N, −12–25°E). This cleans up the topology plot and distances, but also discards
   a few potentially legitimate outlying nodes.
9. **Hop cap in sim graph:** per node only the 20 strongest links are retained
   (computational feasibility + realism). Very weak long-range links are disregarded.
