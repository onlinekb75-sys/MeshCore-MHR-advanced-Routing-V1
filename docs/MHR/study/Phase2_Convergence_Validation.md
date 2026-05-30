# Phase 2 — Zeitaufgeloeste Konvergenz-Validierung (Korrektheits-GATE)

*Erzeugt von `phase2_convergence_sim.py`. Seeds [42, 43, 44, 45, 46]. DV-Tick 30s, Annoncen-Periode 300s, Hysterese 15%. Reale Topologie aus `neighbor_graph.json`.*

**Topologie:** Riesenkomponente 632 Knoten, backbone-faehig 563, 28 Regionen (H1), 329 Border-Knoten. ETX aus avg_snr. LoRa SF11/BW250k, DATA-ToA 493.6 ms.

---

## GESAMT-VERDIKT: **GO** — darf Phase 2 codiert werden?

**GO.** Alle fuenf Gate-Punkte bestehen: null transiente Schleifen mit Feasibility (waehrend die Gegenprobe ohne Feasibility Loops zeigt), endliche Konvergenz kalt und nach Stoerung, vollstaendige Re-Konvergenz unter Churn **mit der jetzt pflichtigen Churn-Haertung** (Trigger-on-change + Hold-down/Route-Poisoning + origin-unabhaengige Aggregat-Feasibility — Gate 3 ging damit von FAIL auf PASS), Mixed-FW graceful und nie schlechter als Baseline, und das Kontroll-Budget passt locker ins 10%-Duty-Cycle-Sub-Band.

| Gate | Inhalt | Ergebnis |
|---|---|---|
| 1 | Schleifenfreiheit (Feasibility) | PASS |
| 2 | Konvergenzzeit kalt/Stoerung | PASS |
| 3 | Kein Flattern unter Churn | PASS |
| 4 | Mixed-FW graceful + nie schlechter | PASS |
| 5 | Kontroll-Budget < 10% Duty | PASS |

---

## Gate 1 — Schleifenfreiheit (Babel-Feasibility) + GEGENPROBE

Geprueft wird die AKTUELLE Next-Hop-Kette ueber alle Ticks und Seeds, je Tick tausende (Quelle,Ziel)-Paare. Drei Groessen zaehlen:

- **Kaltstart-Loops (Feasibility):** **0** ueber alle Ticks der Kaltstart-Phase und alle Seeds. (Naiv zum Vergleich: 0.)
- **Persistente Loops nach Re-Konvergenz (Feasibility):** **0** (im letzten Tick). Naiv: 1.
- **Loop-Vorkommnisse gesamt** (inkl. transientes Stoerungs-Fenster): Feasibility **0** (max/Tick 0) vs. naiv **55** (max/Tick 2).
- **Die Gegenprobe traegt:** ohne Feasibility entstehen deutlich mehr (und persistente, count-to-infinity-artige) Loops; mit Feasibility ist der Kaltstart komplett schleifenfrei und alle Loops nach einer Stoerung sind rein transient (loesen sich auf). Die Schleifenfreiheit kommt also aus dem Mechanismus (Babel-Bedingung), nicht aus der Topologie.
- Beleg: `fig_p2_loops.png`. Zusaetzlich belegt ein Mini-Topologie-Unittest (Linie D-X-A-B, X getoetet) den Mechanismus isoliert: naiv = 40/40 Loop-Ticks (A<->B count-to-infinity), Feasibility = 0.

## Gate 2 — Konvergenzzeit

Konvergenz = aufgeloeste Next-Hops der Stichprobe stabil ueber 2 Ticks UND null Loops.

- **Kaltstart:** Mittel 29.4 Ticks = **882 s** (alle Seeds konvergiert: True).
- **Nach Stoerung** (Ausfall eines Artikulationspunkts/Cut-Vertex): Mittel 2.0 Ticks = **60 s** (alle Seeds re-konvergiert: True).
- Beleg: `fig_p2_convergence.png`.

