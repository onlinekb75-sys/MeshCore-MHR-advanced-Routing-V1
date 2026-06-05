# Langsame per-Node-Kalibrierung & On-Node-Machbarkeit (Ergebnis: NO-GO)

*Frage: Bringt eine langsame (12–48 h) Selbst-Kalibrierung pro Knoten an die lokale Dichte/Rolle
mehr als der globale statische sichere Satz — v. a. an den Extremen (dichte Hubs / sparse Brücken)?
Und ist eine abgespeckte Version auf dem Node lauffähig?*

**Antwort: NEIN (zweites, unabhängiges NO-GO).** Lokal-kalibriert schlägt den statischen Satz nicht;
es ist sogar minimal schlechter bei der Airtime. Der Grund ist strukturell: die **Per-Paket-Guards
G1–G5 adaptieren bereits in Echtzeit** an die lokale Lage (tatsächliche gehörte Redundanz pro Paket)
— besser als ein über 12–48 h gemittelter Vorab-Wert das je könnte.

Validierung: `local_calib_sim.py` / `local_calib_results.json` (echter neighbor-graph: 632 Knoten /
1577 Kanten, Klassen: 129 Hubs / 207 mittel / 296 Brücken-Blätter; Seed 42, ≥5 Seeds).

## Ergebnis
| Kennzahl | Wert |
|---|---|
| Airtime lokal-kalibriert vs. statisch (hohe Adoption) | **−0,50 pp** (schlechter) |
| Extra-Gewinn an **Hubs** | **−0,04 pp** (kein Vorteil) |
| Schlechteste Brücken-Lieferquote-Δ (lokal) | −0,017 (safe) |
| abgespeckte On-Node-Kennlinie: behaltener Gewinn | **0 %** (kein Gewinn vorhanden) |
| Entscheidung | **`go_local_full = false`** |

## Ehrliche Nuance (ein verwertbarer Nebenbefund)
Die feinere **Pro-Klasse**-Analyse zeigt: der *statische* Satz dippt an **Brücken** bei α=0,5 leicht
(worst class-Δ −0,052), die lokale Kalibrierung nicht (−0,017). Lokal ist also etwas *sicherer* an
Brücken, aber dafür minimal *schlechter* bei der Airtime — keiner dominiert. **Konsequenz:** statt
eines Reglers genügt eine kleine **statische Default-Nachjustierung** (z. B. `supp_min_degree` an
Brücken konservativer), falls Stufe B je scharf geschaltet wird. Kein adaptiver Apparat nötig.

## On-Node-Machbarkeit (Zusatzfrage)
Die abgespeckte Kalibrier-Kennlinie wäre **trivial** node-tauglich: ~9 Byte Lookup, 4 Byte Zustand
pro Knoten, ≤ 5 Integer-Vergleiche, kein float/Heap/Sort, 1× pro 12–48 h. Machbarkeit ist also **kein**
Hinderungsgrund — aber sie ist **moot**, weil es keinen Gewinn zu holen gibt. (Eine *reichhaltige*
RX-Statistik/Telemetrie auf dem Node ist davon unabhängig sinnvoll — siehe Empfehlung unten.)

## Gesamtbild: zwei unabhängige NO-GOs zur Auto-Adaption
1. Schneller (1–2 h) supp_prob-Regler → +0,76 pp (zu wenig). `Adaptive_Controller_Design.md`.
2. Langsame (12–48 h) per-Node-Kalibrierung → −0,5 pp (schlechter). Dieses Dokument.

Robuste Schlussfolgerung: **Die Per-Paket-Guards holen die lokale Adaption bereits ein; ein langsamer
äußerer Regelkreis für Suppressions-Parameter lohnt nicht.** Statischer sicherer Satz + Guards bleibt
die Empfehlung.

## Empfehlung trotz NO-GO
Eine **passive On-Node-RX-Statistik/Telemetrie** (kein Regler!) ist sehr wohl sinnvoll und auf dem
ESP32-S3 problemlos: lokaler Pfadlängen-Histogramm (= lokaler Durchmesser), Nachbar-SNR-Verteilung,
Redundanz-/Cover-Histogramm, Traffic nach Typ, Duty-Cycle-Auslastung, Churn. Nutzen: Diagnose,
fundierte *manuelle* Parameterwahl, Speisen der bestehenden Guards (2-Hop-Frische, Cover-Stats),
Anomalie-/Selbstschutz (z. B. Advert-Backoff bei gesättigtem Duty-Cycle). Das nutzt die CPU-Reserve
für echten Wert — ohne die nachweislich nutzlose Auto-Tuning-Schleife.

