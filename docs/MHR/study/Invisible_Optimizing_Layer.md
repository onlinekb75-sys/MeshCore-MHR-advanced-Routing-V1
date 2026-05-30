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