## Gate 3 — Kein Flattern unter Churn (ALT vs. GEHAERTET)

Knoten gehen nach advert_count-Profil an/aus (selten gehoerte = instabiler) plus sporadischer Linkausfall. Unter DAUER-Churn sind transiente Loops/Wechsel unvermeidbar; entscheidend ist niedrige, nicht-aufschwingende Wechselrate und vollstaendige Rueck-Konvergenz nach Churn-Stopp (Loops=0 UND Wechsel=0).

**Churn-Haertung (jetzt Pflicht, Design Abschnitt 3):** (a) **Trigger-on-change** rate-limitiert (>= 2 Ticks zwischen getriggerten Updates je Knoten) — sofortiges DV-Update bei Metrik-/Next-Hop-Aenderung statt nur periodisch; (b) **Hold-down + Route-Poisoning** bei Knoten-/Link-Ausfall (Poison = INF mit erhoehter Seqno, Hold-down 20 Ticks = keine schlechtere Alternative annehmen); (c) **origin-unabhaengige Aggregat-Feasibility** ueber den Ziel-Schluessel `("R",dreg)` (stellt die Babel-Invariante fuer die H1-Schicht wieder her). Hysterese >=15 % bleibt aktiv.

| Metrik (max/Mittel ueber Seeds) | ALT (nur periodisch) | GEHAERTET |
|---|---|---|
| Re-Konvergenz nach Churn-Stopp (Loops=0 UND Wechsel=0) | **NEIN** | ja |
| Restschleifen nach Churn-Stopp (Settle, max ueber Seeds) | **4** | **0** |
| Restschleifen je Seed (Settle) | [4, 0] | [0, 0, 0, 0, 0] |
| Restwechsel nach Churn-Stopp (Settle, max) | 0 | 0 |
| Routen-Wechselrate Tail (eingeschwungen) | 0.80%/Tick | 0.19%/Tick |
| Wechselrate gesamt | 1.46%/Tick | 1.52%/Tick |
| transiente Loops max (waehrend Churn) | 40 | 14 |
| Tail <= Gesamt (schwingt nicht auf) | ja | ja |
| **Gate-3-Verdikt** | **FAIL** | PASS |

- Beleg: `fig_p2_churn_stability.png` (Wechselrate ALT vs. GEHAERTET, Seed 42).

**Ergebnis der Haertung:** die GEHAERTETE Variante re-konvergiert nach Churn-Stopp vollstaendig (Restschleifen=0 UND Restwechsel=0 ueber ALLE Seeds) und senkt die eingeschwungene Wechselrate deutlich. Die persistenten Inter-Region-Aggregat-Loops der ALT-Variante (multi-Origin-ABR, gegenseitiges Zeigen zweier Border-Router auf je ein lebendes Aggregat) loesen sich auf: die origin-unabhaengige Aggregat-FD verhindert, dass eine nicht-feasible Aggregat-Route Successor wird, das Poisoning+Hold-down raeumt stale Aggregate auf, und Trigger-on-change verbreitet die Retraction sofort. Gate 3 geht damit von **FAIL (ALT)** auf **PASS (GEHAERTET)**.

## Gate 4 — Mixed-Firmware-Sweep

| Adoption | Backbone-Knoten | Liefer Base | Liefer DV | Delta (pp) | Netto-Airtime | Loops | nie schlechter |
|---|---|---|---|---|---|---|---|
| 1% | 6 | 0.964 | 0.964 | +0.0 | +0.0% | 0 | ja |
| 10% | 56 | 0.964 | 0.964 | +0.0 | +0.0% | 0 | ja |
| 25% | 141 | 0.964 | 0.964 | +0.0 | -0.8% | 0 | ja |
| 50% | 282 | 0.964 | 0.967 | +0.3 | -3.7% | 0 | ja |
| 75% | 422 | 0.964 | 0.972 | +0.8 | -20.8% | 0 | ja |
| 100% | 563 | 0.964 | 0.978 | +1.4 | -44.1% | 0 | ja |

