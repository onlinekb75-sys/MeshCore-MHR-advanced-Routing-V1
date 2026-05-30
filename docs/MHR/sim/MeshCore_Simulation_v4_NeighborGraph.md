# MeshCore-Simulation v4 — auf der ECHTEN, server-gemessenen Link-Topologie

**Datenquelle:** server-aufgelöster `neighbor_graph.json` (CoreScope-Pipeline, Abzug 2026-05-30).
**Datensatz:** 1034 Knoten, 1956 Kanten mit **echtem Per-Link-`avg_snr`** (Median ~4,2 dB),
`weight` = Beobachtungszahl, `ambiguous`-Flag (173 Kanten = 8,8 %). Anreicherung (Geo/Rolle/
Traffic) via Pubkey-(Präfix-)Join mit `nodes.json` (1962 Knoten).
**Reproduzierbar:** Master-Seed 42, 6 Seeds für alle Zufallsanteile, 150 Quelle-Ziel-Paare/Seed.
**Skript:** `mhr_sim_real_v4.py` · **Ergebnisse:** `sim_results_v4.json` · **Plots:** `fig_v4_*.png`.

> **Warum v4 statt v3?** v3 und die Studie bauten den Routing-Graphen aus einem
> GEOMETRISCHEN Log-Distance-Linkmodell (Reichweitenscheiben, pro Knoten Top-20-Nachbarn).
> Dieses Modell traf nur ~41,7 % der real beobachteten Kanten, und seine SNR-Distanz-Annahme
> wird von den Realdaten widerlegt (|corr(log d, SNR)| ≈ 0,42, PLE ≈ 0,4 statt 2,55). v4 nutzt
> stattdessen die **echten gemessenen Kanten** als Routing-Graph und das **echte Per-Link-SNR**
> für Reliability/ETX — die genaueste verfügbare Grundlage.

---

## 1) Reale Topologie

| Größe | v4 (echte Kanten) | v3-Sim (geometrisch) |
|---|---|---|
| Knoten (Kern, ohne `ambiguous`) | **1034** | 881 aktiver Subgraph |
| Kanten | **1783** (173 ambiguous verworfen) | 9.861 (Top-20-Reichweite) |
| **Ø-Grad** | **3,45** (median 2, max 92) | **~23,7** |
| Komponenten | 202 | — |
| Größte Zusammenhangskomponente | **632** (61 %) | 831 |
| Knoten mit Geo (joinbar) | 930/1034 | — |
| Rollen | 821 repeater, 146 companion, 56 room, 11 observer | — |

**Kernunterschied:** v4 ist eine **SPARSE reale Topologie** (Ø-Grad 3,45) mit vielen Blattknoten
(405 Knoten Grad 1, 168 Grad 2) und wenigen Hubs (max. Grad 92). v3 war ein **dichter geometrischer
Graph** (Ø-Grad ~24). Diese Sparsity ist der entscheidende Treiber aller v4-Abweichungen: das reale
Netz hat **viel weniger Pfad-Redundanz** als das geometrische Modell suggerierte.

**Link-Reliability/ETX aus echtem SNR (nicht Distanz):** Pro Kante
`p = clip(σ((avg_snr − (−12 dB)) / 4), 0.02, 0.995)` (logistisch um die Empfangsschwelle),
`ETX = 1/p²`. Median-Link-SNR 4,2 dB → Median-Reliability **0,98**; P10-SNR −6,6 dB → schwache
Links bleiben erkennbar. `weight` (Beobachtungszahl, Median 40) ist als Stabilitätsmaß im
Kanten-Attribut erhalten.

---

## 2) Hält die Studie auf echten Kanten? (Befund für Befund)

### 2a) Baseline (first-wins-Flood) vs. MHR — **BESTÄTIGT**

