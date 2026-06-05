# Bench-Test-Plan — MHR-MeshCore (Heltec V4)

*Was in Software validiert ist, muss auf echter Funk-Hardware bestätigt werden. Dieser Plan ist
auf Heltec V4 (ESP32-S3) zugeschnitten. Reihenfolge = aufsteigendes Risiko; jede Stufe hat klare
Akzeptanzkriterien. **Nur Bench-/Ersatzgeräte, nicht produktive Repeater.** Vorher Config/Identität
sichern.*

## 0. Voraussetzungen
- **Hardware:** mind. **5 Heltec V4** (für Flood-Umwege/Best-of-N reichen 3–4; für Backbone/Regionen
  ≥ 5, idealerweise mit 2 „Regionen"). USB für CLI/Serial-Log an jedem Knoten.
- **Firmware:** `dist/`-`.bin` (default-Konfig: Stufe A + Best-of-N AN, Stufe B + Phase 2 AUS).
- **Mess-Werkzeug:** CLI-Kommandos `trace <ziel>`, `get stats`, `neighbors`, Paket-Log (`set log on`),
  und — für Phase 2 — eine DV-Tabellen-Ausgabe (Debug). Topologie räumlich strecken (Dämpfer/Abstand),
  damit Mehr-Hop-Pfade + Umwege real entstehen.
- **Baseline-Referenz:** ein Knoten mit **Upstream**-Firmware (zum Vergleich „nie schlechter").

## 1. Stufe A — Hop-gewichtetes Timing + flood.max 15  *(default AN)*
**Test:** 4–5 Knoten in einer Kette/Mesh, wiederholt `trace` zwischen den Enden vor/nach Aktivierung
(`set txhopweight 0` ↔ `0.6`, `set flood.max 64` ↔ `15`).
**Akzeptanz:** mit MHR sind die getracten Pfade im Mittel **gleich kurz oder kürzer**; nie länger.
flood.max 15 kappt keine *nötige* Zustellung (Netzdurchmesser < 15). Lieferquote ≥ Upstream-Knoten.

## 2. Best-of-N am Ziel  *(default AN, Repeater)*
**Test:** Discovery über **zwei** Pfade unterschiedlicher Länge erzwingen (z.B. ein direkter 2-Hop +
ein 4-Hop-Umweg). Ziel-Knoten Serial-Log beobachten.
**Akzeptanz (die im Review/Code genannten Punkte):**
1. **Single-Copy = first-wins:** nur ein Pfad → zurückgemeldeter Pfad identisch (nur ~Fenster später).
2. **Multi-Copy:** kürzester (2-Hop) Pfad wird zurückgemeldet, nicht der 4-Hop-Umweg.
3. **SNR-Tiebreak:** zwei gleich lange Pfade, unterschiedliches Last-Hop-SNR → besseres SNR gewinnt.
4. **Dedup-Invariante (kritisch):** Payload genau **einmal** zugestellt, egal wie viele Kopien.
5. **Genau ein Path-Return** trotz N Kopien.
6. `set bofn.enable 0` → sofort first-wins ohne Reboot; `set bofn.window 800` greift live.

## 3. Stufe B — guarded Suppression  *(default AUS → für Test einschalten)*
**Vorbereitung:** dichtes Cluster (≥ 4–5 Repeater in gegenseitiger Reichweite). 2-Hop-Tabelle erst
„frisch" lernen lassen (einige Advert-Perioden). Dann `set supp.enable 1` (Satz: k_cover 2,
min_degree 3, snr_floor -6, prob 80).
**Akzeptanz:**
- **Airtime sinkt** im Cluster (weniger Rebroadcasts, via Paket-Log/`get stats`), **Lieferquote bleibt
  ≥** dem AUS-Zustand (kein Blattknoten abgeschnitten — teste ein schwach angebundenes Blatt!).
- **Frische-Gating:** frisch gebooteter Knoten unterdrückt NICHT, bevor er gelernt hat.
- `set supp.enable 0` → sofort wie Stufe A. Bei Zweifel/Coverage-Verlust: aus lassen.

## 4. Phase 2 — Backbone  *(default AUS; NUR nach Code-Fix B1 + erneutem Konvergenz-Gate aktivieren!)*
> ⚠️ **Sperre:** `bb.enable 1` erst, wenn der Loop-BLOCKER B1 (Per-Ziel-Seqno) gefixt und das
> Konvergenz-Gate gegen die korrigierte Logik erneut GO ist. Vorher würde es Loops erzeugen.
**Test (≥ 5 Knoten, 2 Regionen):** `set bb.enable 1` auf den Repeatern, `bb.period` 600.
**Akzeptanz:**
- **Konvergenz:** DV-Tabellen stabilisieren sich (Debug-Dump), kein dauerhaftes Wackeln.
- **Schleifenfreiheit:** keine kreisenden Pakete (Paket-Log/`trace` zeigt loop-freie Pfade), auch
  während/nach Knoten an-/abschalten (Churn).
- **Kontroll-Airtime** bleibt klein (DV nur zero-hop, Periode ≥ 300 s) — `get stats` gegen Budget.
- **Mixed-FW:** ein Upstream-Knoten im Netz → ignoriert DV (kein Reflood), Netz bleibt funktionsfähig.
- **Nie schlechter:** Lieferquote ≥ AUS-Zustand (Data-Plane-Short-Circuit ist noch nicht aktiv →
  Daten laufen weiter über Flood-and-cache; der Test prüft v.a. Control-Plane-Stabilität).

## 5. Allgemeine Sicherheits-/Abbruchkriterien
- Sinkt bei einer Stufe die **Lieferquote** unter den AUS-Zustand → Feature aus (`set …enable 0`),
  Ursache notieren. „Nie schlechter" ist die harte Linie.
- Watchdog-Resets / RAM-Auffälligkeiten (`get stats`) beobachten.
- Jede Stufe einzeln testen, bevor mehrere kombiniert werden.
- Ergebnisse (Pfade, Airtime, Lieferquote, je AN/AUS) protokollieren → fließt in die nächste
  Sim-Kalibrierung zurück.

## Was die Software-Validierung schon abgedeckt hat (Kontext)
Routing-*Verhalten*, Adoptions-Effekte, Konvergenz/Schleifenfreiheit (Sim-Gate) sind in `docs/MHR/sim`
+ `docs/MHR/study` belegt. Der Bench-Test prüft die **funk-/HW-abhängigen** Punkte, die keine Sim
liefern kann: reales Timing/Cover im Backoff-Fenster, 2-Hop-Lern-Konvergenz on-air, Capture/Kollision,
Watchdog/RAM unter Last, und dass Stock-Knoten DV wirklich ignorieren.

---
## 🇬🇧 English Translation

# Bench Test Plan — MHR-MeshCore (Heltec V4)

*What has been validated in software must be confirmed on real radio hardware. This plan is
tailored to Heltec V4 (ESP32-S3). Order = ascending risk; each stage has clear
acceptance criteria. **Bench/spare devices only, not production repeaters.** Back up config/identity
beforehand.*

## 0. Prerequisites
- **Hardware:** at least **5 Heltec V4** (3–4 suffice for flood detours/Best-of-N; for backbone/regions
  ≥ 5, ideally with 2 "regions"). USB for CLI/serial log on each node.
- **Firmware:** `dist/`-`.bin` (default config: Stage A + Best-of-N ON, Stage B + Phase 2 OFF).
- **Measurement tools:** CLI commands `trace <target>`, `get stats`, `neighbors`, packet log (`set log on`),
  and — for Phase 2 — a DV table dump (debug). Spread topology spatially (attenuators/distance)
  so that multi-hop paths + detours arise in reality.
- **Baseline reference:** one node running **upstream** firmware (for the "never worse" comparison).

## 1. Stage A — Hop-weighted Timing + flood.max 15  *(default ON)*
**Test:** 4–5 nodes in a chain/mesh, repeated `trace` between the endpoints before/after activation
(`set txhopweight 0` ↔ `0.6`, `set flood.max 64` ↔ `15`).
**Acceptance:** with MHR the traced paths are on average **equal or shorter**; never longer.
flood.max 15 does not cut off any *necessary* delivery (network diameter < 15). Delivery rate ≥ upstream node.

## 2. Best-of-N at Destination  *(default ON, repeater)*
**Test:** Force discovery over **two** paths of different length (e.g. one direct 2-hop +
one 4-hop detour). Monitor destination node serial log.
**Acceptance (points listed in review/code):**
1. **Single-copy = first-wins:** only one path → reported path identical (only ~window later).
2. **Multi-copy:** shortest (2-hop) path is reported, not the 4-hop detour.
3. **SNR tiebreak:** two equally long paths, different last-hop SNR → better SNR wins.
4. **Dedup invariant (critical):** payload delivered exactly **once**, regardless of how many copies.
5. **Exactly one path-return** despite N copies.
6. `set bofn.enable 0` → immediately first-wins without reboot; `set bofn.window 800` takes effect live.

## 3. Stage B — Guarded Suppression  *(default OFF → enable for test)*
**Preparation:** dense cluster (≥ 4–5 repeaters within mutual range). Let the 2-hop table learn
"fresh" first (several advert periods). Then `set supp.enable 1` (settings: k_cover 2,
min_degree 3, snr_floor -6, prob 80).
**Acceptance:**
- **Airtime decreases** in the cluster (fewer rebroadcasts, via packet log/`get stats`), **delivery rate remains
  ≥** the OFF state (no leaf node cut off — test a weakly connected leaf!).
- **Freshness gating:** a freshly booted node does NOT suppress before it has learned.
- `set supp.enable 0` → immediately reverts to Stage A behavior. If in doubt/coverage loss: leave off.

## 4. Phase 2 — Backbone  *(default OFF; ONLY activate after Code-Fix B1 + renewed convergence gate!)*
> ⚠️ **Lock:** `bb.enable 1` only after the loop-BLOCKER B1 (per-destination seqno) is fixed and the
> convergence gate is GO again against the corrected logic. Before that it would generate loops.
**Test (≥ 5 nodes, 2 regions):** `set bb.enable 1` on the repeaters, `bb.period` 600.
**Acceptance:**
- **Convergence:** DV tables stabilize (debug dump), no persistent oscillation.
- **Loop-freedom:** no circulating packets (packet log/`trace` shows loop-free paths), including
  during/after nodes being toggled on/off (churn).
- **Control airtime** stays small (DV zero-hop only, period ≥ 300 s) — `get stats` against budget.
- **Mixed-FW:** one upstream node in the network → ignores DV (no reflood), network remains functional.
- **Never worse:** delivery rate ≥ OFF state (data-plane short-circuit is not yet active →
  data continues to flow via flood-and-cache; the test primarily checks control-plane stability).

## 5. General Safety / Abort Criteria
- If at any stage the **delivery rate** drops below the OFF state → disable the feature (`set …enable 0`),
  note the cause. "Never worse" is the hard line.
- Monitor watchdog resets / RAM anomalies (`get stats`).
- Test each stage individually before combining multiple.
- Log results (paths, airtime, delivery rate, per ON/OFF) → feeds back into the next
  simulation calibration.

## What Software Validation Has Already Covered (Context)
Routing *behavior*, adoption effects, convergence/loop-freedom (sim gate) are documented in `docs/MHR/sim`
+ `docs/MHR/study`. The bench test checks the **radio/HW-dependent** points that no simulation
can provide: real timing/coverage in the backoff window, 2-hop learning convergence on-air, capture/collision,
watchdog/RAM under load, and that stock nodes truly ignore DV.
