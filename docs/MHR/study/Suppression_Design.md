# Design: Redundanz-gesicherte Flood-Suppression (Stufe B, sicher)

*Ziel: Airtime senken durch Unterdrücken redundanter Flood-Rebroadcasts — aber so, dass die
Lieferquote bei JEDEM Adoptionsgrad (1 Knoten → alle) ≥ Baseline bleibt, auch auf der sparsen
echten Topologie. Rein lokal, passiv, mischbetriebs-sicher. „Erst Design, simulativ abgesichert,
dann Code."*

Basis: v4-Befund (`MeshCore_Simulation_v4_NeighborGraph.md`) — naive Suppression (M3/M4) bricht
die Lieferquote schon ab **5–10 % Adoption**, weil ein unterdrückter Rebroadcast der *einzige* Pfad
zu einem Blattknoten sein kann (Ø-Grad real nur 3,45).

---

## 1. Leitidee

> **Ein Knoten schweigt nur, wenn er lokal BEWEISEN kann, dass die Abdeckung ohne ihn schon steht.**

Im Zweifel wird gesendet (Default = sichere Aktion). Das macht den Mechanismus strukturell
„nie schlechter": Unsicherheit ⇒ Rebroadcast wie Upstream.

Der Schlüssel: **passives Topologie-Lernen** (M6, kostet 0 Airtime — die Pfad-Ketten fliegen
ohnehin) liefert die 2-Hop-Nachbarschaft, mit der ein Knoten prüfen kann, ob seine Nachbarn auch
von anderen erreicht werden.

---

## 2. Die fünf Schutzschichten (alle müssen erfüllt sein, sonst senden)

Ein Repeater R hat eine Flood-Kopie P empfangen und einen (hop-gewichteten, Stufe A) Backoff bis T.
Während `[jetzt, T]` lauscht er und sammelt. Bei T, *bevor* er sendet:

| # | Guard | Bedingung zum SCHWEIGEN | Schützt vor |
|---|-------|-------------------------|-------------|
| **G1** | **Low-Degree-/Leaf-Schutz** | `R.degree ≥ MIN_DEGREE` (z.B. 4) | Brücken/Blatt-Relais schweigen NIE |
| **G2** | **Cover-Count** | ≥ `K_COVER` (z.B. 2) *verschiedene* andere Knoten haben P im Fenster bereits weitergesendet | zu wenig Redundanz |
| **G3** | **Neighbour-Coverage** | *jeder* bekannte Nachbar von R ist auch Nachbar mindestens eines Cover-Senders (aus passivem 2-Hop-Tabelle) | „ich bin jemandes einziger Relay" |
| **G4** | **Reliability-Floor** | die Cover-Sender wurden mit EWMA-SNR ≥ `SNR_FLOOR` gehört (zuverlässige Redundanz, nicht nur vorhandene) | Wackel-Redundanz |
| **G5** | **Prob-Margin** | `rng() < P_SUPPRESS` (z.B. 0,8) | Rest-Redundanz als Sicherheitspuffer; bei voller Adoption sendet immer ein Bruchteil |

Erfüllt R **alle** G1–G5 → **Suppress** (eigenen Rebroadcast auslassen). Sonst → **Rebroadcast**
(exakt wie Upstream). 

Die zentrale, gegen das v4-Versagen gerichtete Schicht ist **G3**: sie verhindert genau das Kappen
des einzigen Blatt-Pfads. G1 ist der billige, robuste Fallback, falls die 2-Hop-Tabelle (noch)
unvollständig ist (frisch gebooteter Knoten lernt erst).

---

## 3. Passives 2-Hop-Lernen (das Fundament, M6)