## Limitierungen
Modell-Idealisierungen (kein Duty-Cycle/Kollisionen — die würden Suppression eher aufwerten, aber
nicht die *relative* Aussage adaptiv-vs-statisch ändern); neighbor-graph erfasst nur genutzte Links;
Momentaufnahme; Klassengrenzen nach Grad-Quantilen (q40/q80).

---
## 🇬🇧 English Translation

# Slow per-Node Calibration & On-Node Feasibility (Result: NO-GO)

*Question: Does a slow (12–48 h) self-calibration per node adapted to local density/role provide
more benefit than the global static safe set — especially at the extremes (dense hubs / sparse bridges)?
And is a stripped-down version runnable on the node?*

**Answer: NO (second, independent NO-GO).** Locally calibrated does not beat the static set;
it is even marginally worse in terms of airtime. The reason is structural: the **per-packet guards
G1–G5 already adapt in real time** to the local situation (actually heard redundancy per packet)
— better than any pre-averaged value over 12–48 h ever could.

Validation: `local_calib_sim.py` / `local_calib_results.json` (real neighbor-graph: 632 nodes /
1577 edges, classes: 129 hubs / 207 medium / 296 bridge-leaves; seed 42, ≥5 seeds).

## Result
| Metric | Value |
|---|---|
| Airtime locally-calibrated vs. static (high adoption) | **−0.50 pp** (worse) |
| Extra gain at **hubs** | **−0.04 pp** (no advantage) |
| Worst bridge delivery-rate delta (local) | −0.017 (safe) |
| Stripped-down on-node characteristic: retained gain | **0 %** (no gain present) |
| Decision | **`go_local_full = false`** |

## Honest Nuance (one actionable side finding)
The finer **per-class** analysis shows: the *static* set dips slightly at **bridges** at α=0.5
(worst class-Δ −0.052), local calibration does not (−0.017). Local is therefore somewhat *safer* at
bridges, but marginally *worse* in airtime — neither dominates. **Consequence:** instead of a
controller, a small **static default readjustment** is sufficient (e.g. `supp_min_degree` more
conservative at bridges), if Stage B is ever activated. No adaptive machinery needed.

## On-Node Feasibility (Additional Question)
The stripped-down calibration characteristic would be **trivially** node-compatible: ~9 bytes lookup,
4 bytes state per node, ≤ 5 integer comparisons, no float/heap/sort, 1× per 12–48 h. Feasibility is
therefore **not** an obstacle — but it is **moot**, because there is no gain to be had. (A *rich*
RX statistics/telemetry on the node is independently useful — see recommendation below.)

## Overall Picture: Two Independent NO-GOs for Auto-Adaptation
1. Fast (1–2 h) supp_prob controller → +0.76 pp (too little). `Adaptive_Controller_Design.md`.
2. Slow (12–48 h) per-node calibration → −0.5 pp (worse). This document.

Robust conclusion: **The per-packet guards already capture local adaptation; a slow outer control loop
for suppression parameters is not worthwhile.** Static safe set + guards remains the recommendation.

## Recommendation Despite NO-GO
A **passive on-node RX statistics/telemetry** (no controller!) is very much useful and straightforward
on the ESP32-S3: local path-length histogram (= local diameter), neighbor SNR distribution,
redundancy/cover histogram, traffic by type, duty-cycle utilization, churn. Benefit: diagnostics,
well-founded *manual* parameter selection, feeding the existing guards (2-hop freshness, cover stats),
anomaly/self-protection (e.g. advert backoff when duty-cycle is saturated). This uses the CPU reserve
for real value — without the demonstrably useless auto-tuning loop.

## Limitations
Model idealizations (no duty-cycle/collisions — these would tend to upgrade suppression, but would
not change the *relative* statement adaptive-vs-static); neighbor-graph only captures used links;
snapshot; class boundaries by degree quantiles (q40/q80).