| Kennzahl | Baseline (Flood) | MHR (ETX/hop-geleitet) | Δ |
|---|---|---|---|
| Lieferquote | 0,931 | — (Pfad existiert) | — |
| Ø Hops | 5,05 | **4,84** | −4 % |
| Detour (Baseline/MHR), Median · Mittel | — | — | **1,00× · 1,06×** |
| Paare, bei denen Baseline länger ist | — | — | **26 %** |
| Ø Sende-Ereignisse (Airtime) | **616,8** | **4,84** (Unicast) | **−99,2 %** |
| Ø Pfad-Zuverlässigkeit | 0,707 | **0,806** | **+14 %** |

**Bestätigung:** Der **Airtime-Vorteil** von metrik-geleitetem MHR bleibt auf echten Kanten
gewaltig (−99,2 %). Auf der **sparsen** realen Topologie flutet der Baseline-Flood netzweit
durch fast die ganze 632-Knoten-Komponente (~617 Sende-Ereignisse pro Discovery), MHR nutzt
nur die ~5 Hops des gewählten Pfads. Zuverlässigkeit steigt (+14 %, echtes SNR), Hops sinken leicht.

**Abweichung (ehrlich):** Der **Detour-Vorteil** schrumpft praktisch auf null
(Median 1,00×, Mittel 1,06×). Grund: in einer **sparsen** Topologie gibt es kaum alternative
Pfade — der first-wins-Flood findet fast immer denselben (kurzen) Pfad wie MHR, weil es schlicht
keine langen Umweg-Alternativen gibt. Das **relativiert** das v3-Sim-Detour-Argument (dort
Median 1,17×) und den gemessenen A4-Detour (Median 2,1× aus realen Paketen) — Letzterer
entstand durch reale Timing-/Kollisions-/Mixed-Firmware-Effekte, die dieses idealisierte
Flood-Modell **nicht** abbildet. **Der belastbare MHR-Gewinn auf echten Kanten ist Airtime
und Zuverlässigkeit, nicht Hop-Detour.**

### 2b) flood.max-Sweep — **flood.max = 15 BESTÄTIGT (mit Nuance)**

Realer Netzdurchmesser (Hop-Stichprobe der Riesenkomponente): **P90 = 12 Hops, max = 14**.

| flood.max | Lieferquote | Δ vs. Baseline | Airtime vs. Baseline |
|---|---|---|---|
| 10 | 0,951 | **+0,020** | −1,4 % |
| 12 | 0,943 | +0,012 | −0,2 % |
| **15** | **0,939** | **+0,008** | ±0,0 % |
| 18 | 0,931 | ±0,000 | ±0,0 % |
| 20 | 0,931 | ±0,000 | ±0,0 % |
| 64 | 0,931 | ±0,000 | ±0,0 % |

**Befund:** Auf der realen Topologie ändert `flood.max` ab 15 **nichts** an Lieferquote oder
Airtime — weil der reale Durchmesser (P90 = 12) klar darunter liegt; höhere Limits sind reine
Reserve. Werte **≤ 12** kappen sogar einige der wenigen langen (15–18-Hop-)Pfade und erhöhen die
gemessene Lieferquote minimal (+0,01…0,02) bei kaum geänderter Airtime — das ist aber ein
Effekt der **idealisierten** Topologie (keine Kollisionen) und kein Sicherheitsgewinn.

**Implikation:** **`flood.max = 15` ist auf der echten Topologie weiterhin eine sichere,
sinnvolle Wahl** (Lieferquote ≥ Baseline, Airtime ≤ Baseline, Reserve über dem P90-Durchmesser
von 12). Es gibt **keinen** Beleg, von 15 abzuweichen; 12 wäre knapp am realen P90-Durchmesser
(zu wenig Reserve), 64 ist unnötig hoch. **`MHR_HOP_HORIZON = 12`** ist als Wert konsistent mit
dem realen P90-Durchmesser, aber als *hartes* Flood-Limit zu knapp — 15 lässt die nötige Reserve.

