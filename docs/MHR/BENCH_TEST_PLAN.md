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
