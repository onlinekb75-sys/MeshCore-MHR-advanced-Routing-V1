# Adaptiver Suppressions-Regler — Design & Validierung (Ergebnis: NO-GO)

*Frage (Nutzer): Kann sich die Firmware alle 1–2 h selbst an die Umgebung anpassen, um die
Suppression optimal einzustellen? Ist das sinnvoll?*
**Antwort nach Simulation: technisch möglich und stabil — aber NICHT lohnend. Der statische
sichere Parametersatz fängt den Gewinn bereits ein.**

Validierung: `adaptive_sim.py` / `adaptive_results.json` (echter neighbor-graph, Seed 42, ≥5 Seeds,
40 Adaptions-Ticks pro Szenario). Baut auf der validierten guarded Suppression auf
([Suppression_Design.md](Suppression_Design.md), [SUPPRESSION_VALIDATION.md](SUPPRESSION_VALIDATION.md)).

## Das untersuchte Design
Schnelle Sicherheit bleibt per-Paket bei den Guards G1–G5. Ein LANGSAMER äußerer Regelkreis (Tick ≈
1–2 h) stellt pro Knoten nur die Suppressions-**Aggressivität** (`supp_prob`) **innerhalb des
validierten sicheren Fensters** nach — anhand lokal messbarer Größen (Nachbar-Dichte, gehörte
Cover-Redundanz, Airtime-Druck, Churn). Asymmetrisch (schnell zurück auf konservativ bei
Churn/sinkender Redundanz, langsam vor), gedämpft, bounded. Da die Guards die Lieferquote für jeden
Parameterwert schützen, kann die Adaption nur Airtime gegen Marge tauschen.

## Ergebnisse (statisch sicher vs. adaptiv, Airtime-Gewinn)
| Szenario | statisch | adaptiv | Δ (pp) | strikt-safe Ticks (stat/adapt) |
|---|---|---|---|---|
| Tag/Nacht-Last | 12,3 % | 14,8 % | **+2,5** | 20 / 20 |
| Knoten-Churn | 12,6 % | 14,5 % | **+1,9** | 17 / 14 |
| Dichte-Verschiebung | 10,5 % | 9,6 % | **−0,9** | 22 / 19 |
| Linkausfall | 11,8 % | 11,4 % | **−0,3** | 17 / 20 |
| **Mittel** | | | **+0,76** | |

- **Konvergenz/Oszillation:** der Regler **oszilliert nicht** (`any_oscillation = false`; Settle-Varianz
  von supp_prob 3e-7 … 7e-3; er pendelt sich je nach Dichte um p ≈ 0,58–0,77 ein). Das Konzept ist
  also stabil — das war nicht das Problem.
- **Mehrwert:** im Mittel nur **+0,76 pp** Airtime gegenüber dem statischen sicheren Satz (max
  +2,48 pp), in zwei Szenarien sogar leicht schlechter. Unter der Nutzen-Schwelle (2,0 pp).
- **Sicherheit:** `all_scenarios_safe = false` — der adaptive Regler ist in einzelnen Ticks minimal
  weniger strikt-safe als der statische Satz (der durchgehend safe ist).

## Entscheidung: NO-GO (`go = false`)
Der adaptive Regler bringt für die **zusätzliche Komplexität und ein minimales Sicherheitsrisiko**
nur einen vernachlässigbaren Airtime-Vorteil über den ohnehin sehr guten statischen sicheren Satz.
Gemäß Projektpriorität **Qualität & Stabilität vor letzter Optimierung** wird er **nicht codiert**.

**Stattdessen empfohlen:** den **statischen** sicheren Satz nutzen (`k_cover=2, min_degree=3..4,
snr_floor=-6, prob=0.8`). Er ist einfacher, durchgehend safe und holt praktisch denselben Gewinn.

**Aufgehoben, nicht verloren:** Falls künftig Daten aus *sehr* heterogenen Netzen (z. B. extreme
Tag/Nacht-Schwankung, wo Adaptiv +2,5 pp zeigte) einen größeren Spread belegen, kann der Regler — er
ist ja stabil — als opt-in Schicht reaktiviert und neu bewertet werden. Heute: nein.

## Limitierungen
Verteilte Regler beeinflussen sich gegenseitig (im Modell erfasst, real evtl. anders); lokaler
Airtime-Proxy ≠ globale Delivery (durch Guards aber abgesichert); Sim-Idealisierungen (kein
Duty-Cycle/Kollisionen). Die Kernaussage „Mehrwert < statischer Satz" ist über alle Szenarien robust.