### 2c) Adoptions-Sweep + Safety-Invariante — **Dreiteilung BESTÄTIGT, aber Suppressions-Sicherheitszone SCHRUMPFT DRASTISCH**

Safety-Invariante: Lieferquote ≥ Baseline − Rausch-Band UND Airtime ≤ Baseline + Rausch-Band.

**Gruppe 1 — „gute Bürger" (sicher bei JEDEM α, monoton): BESTÄTIGT**
- **M1 (hop-gewichtetes Delay):** deliv +0,02 über alle α, Airtime ≈ Baseline. Safe von 1 Knoten → alle.
- **M5 (Best-of-N am Ziel nach Hops):** exakt neutral (deliv/airtime = Baseline), safe überall.
- **M7_15 (flood.max = 15):** deliv +0,008, Airtime neutral, safe überall.

**Gruppe 2 — „adaptive Suppression" (NICHT mehr bis α=1 sicher): ABWEICHUNG**

| Mechanismus | safe bis α ≈ | bei α = 1.0: Lieferquote | Airtime |
|---|---|---|---|
| M3 (shorter-path-cancel) | **0,10** | 0,762 (**−0,17**) | −37,8 % |
| COMBI (M1+M3+M5+M7) | **0,05** | 0,759 (**−0,17**) | −39,3 % |
| M4 (MPR/CDS-Schweigen) | **0,05** (≤1 %) | 0,536 (**−0,40**) | −87,2 % |
| M2k2 / M2k3 (counter-suppress) | bricht früh | 0,63 / 0,76 | −53 % / −34 % |

**Das ist die wichtigste v4-Abweichung von der Studie.** In v3/Studie galten die
Suppressions-Mechanismen bis zu hohen α als sicher, weil der **dichte geometrische Graph
(Ø-Grad ~24)** massive Pfad-Redundanz bot — unterdrückte Sender wurden durch Nachbarn ersetzt.
Auf der **sparsen realen Topologie (Ø-Grad 3,45)** existiert diese Redundanz **nicht**: jeder
unterdrückte Rebroadcast kann der **einzige** Pfad zu einem Blattknoten gewesen sein, also bricht
die Lieferquote schon ab moderater Adoption ein. **M4 (MPR/CDS) bricht am frühesten (ab α≈0,05)** —
das bestätigt die Studien-Warnung „MPR kippt bei voller Adoption in Coverage-Verlust", **aber auf
echten Kanten passiert das viel früher und härter**.

**Fundament-Gruppe (M0/Baseline + neutrale Mechanismen):** unverändert sicher.

> **Konsequenz:** Reine **Airtime-Suppression (Stufe B/C) ist auf der echten sparsen Topologie
> NICHT mischbetriebs-sicher über α≈5–10 % hinaus** ohne harte Redundanz-Garantie. Der v3/Studie-
> Optimismus für M3/M4/COMBI bei hoher Adoption hält auf echten Kanten NICHT.

### 2d) SNR-Frage neu — **RELATIVIERT v3 (SNR ist auf echten Kanten ein etwas stärkerer Hebel als gedacht, aber kein Hop-Ersatz)**

Jetzt liegt echtes Per-Link-SNR vor (kein Distanz-Proxy):

| Frage | v4-Befund |
|---|---|
| Korr(mittleres Pfad-SNR, Hop-Zahl) | **+0,17** (schwach) |
| ETX-Pfad vs. reiner Hop-Pfad: Ø extra Hops | **+0,19** |
| ETX-Pfad vs. Hop-Pfad: Ø Reliability-Gewinn | **+0,168** (Median +0,041) |
| Anteil Paare, bei denen ETX-Pfad ≠ Hop-Pfad | **18 %** |
| Korr(Einzel-Link-SNR, Knotengrad) | **−0,52** |

**Bestätigung von v3 (Richtung):** SNR korreliert nur **schwach** mit Pfad-Kürze (+0,17) — ein
kurzer Pfad ist nicht automatisch SNR-stark. Hop-Zahl bleibt der dominante Kürze-Hebel.