Kostet **null** Airtime. Aus jeder gehörten Flood-Pfad-Kette und jedem Advert:
- **1-Hop-Nachbarn** von R = Knoten, die R direkt hört (letzter Pfad-Hop vor R; Advert-Absender in Reichweite). EWMA-SNR je Nachbar (existiert via Stufe-A-`putNeighbour` schon).
- **2-Hop-Wissen** = für jeden gehörten Sender X dessen jüngste Nachbarn (aus X's Pfad-Ketten ableitbar). Fixe Tabelle, Verdrängung least-recently-heard. Dimensionierung ~1–2 KB (passt auf nRF52840/ESP32-S3).
- Konvergenz: G3 ist erst „scharf", wenn die Tabelle als *frisch genug* gilt (Zeitstempel); sonst greift nur G1 (degree-basiert) → konservativ sicher.

Reine Beobachtung vorhandener Felder ⇒ Alt-Knoten merken nichts, kein Paketformat-Eingriff.

---

## 4. Warum das bei JEDEM Adoptionsgrad sicher ist

- **α niedrig:** Stock-Knoten fluten ohnehin voll → ein paar schweigende MHR-Knoten sind harmlos
  (G1–G5 nur zusätzliche Vorsicht). 
- **α hoch / 100 %:** Kein Stock-Sicherheitsnetz mehr — jetzt tragen G2 (≥k Cover) + G3
  (Blatt-Schutz) + G5 (immer ein Bruchteil sendet). G3 garantiert, dass kein Knoten den letzten
  Pfad zu einem schlecht vernetzten Nachbarn kappt; G5 hält eine harte Restredundanz. Genau die
  Schicht, die der naiven M3/M4 fehlte.
- **Invariante:** Suppress nur bei *bewiesener* reichlicher, zuverlässiger, blatt-sicherer
  Redundanz; sonst senden. ⇒ Lieferquote kann konstruktiv nicht unter Baseline fallen.

---

## 5. Parameter (CLI-tunbar, konservative Defaults)

| Parameter | Default | Bedeutung |
|-----------|---------|-----------|
| `supp_enable` | 0 (aus) | Gesamtschalter (erst nach Sim-/Bench-Freigabe an) |
| `supp_min_degree` (G1) | 4 | unter diesem Grad nie schweigen |
| `supp_k_cover` (G2) | 2 | nötige verschiedene Cover-Sender |
| `supp_snr_floor` (G4) | -6 dB | Mindest-EWMA-SNR der Cover-Sender |
| `supp_prob` (G5) | 0.8 | Suppress-Wahrscheinlichkeit bei erfüllten Guards |

Default `supp_enable = 0` ⇒ exakt Upstream/Stufe-A-Verhalten, bis die Simulation den sicheren
Parametersatz bestätigt und ein Bench-Test ihn validiert.

---

## 6. Validierungsplan (VOR dem Code)

Auf dem echten `neighbor-graph` (v4-Harness):
1. **Safety-Sweep:** Adoption 1 Knoten → 100 %. Nachweis: Lieferquote ≥ Baseline bei **allen** α
   (im Gegensatz zu naivem M3/M4, das ab 5–10 % bricht) UND messbare Airtime-Senkung.
2. **Parameter-Sweep:** `k_cover ∈ {2,3}`, `min_degree ∈ {3,4,5}`, `supp_prob ∈ {0.6,0.8,1.0}` —
   sicheren Sweet-Spot finden (max. Airtime-Gewinn bei deliv ≥ Baseline über alle α).
3. **Ablation:** zeige, dass G3 (Neighbour-Coverage) die *load-bearing* Schicht ist (ohne G3 bricht
   es wie naiv; mit G3 hält es).
4. **Stress:** Churn + Linkausfall — bleibt die Invariante?
5. Erst wenn (1)–(4) bestehen → Code (lokal in `routeRecvPacket`/Outbound-Queue + passive
   2-Hop-Tabelle), wieder adversarial reviewt und bench-getestet.

**Erfolgskriterium:** ein Parametersatz, der über den GESAMTEN Adoptions-Sweep die Safety-Invariante
hält und trotzdem nennenswert Airtime spart. Findet die Simulation keinen → Mechanismus wird NICHT
codiert (Qualität/Stabilität vor Airtime).
