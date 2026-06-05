# Studien-Ergebnisse: Routing-Mechanismen × Adoption (MeshCore, Realdaten)

Erzeugt von `study_sim.py`. **GEMESSEN** (Realtopologie/Kalibrierung aus `../sim/data/`) ist strikt getrennt von **SIMULIERT** (Flood-Routing-Modell + Mechanismen + Sweep, dieses Skript).

## Topologie (GEMESSEN/abgeleitet, wie v3)

- Aktiver Repeater-Subgraph: **814** Knoten (role=repeater, Geo, relay-aktiv/24h>0 ODER real als Pfad-Hop gesehen — reproduziert v3's ~831-Knoten-Subgraph).
- Reichweiten-Linkgraph: **814** Knoten, **9275** Kanten; größte Komponente **776** Knoten / 9226 Kanten (hier wird simuliert).
- Greedy-Relay-Menge (M4, CDS): **49** Knoten (6.3%).
- **Quervalidierung** gegen beobachtete Kanten (`topology_edges.json`): 3459 beob. Kanten, 3306 zwischen aktiven Knoten, davon 1380 (41.7%) vom geometrischen Reichweitenmodell reproduziert.
- Reale Detour-Median-Referenz (GEMESSEN): **2.11×**.

## Modellannahmen (SIMULIERT) — ehrlich getrennt von der Messung

- Flood timing-getrieben (PQ nach Ankunftszeit), Stock-Knoten: first-packet-wins + Zufalls-Jitter. **Airtime = Anzahl tatsächlich sendender Knoten je zugestellter Nachricht.**
- Link-Stochastik: eine Aussendung erreicht Nachbar v mit P=Reliability des Links (Monte-Carlo über Seed). Reliability des genutzten Pfads = Produkt der Link-Reliabilities.
- Mechanismen sind **lokale Regeln** der Neu-Firmware-Knoten; Stock-Knoten fluten unverändert (eingebautes Sicherheitsnetz).
- **Baseline (M0, α=0):** Lieferquote **0.930**, Airtime **751.2** Sende-Ereignisse/Zustellung, Detour-Median 1.11.

- Konfiguration: 5 Seeds, 120 Paare/Seed, MC=1; Laufzeit 763.5s.

## Safety-Invariante

Für jeden (Mechanismus, α): **Lieferquote ≥ Baseline UND Airtime ≤ Baseline.** Verletzung ⇒ Mechanismus bei diesem α disqualifiziert.

**Monte-Carlo-Rausch-Band:** Mechanismen, die keine Sender unterdrücken (M1/M5/M7-hoch), haben dieselbe *wahre* Airtime/Lieferquote wie die Baseline; gemessene Mini-Abweichungen sind reines Sampling-Rauschen (je α/Seed andere RNG-Ziehungen). Als Verletzung gilt nur ein Unterschreiten außerhalb von ±2·Standardfehler der Baseline bzw. einer absoluten Mindesttoleranz (Lieferquote ±0.024, Airtime ±6.2 = 0.82%). Die Spalte *Safety* nutzt dieses Band; *streng* = ohne Band.

## Ergebnis-Tabellen je Mechanismus (Rollout: Top-Traffic)

### M0

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.05 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.25 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.5 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |

### M1

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.935 | +0.005 | 752.8 | +0.2% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.935 | +0.005 | 752.1 | +0.1% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.938 | +0.008 | 753.6 | +0.3% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.940 | +0.010 | 753.9 | +0.4% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.933 | +0.003 | 752.0 | +0.1% | 1.10 | 1.00 | ✅ |
| 0.5 | 0.948 | +0.018 | 753.9 | +0.4% | 1.00 | 1.00 | ✅ |
| 1.0 | 0.940 | +0.010 | 753.4 | +0.3% | 1.00 | 1.00 | ✅ |

### M2k2

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.947 | +0.017 | 749.7 | -0.2% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.945 | +0.015 | 746.4 | -0.6% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.933 | +0.003 | 716.2 | -4.7% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.932 | +0.002 | 676.3 | -10.0% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.898 | -0.032 | 567.5 | -24.5% | 1.17 | 1.00 | ❌ VERLETZT |
| 0.5 | 0.867 | -0.063 | 389.6 | -48.1% | 1.22 | 1.00 | ❌ VERLETZT |
| 1.0 | 0.795 | -0.135 | 115.2 | -84.7% | 1.50 | 1.00 | ❌ VERLETZT |

### M2k3

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.935 | +0.005 | 749.3 | -0.3% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.940 | +0.010 | 744.7 | -0.9% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.927 | -0.003 | 714.9 | -4.8% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.927 | -0.003 | 680.4 | -9.4% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.910 | -0.020 | 577.7 | -23.1% | 1.14 | 1.00 | ✅ |
| 0.5 | 0.895 | -0.035 | 419.1 | -44.2% | 1.20 | 1.00 | ❌ VERLETZT |
| 1.0 | 0.867 | -0.063 | 169.3 | -77.5% | 1.25 | 1.00 | ❌ VERLETZT |

### M3

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.942 | +0.012 | 750.2 | -0.1% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.932 | +0.002 | 748.0 | -0.4% | 1.14 | 1.00 | ✅ |
| 0.05 | 0.948 | +0.018 | 723.4 | -3.7% | 1.13 | 1.00 | ✅ |
| 0.1 | 0.943 | +0.013 | 692.1 | -7.9% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.913 | -0.017 | 609.0 | -18.9% | 1.14 | 1.00 | ✅ |
| 0.5 | 0.897 | -0.033 | 476.3 | -36.6% | 1.14 | 1.00 | ❌ VERLETZT |
| 1.0 | 0.738 | -0.192 | 249.2 | -66.8% | 1.17 | 1.00 | ❌ VERLETZT |

### M4

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.938 | +0.008 | 743.6 | -1.0% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.932 | +0.002 | 714.8 | -4.8% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 677.1 | -9.9% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.850 | -0.080 | 530.5 | -29.4% | 1.17 | 1.00 | ❌ VERLETZT |
| 0.5 | 0.738 | -0.192 | 326.6 | -56.5% | 1.25 | 1.00 | ❌ VERLETZT |
| 1.0 | 0.068 | -0.862 | 3.9 | -99.5% | 1.00 | 1.00 | ❌ VERLETZT |

### M5

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.01 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.05 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.25 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.5 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |

### M7_12

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.927 | -0.003 | 752.0 | +0.1% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.950 | +0.020 | 753.6 | +0.3% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.928 | -0.002 | 746.1 | -0.7% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.938 | +0.008 | 742.6 | -1.1% | 1.11 | 1.00 | ✅ |
| 0.25 | 0.913 | -0.017 | 719.2 | -4.3% | 1.12 | 1.00 | ✅ |
| 0.5 | 0.910 | -0.020 | 702.1 | -6.5% | 1.12 | 1.00 | ✅ |
| 1.0 | 0.863 | -0.067 | 669.5 | -10.9% | 1.12 | 1.00 | ❌ VERLETZT |

### M7_15

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.952 | +0.022 | 752.9 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.943 | +0.013 | 752.4 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.940 | +0.010 | 752.8 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.952 | +0.022 | 749.8 | -0.2% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.943 | +0.013 | 743.1 | -1.1% | 1.11 | 1.00 | ✅ |
| 0.5 | 0.923 | -0.007 | 736.3 | -2.0% | 1.12 | 1.00 | ✅ |
| 1.0 | 0.910 | -0.020 | 723.9 | -3.6% | 1.12 | 1.00 | ✅ |

### COMBI

| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | Routen-Stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1node | 0.927 | -0.003 | 753.5 | +0.3% | 1.00 | 1.00 | ✅ |
| 0.01 | 0.940 | +0.010 | 747.0 | -0.6% | 1.00 | 1.00 | ✅ |
| 0.05 | 0.932 | +0.002 | 722.8 | -3.8% | 1.08 | 1.00 | ✅ |
| 0.1 | 0.943 | +0.013 | 687.9 | -8.4% | 1.00 | 1.00 | ✅ |
| 0.25 | 0.888 | -0.042 | 587.0 | -21.9% | 1.11 | 1.00 | ❌ VERLETZT |
| 0.5 | 0.887 | -0.043 | 445.7 | -40.7% | 1.12 | 1.00 | ❌ VERLETZT |
| 1.0 | 0.683 | -0.247 | 223.0 | -70.3% | 1.12 | 1.00 | ❌ VERLETZT |

## Safety-Befund (alle Rollouts)

Folgende (Mechanismus, α, Rollout) verletzen die Invariante:

| Mechanismus | α | Rollout | Lieferquote (Base 0.930) | Airtime (Base 751.2) | Grund |
|---|---|---|---|---|---|
| M2k2 | 0.5 | random | 0.832 | 384.3 | Lieferquote<Baseline |
| M2k2 | 1.0 | random | 0.795 | 115.2 | Lieferquote<Baseline |
| M2k2 | 0.25 | top_traffic | 0.898 | 567.5 | Lieferquote<Baseline |
| M2k2 | 0.5 | top_traffic | 0.867 | 389.6 | Lieferquote<Baseline |
| M2k2 | 1.0 | top_traffic | 0.795 | 115.2 | Lieferquote<Baseline |
| M2k3 | 0.5 | random | 0.872 | 417.4 | Lieferquote<Baseline |
| M2k3 | 1.0 | random | 0.867 | 169.3 | Lieferquote<Baseline |
| M2k3 | 0.5 | top_traffic | 0.895 | 419.1 | Lieferquote<Baseline |
| M2k3 | 1.0 | top_traffic | 0.867 | 169.3 | Lieferquote<Baseline |
| M3 | 0.5 | random | 0.880 | 465.5 | Lieferquote<Baseline |
| M3 | 1.0 | random | 0.738 | 249.2 | Lieferquote<Baseline |
| M3 | 0.5 | top_traffic | 0.897 | 476.3 | Lieferquote<Baseline |
| M3 | 1.0 | top_traffic | 0.738 | 249.2 | Lieferquote<Baseline |
| M4 | 0.05 | random | 0.898 | 710.5 | Lieferquote<Baseline |
| M4 | 0.1 | random | 0.900 | 668.0 | Lieferquote<Baseline |
| M4 | 0.25 | random | 0.880 | 558.0 | Lieferquote<Baseline |
| M4 | 0.5 | random | 0.817 | 370.2 | Lieferquote<Baseline |
| M4 | 1.0 | random | 0.068 | 3.9 | Lieferquote<Baseline |
| M4 | 0.25 | top_traffic | 0.850 | 530.5 | Lieferquote<Baseline |
| M4 | 0.5 | top_traffic | 0.738 | 326.6 | Lieferquote<Baseline |
| M4 | 1.0 | top_traffic | 0.068 | 3.9 | Lieferquote<Baseline |
| M7_12 | 1.0 | random | 0.863 | 669.5 | Lieferquote<Baseline |
| M7_12 | 1.0 | top_traffic | 0.863 | 669.5 | Lieferquote<Baseline |
| COMBI | 0.1 | random | 0.898 | 681.0 | Lieferquote<Baseline |
| COMBI | 0.5 | random | 0.867 | 450.3 | Lieferquote<Baseline |
| COMBI | 1.0 | random | 0.683 | 223.0 | Lieferquote<Baseline |
| COMBI | 0.25 | top_traffic | 0.888 | 587.0 | Lieferquote<Baseline |
| COMBI | 0.5 | top_traffic | 0.887 | 445.7 | Lieferquote<Baseline |
| COMBI | 1.0 | top_traffic | 0.683 | 223.0 | Lieferquote<Baseline |

## Ranking (Airtime-Gewinn bei gehaltener Lieferquote, α=1.0 Top-Traffic)

| Rang | Mechanismus | ΔAirtime% @α=1.0 | Lieferquote | Safe@1.0 | Detour med |
|---|---|---|---|---|---|
| 1 | M4 | -99.5% | 0.068 | ❌ | 1.00 |
| 2 | M2k2 | -84.7% | 0.795 | ❌ | 1.50 |
| 3 | M2k3 | -77.5% | 0.867 | ❌ | 1.25 |
| 4 | COMBI | -70.3% | 0.683 | ❌ | 1.12 |
| 5 | M3 | -66.8% | 0.738 | ❌ | 1.17 |
| 6 | M7_12 | -10.9% | 0.863 | ❌ | 1.12 |
| 7 | M7_15 | -3.6% | 0.910 | ✅ | 1.12 |
| 8 | M5 | +0.0% | 0.930 | ✅ | 1.00 |
| 9 | M1 | +0.3% | 0.940 | ✅ | 1.00 |

## Adoptionsschwelle (erstes α mit ≥2% Airtime-Senkung & safe, Top-Traffic)

| Mechanismus | Schwelle α | ΔAirtime% dort |
|---|---|---|
| M1 | — (keine ≥2%-Senkung) | — |
| M2k2 | 0.05 | -4.7% |
| M2k3 | 0.05 | -4.8% |
| M3 | 0.05 | -3.7% |
| M4 | 0.05 | -4.8% |
| M5 | — (keine ≥2%-Senkung) | — |
| M7_12 | 0.25 | -4.3% |
| M7_15 | 1.0 | -3.6% |
| COMBI | 0.05 | -3.8% |

## Kombi M3+M5+M7 (COMBI)

- α=1node: Lieferquote 0.927 (-0.003), Airtime 753.5 (+0.3%), Detour-Median 1.00, Safety OK.
- α=0.1: Lieferquote 0.943 (+0.013), Airtime 687.9 (-8.4%), Detour-Median 1.00, Safety OK.
- α=0.25: Lieferquote 0.888 (-0.042), Airtime 587.0 (-21.9%), Detour-Median 1.11, Safety VERLETZT.
- α=1.0: Lieferquote 0.683 (-0.247), Airtime 223.0 (-70.3%), Detour-Median 1.12, Safety VERLETZT.

## Stress-Befund

### Churn (20% instabile Knoten nach advert_count) & Linkausfall (α=1.0)

| Mechanismus | Szenario | Lieferquote | Airtime | Routen-Stab. |
|---|---|---|---|---|
| M0 | linkfail_10 | 0.907 | 742.4 | 1.00 |
| M0 | linkfail_20 | 0.908 | 740.6 | 1.00 |
| M0 | churn_20 | 0.874 | 574.0 | 1.00 |
| M3 | linkfail_10 | 0.775 | 255.3 | 1.00 |
| M3 | linkfail_20 | 0.787 | 259.4 | 1.00 |
| M3 | churn_20 | 0.719 | 210.9 | 1.00 |
| M4 | linkfail_10 | 0.057 | 3.7 | 1.00 |
| M4 | linkfail_20 | 0.048 | 3.0 | 1.00 |
| M4 | churn_20 | 0.060 | 3.3 | 1.00 |
| M7_12 | linkfail_10 | 0.850 | 666.6 | 1.00 |
| M7_12 | linkfail_20 | 0.838 | 658.2 | 1.00 |
| M7_12 | churn_20 | 0.843 | 530.0 | 1.00 |
| COMBI | linkfail_10 | 0.718 | 227.6 | 1.00 |
| COMBI | linkfail_20 | 0.723 | 235.1 | 1.00 |
| COMBI | churn_20 | 0.622 | 185.8 | 1.00 |

### M6 (passives Topologie-Lernen + Feasible-Successor): eingesparte Re-Discovery-Airtime bei Linkausfall

| Szenario | gebrochene Paare | Baseline-Reflood Ø | eingespart % | lokale Backup-Recovery |
|---|---|---|---|---|
| m6_linkfail_10 | 152 | 671.0 | 94% | 0.88 |
| m6_linkfail_20 | 224 | 687.9 | 96% | 0.92 |

## Limitierungen (ehrlich)

- **Linkmodell** ist geometrisch (Log-Distance + PLE-Floor 2.0); reales Gelände/Antennenhöhe nicht abgebildet. Der reale SNR/Distanz-Zusammenhang ist schwach (|corr|≈0.42), darum ist Hop-Zahl der verlässlichere Hebel — das Modell respektiert das, ist aber eine bewusste Vereinfachung.
- Quervalidierung gegen beobachtete Kanten ist nur teilweise deckend: das geometrische Modell und die beobachtete Flood-Stichprobe überlappen nur begrenzt (siehe Topologie-Abschnitt). Absolute Airtime-Zahlen sind daher modellabhängig; die **relativen** Mechanismus-Vergleiche sind robuster.
- Timing-Jitter/Backoff-Fenster sind modelliert, nicht aus Hardware gemessen. M1/M3 hängen vom Timing-Modell ab.
- M2 (counter-based) hört Kopien im selben diskreten Flood; ein reales kontinuierliches Backoff-Fenster ist gröber approximiert.
- M5 ändert nur den gecachten Pfad (Detour), nicht die Flood-Airtime — so modelliert und so berichtet.
- M6 ist als Airtime-Einsparungs-Modell (Backup statt Reflood) gerechnet, nicht als vollständige DV-Protokoll-Simulation.
- Stichprobengröße: 5 Seeds × 120 Paare; Konfidenzintervalle nicht ausgewiesen.

---
## 🇬🇧 English Translation

# Study Results: Routing Mechanisms × Adoption (MeshCore, Real-World Data)

Generated by `study_sim.py`. **MEASURED** (real topology/calibration from `../sim/data/`) is strictly separated from **SIMULATED** (flood-routing model + mechanisms + sweep, this script).

## Topology (MEASURED/derived, as in v3)

- Active repeater subgraph: **814** nodes (role=repeater, Geo, relay-active/24h>0 OR actually seen as a path hop — reproduces v3's ~831-node subgraph).
- Range link graph: **814** nodes, **9275** edges; largest component **776** nodes / 9226 edges (this is where simulation runs).
- Greedy relay set (M4, CDS): **49** nodes (6.3%).
- **Cross-validation** against observed edges (`topology_edges.json`): 3459 observed edges, 3306 between active nodes, of which 1380 (41.7%) reproduced by the geometric range model.
- Real detour median reference (MEASURED): **2.11×**.

## Model Assumptions (SIMULATED) — honestly separated from measurement

- Flood timing-driven (PQ by arrival time), stock nodes: first-packet-wins + random jitter. **Airtime = number of actually transmitting nodes per delivered message.**
- Link stochastics: one transmission reaches neighbor v with P=link reliability (Monte-Carlo over seed). Reliability of the used path = product of link reliabilities.
- Mechanisms are **local rules** of new-firmware nodes; stock nodes flood unchanged (built-in safety net).
- **Baseline (M0, α=0):** Delivery rate **0.930**, Airtime **751.2** transmission events/delivery, Detour median 1.11.

- Configuration: 5 seeds, 120 pairs/seed, MC=1; runtime 763.5s.

## Safety Invariant

For every (mechanism, α): **Delivery rate ≥ Baseline AND Airtime ≤ Baseline.** Violation ⇒ mechanism disqualified at this α.

**Monte-Carlo noise band:** Mechanisms that do not suppress any senders (M1/M5/M7-high) have the same *true* airtime/delivery rate as the baseline; measured micro-deviations are pure sampling noise (different RNG draws per α/seed). A violation is only counted when falling outside ±2·standard error of the baseline or an absolute minimum tolerance (delivery rate ±0.024, airtime ±6.2 = 0.82%). The *Safety* column uses this band; *strict* = without band.

## Result Tables per Mechanism (Rollout: Top-Traffic)

### M0

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.05 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.25 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.5 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |

### M1

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.935 | +0.005 | 752.8 | +0.2% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.935 | +0.005 | 752.1 | +0.1% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.938 | +0.008 | 753.6 | +0.3% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.940 | +0.010 | 753.9 | +0.4% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.933 | +0.003 | 752.0 | +0.1% | 1.10 | 1.00 | ✅ |
| 0.5 | 0.948 | +0.018 | 753.9 | +0.4% | 1.00 | 1.00 | ✅ |
| 1.0 | 0.940 | +0.010 | 753.4 | +0.3% | 1.00 | 1.00 | ✅ |

### M2k2

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.947 | +0.017 | 749.7 | -0.2% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.945 | +0.015 | 746.4 | -0.6% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.933 | +0.003 | 716.2 | -4.7% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.932 | +0.002 | 676.3 | -10.0% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.898 | -0.032 | 567.5 | -24.5% | 1.17 | 1.00 | ❌ VIOLATED |
| 0.5 | 0.867 | -0.063 | 389.6 | -48.1% | 1.22 | 1.00 | ❌ VIOLATED |
| 1.0 | 0.795 | -0.135 | 115.2 | -84.7% | 1.50 | 1.00 | ❌ VIOLATED |

### M2k3

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.935 | +0.005 | 749.3 | -0.3% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.940 | +0.010 | 744.7 | -0.9% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.927 | -0.003 | 714.9 | -4.8% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.927 | -0.003 | 680.4 | -9.4% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.910 | -0.020 | 577.7 | -23.1% | 1.14 | 1.00 | ✅ |
| 0.5 | 0.895 | -0.035 | 419.1 | -44.2% | 1.20 | 1.00 | ❌ VIOLATED |
| 1.0 | 0.867 | -0.063 | 169.3 | -77.5% | 1.25 | 1.00 | ❌ VIOLATED |

### M3

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.942 | +0.012 | 750.2 | -0.1% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.932 | +0.002 | 748.0 | -0.4% | 1.14 | 1.00 | ✅ |
| 0.05 | 0.948 | +0.018 | 723.4 | -3.7% | 1.13 | 1.00 | ✅ |
| 0.1 | 0.943 | +0.013 | 692.1 | -7.9% | 1.14 | 1.00 | ✅ |
| 0.25 | 0.913 | -0.017 | 609.0 | -18.9% | 1.14 | 1.00 | ✅ |
| 0.5 | 0.897 | -0.033 | 476.3 | -36.6% | 1.14 | 1.00 | ❌ VIOLATED |
| 1.0 | 0.738 | -0.192 | 249.2 | -66.8% | 1.17 | 1.00 | ❌ VIOLATED |

### M4

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 0.01 | 0.938 | +0.008 | 743.6 | -1.0% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.932 | +0.002 | 714.8 | -4.8% | 1.14 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 677.1 | -9.9% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.850 | -0.080 | 530.5 | -29.4% | 1.17 | 1.00 | ❌ VIOLATED |
| 0.5 | 0.738 | -0.192 | 326.6 | -56.5% | 1.25 | 1.00 | ❌ VIOLATED |
| 1.0 | 0.068 | -0.862 | 3.9 | -99.5% | 1.00 | 1.00 | ❌ VIOLATED |

### M5

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1node | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.01 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.05 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.1 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.25 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 0.5 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |

### M7_12

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.927 | -0.003 | 752.0 | +0.1% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.950 | +0.020 | 753.6 | +0.3% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.928 | -0.002 | 746.1 | -0.7% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.938 | +0.008 | 742.6 | -1.1% | 1.11 | 1.00 | ✅ |
| 0.25 | 0.913 | -0.017 | 719.2 | -4.3% | 1.12 | 1.00 | ✅ |
| 0.5 | 0.910 | -0.020 | 702.1 | -6.5% | 1.12 | 1.00 | ✅ |
| 1.0 | 0.863 | -0.067 | 669.5 | -10.9% | 1.12 | 1.00 | ❌ VIOLATED |

### M7_15

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.11 | 1.00 | ✅ |
| 1node | 0.952 | +0.022 | 752.9 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.01 | 0.943 | +0.013 | 752.4 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.05 | 0.940 | +0.010 | 752.8 | +0.2% | 1.12 | 1.00 | ✅ |
| 0.1 | 0.952 | +0.022 | 749.8 | -0.2% | 1.12 | 1.00 | ✅ |
| 0.25 | 0.943 | +0.013 | 743.1 | -1.1% | 1.11 | 1.00 | ✅ |
| 0.5 | 0.923 | -0.007 | 736.3 | -2.0% | 1.12 | 1.00 | ✅ |
| 1.0 | 0.910 | -0.020 | 723.9 | -3.6% | 1.12 | 1.00 | ✅ |

### COMBI

| α | Delivery rate | ΔDeliv | Airtime | ΔAirtime% | Detour med | Route stab. | Safety |
|---|---|---|---|---|---|---|---|
| 0.0 | 0.930 | +0.000 | 751.2 | +0.0% | 1.00 | 1.00 | ✅ |
| 1node | 0.927 | -0.003 | 753.5 | +0.3% | 1.00 | 1.00 | ✅ |
| 0.01 | 0.940 | +0.010 | 747.0 | -0.6% | 1.00 | 1.00 | ✅ |
| 0.05 | 0.932 | +0.002 | 722.8 | -3.8% | 1.08 | 1.00 | ✅ |
| 0.1 | 0.943 | +0.013 | 687.9 | -8.4% | 1.00 | 1.00 | ✅ |
| 0.25 | 0.888 | -0.042 | 587.0 | -21.9% | 1.11 | 1.00 | ❌ VIOLATED |
| 0.5 | 0.887 | -0.043 | 445.7 | -40.7% | 1.12 | 1.00 | ❌ VIOLATED |
| 1.0 | 0.683 | -0.247 | 223.0 | -70.3% | 1.12 | 1.00 | ❌ VIOLATED |

## Safety Findings (all rollouts)

The following (mechanism, α, rollout) combinations violate the invariant:

| Mechanism | α | Rollout | Delivery rate (Base 0.930) | Airtime (Base 751.2) | Reason |
|---|---|---|---|---|---|
| M2k2 | 0.5 | random | 0.832 | 384.3 | Delivery rate<Baseline |
| M2k2 | 1.0 | random | 0.795 | 115.2 | Delivery rate<Baseline |
| M2k2 | 0.25 | top_traffic | 0.898 | 567.5 | Delivery rate<Baseline |
| M2k2 | 0.5 | top_traffic | 0.867 | 389.6 | Delivery rate<Baseline |
| M2k2 | 1.0 | top_traffic | 0.795 | 115.2 | Delivery rate<Baseline |
| M2k3 | 0.5 | random | 0.872 | 417.4 | Delivery rate<Baseline |
| M2k3 | 1.0 | random | 0.867 | 169.3 | Delivery rate<Baseline |
| M2k3 | 0.5 | top_traffic | 0.895 | 419.1 | Delivery rate<Baseline |
| M2k3 | 1.0 | top_traffic | 0.867 | 169.3 | Delivery rate<Baseline |
| M3 | 0.5 | random | 0.880 | 465.5 | Delivery rate<Baseline |
| M3 | 1.0 | random | 0.738 | 249.2 | Delivery rate<Baseline |
| M3 | 0.5 | top_traffic | 0.897 | 476.3 | Delivery rate<Baseline |
| M3 | 1.0 | top_traffic | 0.738 | 249.2 | Delivery rate<Baseline |
| M4 | 0.05 | random | 0.898 | 710.5 | Delivery rate<Baseline |
| M4 | 0.1 | random | 0.900 | 668.0 | Delivery rate<Baseline |
| M4 | 0.25 | random | 0.880 | 558.0 | Delivery rate<Baseline |
| M4 | 0.5 | random | 0.817 | 370.2 | Delivery rate<Baseline |
| M4 | 1.0 | random | 0.068 | 3.9 | Delivery rate<Baseline |
| M4 | 0.25 | top_traffic | 0.850 | 530.5 | Delivery rate<Baseline |
| M4 | 0.5 | top_traffic | 0.738 | 326.6 | Delivery rate<Baseline |
| M4 | 1.0 | top_traffic | 0.068 | 3.9 | Delivery rate<Baseline |
| M7_12 | 1.0 | random | 0.863 | 669.5 | Delivery rate<Baseline |
| M7_12 | 1.0 | top_traffic | 0.863 | 669.5 | Delivery rate<Baseline |
| COMBI | 0.1 | random | 0.898 | 681.0 | Delivery rate<Baseline |
| COMBI | 0.5 | random | 0.867 | 450.3 | Delivery rate<Baseline |
| COMBI | 1.0 | random | 0.683 | 223.0 | Delivery rate<Baseline |
| COMBI | 0.25 | top_traffic | 0.888 | 587.0 | Delivery rate<Baseline |
| COMBI | 0.5 | top_traffic | 0.887 | 445.7 | Delivery rate<Baseline |
| COMBI | 1.0 | top_traffic | 0.683 | 223.0 | Delivery rate<Baseline |

## Ranking (Airtime gain while maintaining delivery rate, α=1.0 top-traffic)

| Rank | Mechanism | ΔAirtime% @α=1.0 | Delivery rate | Safe@1.0 | Detour med |
|---|---|---|---|---|---|
| 1 | M4 | -99.5% | 0.068 | ❌ | 1.00 |
| 2 | M2k2 | -84.7% | 0.795 | ❌ | 1.50 |
| 3 | M2k3 | -77.5% | 0.867 | ❌ | 1.25 |
| 4 | COMBI | -70.3% | 0.683 | ❌ | 1.12 |
| 5 | M3 | -66.8% | 0.738 | ❌ | 1.17 |
| 6 | M7_12 | -10.9% | 0.863 | ❌ | 1.12 |
| 7 | M7_15 | -3.6% | 0.910 | ✅ | 1.12 |
| 8 | M5 | +0.0% | 0.930 | ✅ | 1.00 |
| 9 | M1 | +0.3% | 0.940 | ✅ | 1.00 |

## Adoption Threshold (first α with ≥2% airtime reduction & safe, top-traffic)

| Mechanism | Threshold α | ΔAirtime% there |
|---|---|---|
| M1 | — (no ≥2% reduction) | — |
| M2k2 | 0.05 | -4.7% |
| M2k3 | 0.05 | -4.8% |
| M3 | 0.05 | -3.7% |
| M4 | 0.05 | -4.8% |
| M5 | — (no ≥2% reduction) | — |
| M7_12 | 0.25 | -4.3% |
| M7_15 | 1.0 | -3.6% |
| COMBI | 0.05 | -3.8% |

## Combo M3+M5+M7 (COMBI)

- α=1node: Delivery rate 0.927 (-0.003), Airtime 753.5 (+0.3%), Detour median 1.00, Safety OK.
- α=0.1: Delivery rate 0.943 (+0.013), Airtime 687.9 (-8.4%), Detour median 1.00, Safety OK.
- α=0.25: Delivery rate 0.888 (-0.042), Airtime 587.0 (-21.9%), Detour median 1.11, Safety VIOLATED.
- α=1.0: Delivery rate 0.683 (-0.247), Airtime 223.0 (-70.3%), Detour median 1.12, Safety VIOLATED.

## Stress Findings

### Churn (20% unstable nodes by advert_count) & Link failure (α=1.0)

| Mechanism | Scenario | Delivery rate | Airtime | Route stab. |
|---|---|---|---|---|
| M0 | linkfail_10 | 0.907 | 742.4 | 1.00 |
| M0 | linkfail_20 | 0.908 | 740.6 | 1.00 |
| M0 | churn_20 | 0.874 | 574.0 | 1.00 |
| M3 | linkfail_10 | 0.775 | 255.3 | 1.00 |
| M3 | linkfail_20 | 0.787 | 259.4 | 1.00 |
| M3 | churn_20 | 0.719 | 210.9 | 1.00 |
| M4 | linkfail_10 | 0.057 | 3.7 | 1.00 |
| M4 | linkfail_20 | 0.048 | 3.0 | 1.00 |
| M4 | churn_20 | 0.060 | 3.3 | 1.00 |
| M7_12 | linkfail_10 | 0.850 | 666.6 | 1.00 |
| M7_12 | linkfail_20 | 0.838 | 658.2 | 1.00 |
| M7_12 | churn_20 | 0.843 | 530.0 | 1.00 |
| COMBI | linkfail_10 | 0.718 | 227.6 | 1.00 |
| COMBI | linkfail_20 | 0.723 | 235.1 | 1.00 |
| COMBI | churn_20 | 0.622 | 185.8 | 1.00 |

### M6 (passive topology learning + feasible successor): saved re-discovery airtime on link failure

| Scenario | broken pairs | Baseline reflood avg | saved % | local backup recovery |
|---|---|---|---|---|
| m6_linkfail_10 | 152 | 671.0 | 94% | 0.88 |
| m6_linkfail_20 | 224 | 687.9 | 96% | 0.92 |

## Limitations (honest)

- **Link model** is geometric (log-distance + PLE floor 2.0); real terrain/antenna height not modeled. The real SNR/distance relationship is weak (|corr|≈0.42), so hop count is the more reliable lever — the model respects this, but it is a deliberate simplification.
- Cross-validation against observed edges is only partially covering: the geometric model and the observed flood sample overlap only to a limited extent (see topology section). Absolute airtime numbers are therefore model-dependent; the **relative** mechanism comparisons are more robust.
- Timing jitter/backoff windows are modeled, not measured from hardware. M1/M3 depend on the timing model.
- M2 (counter-based) hears copies in the same discrete flood; a real continuous backoff window is more coarsely approximated.
- M5 only changes the cached path (detour), not the flood airtime — modeled and reported as such.
- M6 is computed as an airtime-savings model (backup instead of reflood), not as a full DV-protocol simulation.
- Sample size: 5 seeds × 120 pairs; confidence intervals not reported.
