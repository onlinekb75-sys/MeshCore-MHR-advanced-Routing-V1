# Die unsichtbare, node-lokale Mesh-Optimierungs-Schicht (Architektur)

*Vision: MHR als eine **für Nutzer und Anwendung transparente Zwischenschicht** zwischen Funk-Empfang
und dem bestehenden MeshCore-Routing. Niemand stellt etwas um, kein Paket sieht anders aus, die
Chat-/Messaging-Funktion bleibt bit-identisch — das Netz **arbeitet einfach leiser, kürzer,
zuverlässiger**. Vollständig **dezentral**: jeder Knoten entscheidet allein aus dem, was er ohnehin
hört. Kein Server, keine Koordination, keine neue Infrastruktur.*

Grundlage: validiert auf echten CoreScope-Daten (`../sim/`, `MeshCore_Simulation_v3/v4`, Studie).

---

## 1. Zwei Ebenen, beide node-lokal

```
            ┌──────────────────────────────────────────────┐
   Funk-RX ─┤  (P) PASSIVE WAHRNEHMUNG  — 0 Airtime          │  hört nur mit, was eh fliegt
            │   • Nachbar-Tabelle (EWMA-SNR)                  │
            │   • 2-Hop-Adjazenz (frische-gegatet)           │
            │   • Redundanz/Cover-Histogramm                 │
            │   • lokaler Pfadlängen-Hist (= lok. Durchmesser)│
            │   • Duty-Cycle-Auslastung, Churn               │
            └───────────────┬──────────────────────────────┘
                            │ speist
            ┌───────────────▼──────────────────────────────┐
   Routing ─┤  (D) LOKALE ENTSCHEIDUNGEN  — kein neues Paket │  ändert nur Timing/ob/welcher Pfad
            │   1 Hop-gewichtetes Flood-Timing  (Stufe A ✅) │
            │   2 Guarded Suppression G1–G5     (Stufe B ✅) │
            │   3 Pfad-Erfolgs-Reinforcement    (neu)        │
            │   4 flood.max = lok. Durchmesser  (Stufe A ✅) │
            │   5 Duty-Cycle-Selbstschutz       (defensiv)   │
            └────────────────────────────────────────────────┘
```

(P) kostet **null** Airtime (reine Beobachtung). (D) erzeugt **kein** neues Paket und ändert **kein**
Format — es beeinflusst nur, *wann* / *ob* / *über welchen gecachten Pfad* der Knoten ohnehin sendet.

---

## 2. Was die Schicht „unsichtbar" macht (Transparenz-Garantien)

- **Kein Paketformat-Eingriff, keine neuen Pflichtfelder, keine neuen Paket-Typen** → Alt-Knoten und
  die App sehen exakt das heutige Protokoll.
- **Keine app-/nutzersichtbare Verhaltensänderung** — Messaging/Discovery funktionieren identisch;
  der einzige beobachtbare Effekt ist *weniger Airtime / kürzere Pfade / höhere Zuverlässigkeit*.
- **Jede Komponente „nie schlechter als Upstream"** und einzeln abschaltbar (CLI). Default-sicher.
- **Mixed-Firmware:** koexistiert mit Stock-Knoten; bei Unsicherheit immer die sichere Aktion (senden).

## 3. Was sie „node-only" macht (Dezentralitäts-Garantien)

- **Jede Entscheidung aus lokal beobachtbaren Daten** (eigene RX, eigene Nachbarschaft) — kein
  zentraler Dienst, keine Inter-Node-Aushandlung über das hinaus, was ohnehin fliegt.