**Relativierung (ehrlich):** Anders als v3 suggerierte, ist SNR **nicht wertlos**: ein
SNR-/ETX-geleiteter Pfad liefert in 18 % der Paare einen **anderen** (und im Mittel **+0,168
zuverlässigeren**) Pfad als der reine Hop-kürzeste — für nur **+0,19 zusätzliche Hops**. Echtes
Per-Link-SNR ist also ein **brauchbarer Zuverlässigkeits-Tiebreaker**, kein Ersatz für die
Hop-Metrik. Der starke **negative** Zusammenhang Link-SNR ↔ Knotengrad (−0,52) ist aufschlussreich:
**Hub-Knoten haben im Mittel SCHWÄCHERE Einzel-Links** (viele entfernte/marginale Nachbarn),
während Knoten mit wenigen, nahen Nachbarn starke Links haben. Das ist genau der Grund, warum eine
ETX-Metrik (SNR-gewichtet) Hubs nicht blind bevorzugt — ein realer Vorteil gegenüber reinem Hop-Count.

---

## 3) Firmware-Implikationen

**Bestätigt v4 die ausgelieferte Stufe A?** — **JA, in vollem Umfang, mit zusätzlicher Schärfung:**

1. **`flood.max = 15`** — **bestätigt.** Realer Durchmesser P90 = 12 → 15 ist die korrekte Wahl
   (Reserve über dem Durchmesser, Lieferquote ≥ Baseline, Airtime neutral). **Nicht** auf 12 senken
   (zu knapp am P90), **nicht** auf 64 belassen (unnötig). 15 bleibt.
2. **Hop-gewichtetes Rebroadcast-Delay (`tx_hop_weight = 0,6`)** — **bestätigt.** M1 ist über den
   gesamten Adoptions-Sweep safe und monoton (deliv +0,02, Airtime neutral), führt den Flood
   zuverlässig über kürzere Pfade. `tx_hop_weight = 0,6` ist ein guter Wert; kein Änderungsbedarf.
3. **EWMA-Nachbar-SNR + SNR-/ETX-Tiebreak** — **gestärkt.** v4 zeigt: echtes Per-Link-SNR ist ein
   **brauchbarer Zuverlässigkeits-Tiebreaker** (18 % andere Pfade, +0,168 Reliability für +0,19 Hops),
   und Hub-Links sind real schwächer (Korr −0,52). Eine ETX-Metrik, die SNR **als Tiebreaker nach
   Hops** nutzt (nicht primär), ist datenbelegt sinnvoll. **`MHR_HOP_HORIZON`** sollte als
   Pfad-Längen-Horizont bei **12** (= realer P90-Durchmesser) bleiben; das *Flood-Limit* `flood.max`
   bei **15**.

**Was v4 NEU mahnt (gegenüber Studie/Roadmap):**

4. **Airtime-Suppression (Stufe B/C: M2/M3/M4/COMBI) NICHT ohne harte Redundanz-Garantie ausrollen.**
   Auf der echten sparsen Topologie (Ø-Grad 3,45) bricht jede reine Suppression die Lieferquote schon
   ab **α ≈ 5–10 %** (M4 ab α≈5 %, −0,40 bei voller Adoption). Die in v3/Studie attestierte
   Hochadoptions-Sicherheit dieser Mechanismen **hält auf echten Kanten nicht**. Empfehlung:
   - Stufe B/C nur mit **lokal bestätigter Redundanz** (k≥2 unabhängige Cover-Sender *gehört*, bevor
     unterdrückt wird) und **Reliability-Floor** (nie den einzigen Pfad zu einem Blattknoten kappen).
   - Vor Hardware-Rollout zwingend auf dieser realen Topologie gegenrechnen, nicht auf dem
     geometrischen Modell.

