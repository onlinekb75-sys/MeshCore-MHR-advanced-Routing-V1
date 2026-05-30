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