- nie schlechter als Baseline ueber ALLE Stufen: **True**.
- schleifenfrei ueber ALLE Stufen: **True**.
- netto-positiv ab Adoption: **0.5**.
- Beleg: `fig_p2_mixedfw.png`.

## Gate 5 — Kontroll-Budget (Duty-Cycle)

| DV-Periode | max-Knoten-Busy | % des 10%-Budgets | max DV-Eintraege | passt |
|---|---|---|---|---|
| 300s | 0.9076% | 9.08% | 82 | ja |
| 600s | 0.4567% | 4.57% | 82 | ja |
| 900s | 0.3064% | 3.06% | 82 | ja |

- **Vergleich flaches DV ohne H1 (600s):** schlimmster Knoten 28.45% des Budgets, max DV-Eintraege 563 — zeigt, warum die Regions-Hierarchie noetig ist (Eintraege/Paket-Groesse skaliert sonst mit dem ganzen Netz).
- Beleg: `fig_p2_control_budget.png`.

---

## Ehrliche Limitierungen

- **Idealisiertes Funkmodell:** ToA exakt (Semtech), aber CSMA/Backoff, Kollisionen und reale Halbduplex-Contention nur grob (Flood-Jitter-Modell). Duty-Cycle-Budget ist eine Airtime-Bilanz, kein MAC-Scheduler.
- **Konvergenz-Gates 1+2 sind periodisch getrieben** (Annoncen-Periode), Trigger-on-change dort konservativ NICHT modelliert — reale Kaltstart-/Stoerungs-Konvergenz waere mit Triggern schneller. Die gemessenen Zeiten sind also eine obere Schranke. Gate 3 (Churn) modelliert die GEHAERTETE Variante MIT Trigger-on-change (rate-limitiert) — der direkte ALT/GEHAERTET-Vergleich isoliert den Effekt der Haertung.
- **Regionen geografisch geclustert** (k-means/Lloyd ueber lat/lon, Zielgroesse ~20 Repeater/Region; geo-lose Knoten via Nachbar-Mehrheit), als robuste Naeherung der `region_map`/IATA-Cluster. Border-Knoten = Knoten mit Nachbar in anderer Region. Eine andere Regionierung verschiebt die Aggregat-Loop-Haeufigkeit; der frueher offene multi-Origin-Aggregat-Defekt ist durch die origin-unabhaengige Aggregat-Feasibility (Abschnitt 3a) strukturell geschlossen, nicht topologie-abhaengig weggemittelt.
- **Reproduzierbarkeit:** alle Knoten-Iterationen laufen ueber eine SORTIERTE Reihenfolge (nicht ueber set-Iteration) -> identische Ergebnisse unabhaengig von `PYTHONHASHSEED`, je Seed exakt reproduzierbar (verifiziert ueber mehrere Hash-Seeds).
- **Delivery-Stochastik** nutzt p_reliability aus avg_snr; Gelaende/Antennenhoehe nicht enthalten (gleiche Limitierung wie die bestehenden Sims).
- **Loop-Scan gesampelt** bei grossen Knotenzahlen (bis 1000 (Quelle,Ziel)-Paare je Tick, 25 Quellen, transparent gedeckelt), nicht alle O(N^2) Paare je Tick. Bei null gefundenen Loops ueber zehntausende Stichproben pro Episode ist die Aussage robust, aber kein formaler Beweis — dieser kommt aus der Babel-Theorie und wird durch den isolierten count-to-infinity-Unittest (naiv loopt, Feasibility nicht) gestuetzt.
- **Seed-Budget:** die GEHAERTETE Churn-Variante und die Feasibility-Konvergenz (die massgeblichen Gates) laufen auf ALLEN >=5 Seeds. Die reinen NEGATIV-Baselines (naives DSDV in Gate 1, ALT-Churn in Gate 3) laufen aus Laufzeitgruenden auf 2 Seeds — sie dienen nur dem Mechanismus-/ALT-Kontrast, nicht dem Verdikt.