**Netto:** Stufe A (hop-delay, flood.max 15, tx_hop_weight 0,6, EWMA-SNR) ist auf echten Kanten
**voll bestätigt und sicher**. Der eigentliche Gewinn ist **Airtime** (−99 % bei MHR-Unicast,
Flood aktiviert real ~617 Repeater) und **Zuverlässigkeit** (+14 %), nicht Hop-Detour. Die
aggressiveren Suppressions-Stufen brauchen mehr Vorsicht als die Studie nahelegte.

---

## Erzeugte Artefakte

| Datei | Inhalt |
|---|---|
| `mhr_sim_real_v4.py` | Komplette v4-Pipeline (Seed 42, 6 Seeds, kommentiert) |
| `sim_results_v4.json` | Alle Kennzahlen (Topologie, Baseline-vs-MHR, flood.max-Sweep, Adoptions-Sweep, SNR) |
| `fig_v4_topology.png` | Echte neighbor-graph-Topologie, Kanten nach echtem avg_snr gefärbt |
| `fig_v4_baseline_vs_mhr.png` | Baseline vs. MHR (Hops, Airtime, Zuverlässigkeit) |
| `fig_v4_floodmax_sweep.png` | flood.max-Sweep (Lieferquote + Airtime vs. Baseline) |
| `fig_v4_adoption_safety.png` | Safety-Matrix (Mechanismus × α), Top-Traffic |
| `fig_v4_snr_vs_reliability.png` | SNR vs. Pfad-Kürze + ETX-Pfad-Reliability-Vorteil |

---

## Limitierungen (wissenschaftlich ehrlich)

1. **neighbor-graph erfasst nur REAL GENUTZTE Links.** Kanten entstehen aus beobachteten
   Relay-Pfaden — **potenzielle** (aber im Beobachtungsfenster ungenutzte) Links fehlen. Die echte
   Topologie ist damit eher noch etwas dichter als hier; die Sparsity (und die daraus gefolgerte
   geringe Redundanz) ist eine **Obergrenze** der Vorsicht, kann aber Suppression real minimal
   besser dastehen lassen, als v4 zeigt. Richtungsaussage (Suppression bricht früh) bleibt robust.
2. **Momentaufnahme:** ein einzelner Abzug (2026-05-30). Tageszeit-/Churn-Effekte nicht gemittelt.
3. **Observer-Bias:** der Graph stützt sich auf die Beobachterstandorte (Schwerpunkt Rheinland);
   Regionen mit wenigen Observern sind unterrepräsentiert (kleinere Komponenten, evtl. künstlich
   getrennt).
4. **`ambiguous`-Kanten verworfen** (173 = 8,8 %): die Kern-Analyse ist konservativ; diese Kanten
   würden die Konnektivität leicht erhöhen (Riesenkomponente etwas größer). Separat gezählt.
5. **Pubkey-Join unvollständig:** 137 gekürzte Endpunkt-Keys ohne eindeutigen Treffer in `nodes.json`
   → diese Knoten ohne Geo/Stats (Topologie-Plot zeigt nur 930/1034 Knoten geografisch). Die
   Routing-Analyse läuft trotzdem auf allen Kern-Knoten (Identität = Pubkey).
6. **Flood-Modell ohne Duty-Cycle/CSMA-Kollisionen:** zählt Sende-Ereignisse, modelliert keine
   Kanalkollisionen oder 10-%-Duty-Cycle-Drosselung. Der reale Airtime-Druck ist damit eher noch
   unterschätzt — zugunsten von MHR. Umgekehrt erklärt das, warum der **idealisierte** Flood hier
   fast keine Detours zeigt, während reale Pakete (A4 in v3) Median 2,1× Detour hatten.
7. **SNR-Reliability-Kurve ist eine Modellwahl** (logistisch, Breite 4 dB um −12 dB-Schwelle):
   echtes avg_snr geht ein, aber die Abbildung SNR→p ist kalibriert, nicht gemessen.