- **Kein Backbone, kein Control-Plane-Overlay** (das wäre Phase 2 — bewusst NICHT Teil dieser
  Schicht, weil es Kontroll-Traffic = Airtime kostet und nicht mehr „unsichtbar/gratis" ist).
- Konvergiert allein durch Mithören; ein frisch gebooteter Knoten fällt sauber auf konservativ
  zurück, bis er genug gelernt hat (Frische-Gating).

---

## 4. Die Bausteine (Status)

| # | Baustein | Status | Wirkung (auf Realdaten) |
|---|----------|--------|--------------------------|
| P | Passives Sensing (Nachbarn, 2-Hop, Redundanz, Durchmesser, Duty-Cycle) | Fundament (Teile in Stufe A/B) | ermöglicht alles Übrige, 0 Airtime |
| 1 | Hop-gewichtetes Flood-Timing | **✅ im Code (Stufe A)** | kürzere Pfade führen den Flood |
| 2 | Redundanz-guarded Suppression (G1–G5) | **✅ im Code, default-AUS (Stufe B)** | −12…15 % Airtime bei hoher Adoption, Delivery ≥ Baseline |
| 3 | **Pfad-Erfolgs-Reinforcement** (EWMA-Erfolg je Pfad, bewährte verstärken, wackelnde demoten *vor* Ausfall) | **neu — hier zu simulieren** | weniger Re-Discovery-Airtime, stabilere Routen |
| 4 | flood.max = realer Durchmesser (15) | **✅ im Code (Stufe A)** | kappt Fern-Umwege |
| 5 | Duty-Cycle-Selbstschutz (defensiver Backoff nahe Limit) | Konzept | schützt vor eigener Sättigung |

Verworfen (ehrlich, datenbelegt): schneller adaptiver Regler, langsame per-Node-Kalibrierung, TPC —
sie bringen über die Guards hinaus nichts (siehe `Adaptive_Controller_Design.md`,
`Local_Calibration_Study.md`). Die Schicht bleibt darum bewusst **schlank**.

---

## 5. Node-Realisierbarkeit (die Kernfrage)

| Ressource | Bedarf der Schicht | Heltec V4 (ESP32-S3) | nRF52840 |
|---|---|---|---|
| RAM (fixe Tabellen) | ~1–2 KB (Nachbarn + 2-Hop + Pending + Pfad-Memory) | 512 KB ✅ reichlich | 256 KB ✅ |
| CPU pro Paket | wenige Integer-Vergleiche (Guards) | trivial | trivial |
| CPU „alle paar Stunden" | (entfällt — kein Regler nötig) | — | — |
| Flash | wenige KB Code | reichlich | reichlich |
| **Airtime** (der eigentliche Engpass) | **0 zusätzlich** (rein passiv + nur weniger senden) | — | — |

→ **Erste Einschätzung: realistisch node-only implementierbar.** Keine dyn. Allokation, fixe Tabellen,
kein Server. Der Engpass (Airtime) wird **gesenkt, nicht belastet**. Offene Validierung: der
**Komposit-Effekt** (interagieren die Bausteine sauber?), das **Pfad-Reinforcement** (neu), und die
**Hardware-Bench-Punkte** der Suppression (Cover-Timing, 2-Hop-Frische).

---

## 6. Abgrenzung
Diese Schicht ist die **gratis, unsichtbare, sichere** Optimierung. Der proaktive **Backbone (Phase 2)**
ist explizit NICHT enthalten — er ist mächtiger, aber er kostet Kontroll-Airtime und ist sichtbar im
Sinne neuer Paket-Typen; er bleibt die separate, ambitionierte Kür.

---
## 🇬🇧 English Translation

# The Invisible, Node-Local Mesh Optimization Layer (Architecture)

*Vision: MHR as a **transparent intermediate layer — invisible to users and applications** — between
radio reception and the existing MeshCore routing. Nobody reconfigures anything, no packet looks
different, the chat/messaging function stays bit-identical — the network **simply works quieter,
shorter, more reliably**. Fully **decentralized**: every node decides on its own from what it hears
anyway. No server, no coordination, no new infrastructure.*

Basis: validated on real CoreScope data (`../sim/`, `MeshCore_Simulation_v3/v4`, study).

---

## 1. Two Layers, Both Node-Local

```
            ┌──────────────────────────────────────────────┐
   Radio-RX ─┤  (P) PASSIVE SENSING  — 0 Airtime             │  only listens to what flies anyway
            │   • Neighbor table (EWMA-SNR)                   │
            │   • 2-hop adjacency (freshness-gated)          │
            │   • Redundancy/cover histogram                 │
            │   • Local path-length hist (= local diameter)  │
            │   • Duty-cycle utilization, churn              │
            └───────────────┬──────────────────────────────┘
                            │ feeds
            ┌───────────────▼──────────────────────────────┐
   Routing ─┤  (D) LOCAL DECISIONS  — no new packet          │  only changes timing/whether/which path
            │   1 Hop-weighted flood timing   (Stage A ✅)   │
            │   2 Guarded Suppression G1–G5   (Stage B ✅)   │
            │   3 Path-success reinforcement  (new)          │
            │   4 flood.max = local diameter  (Stage A ✅)   │
            │   5 Duty-cycle self-protection  (defensive)    │
            └────────────────────────────────────────────────┘
```

(P) costs **zero** airtime (pure observation). (D) generates **no** new packet and changes **no**
format — it only influences *when* / *whether* / *via which cached path* the node transmits anyway.

---

## 2. What Makes the Layer "Invisible" (Transparency Guarantees)

- **No packet-format intrusion, no new mandatory fields, no new packet types** → legacy nodes and
  the app see exactly today's protocol.
- **No app-/user-visible behavioral change** — messaging/discovery work identically;
  the only observable effect is *less airtime / shorter paths / higher reliability*.
- **Every component "never worse than upstream"** and individually disableable (CLI). Default-safe.
- **Mixed firmware:** coexists with stock nodes; when in doubt, always the safe action (transmit).

## 3. What Makes it "Node-Only" (Decentralization Guarantees)

- **Every decision from locally observable data** (own RX, own neighborhood) — no
  central service, no inter-node negotiation beyond what is already in flight.
- **No backbone, no control-plane overlay** (that would be Phase 2 — deliberately NOT part of this
  layer, because it costs control traffic = airtime and is no longer "invisible/free").
- Converges purely by overhearing; a freshly booted node falls back cleanly to conservative
  behavior until it has learned enough (freshness gating).

---

## 4. The Building Blocks (Status)

| # | Building Block | Status | Effect (on real data) |
|---|----------------|--------|-----------------------|
| P | Passive sensing (neighbors, 2-hop, redundancy, diameter, duty-cycle) | Foundation (parts in Stage A/B) | enables everything else, 0 airtime |
| 1 | Hop-weighted flood timing | **✅ in code (Stage A)** | shorter paths lead the flood |
| 2 | Redundancy-guarded suppression (G1–G5) | **✅ in code, default-OFF (Stage B)** | −12…15 % airtime at high adoption, delivery ≥ baseline |
| 3 | **Path-success reinforcement** (EWMA success per path, strengthen proven paths, demote shaky ones *before* failure) | **new — to be simulated here** | less re-discovery airtime, more stable routes |
| 4 | flood.max = real diameter (15) | **✅ in code (Stage A)** | cuts off long-range detours |
| 5 | Duty-cycle self-protection (defensive backoff near limit) | Concept | protects against own saturation |

Discarded (honestly, data-backed): fast adaptive controller, slow per-node calibration, TPC —
they add nothing beyond the guards (see `Adaptive_Controller_Design.md`,
`Local_Calibration_Study.md`). The layer therefore remains deliberately **lean**.

---

## 5. Node Feasibility (the Core Question)

| Resource | Layer's requirement | Heltec V4 (ESP32-S3) | nRF52840 |
|---|---|---|---|
| RAM (fixed tables) | ~1–2 KB (neighbors + 2-hop + pending + path memory) | 512 KB ✅ ample | 256 KB ✅ |
| CPU per packet | few integer comparisons (guards) | trivial | trivial |
| CPU "every few hours" | (not needed — no controller required) | — | — |
| Flash | few KB of code | ample | ample |
| **Airtime** (the actual bottleneck) | **0 additional** (purely passive + only transmit less) | — | — |

→ **Initial assessment: realistically implementable node-only.** No dynamic allocation, fixed tables,
no server. The bottleneck (airtime) is **reduced, not added to**. Open validation: the
**composite effect** (do the building blocks interact cleanly?), **path reinforcement** (new), and the
**hardware bench points** of suppression (cover timing, 2-hop freshness).

---

## 6. Delimitation
This layer is the **free, invisible, safe** optimization. The proactive **backbone (Phase 2)**
is explicitly NOT included — it is more powerful, but it costs control airtime and is visible in
the sense of new packet types; it remains the separate, ambitious stretch goal.
