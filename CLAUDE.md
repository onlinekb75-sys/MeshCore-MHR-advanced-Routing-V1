# CLAUDE.md — MHR-MeshCore (Projektkontext für Claude Code)

Diese Datei fasst das gesamte Projekt zusammen, damit Claude Code direkt produktiv weiterarbeiten kann. Sie wird beim Öffnen des Repos automatisch geladen.

## Was das hier ist

Fork von **meshcore-dev/MeshCore** (`main`, MIT). Ziel: die Pfadfindung robuster machen und die durch zufällige Flood-**Umwege** verursachte Airtime-Last senken. MeshCore ist ein hybrides Mesh-Routing-Protokoll für LoRa-Funkgeräte (C++/Arduino/PlatformIO, nRF52 & ESP32).

**Status:** Build grün auf 4 Targets (repeater/companion/room_server/sensor). **Im Code & aktiv (default-an):** Phase 0 (RX-SNR-Flutung, prefer-shorter), Stufe A (hop-gewichtetes Delay `tx_hop_weight` + `tx_snr_weight` + EWMA-SNR + `flood.max` 64→15) und **Best-of-N am Ziel** (kürzester Pfad, dedup-sicher). **Im Code, default-AUS (Bench-gegated):** Stufe B guarded Suppression (`supp.enable`) und **Phase 2 DV-Backbone** (`bb.enable`, Konvergenz-Gate GO; Data-Plane-Short-Circuit noch nicht verdrahtet). **Verworfen, datenbelegt:** adaptiver Regler (2× NO-GO), per-Node-Kalibrierung, TPC. Validiert auf echten CoreScope-Daten (v3/v4 + Studien in `docs/MHR/sim`+`study`; CHANGES_MHR Patch 1–9). Alles auf Hardware **ungetestet** → `docs/MHR/BENCH_TEST_PLAN.md`.

## Das Problem (mit Code-Stellen im Upstream-Verhalten)

MeshCore nutzt **kein** metrik-basiertes Routing. Erste Nachricht wird geflutet, der Pfad gecacht, danach alles über diesen einen Pfad geroutet.

- **„First packet wins":** Ziel cached die *zuerst* eintreffende Flood-Kopie, nicht die beste — `src/Mesh.cpp:138-140` (Originalkommentar bestätigt das).
- **Zufalls-Timing pro Hop:** Retransmit-Delay ist random, keine Qualitätsgewichtung — `examples/simple_repeater/MyMesh.cpp:539` (`getRetransmitDelay`).
- **SNR-Gewichtung per Default AUS:** `rx_delay_base = 0` ⇒ `calcRxDelay` gibt 0 zurück — `examples/simple_repeater/MyMesh.cpp:534-536, 874`.
- **Pfad klebt / wird ungeprüft überschrieben:** `src/helpers/BaseChatMesh.cpp:304-307` (`onContactPathRecv`).
- Companion-Clients leiten **nicht** weiter (nur Repeater bilden das Relais-Mesh) — wichtig fürs Routing-Modell.

## Wichtigste Code-Ankerpunkte (zum Navigieren)

| Thema | Datei:Zeile |
|---|---|
| Empfang/Routing-Dispatch, first-wins | `src/Mesh.cpp` (`onRecvPacket` ~41, `routeRecvPacket` ~330, Path-Return ~167) |
| Flood-RX-Scoring / Delay-Queue | `src/Dispatcher.cpp:190-256` (`checkRecv`, `calcRxDelay`, `packetScore`) |
| Repeater-Overrides (Delays, allow-forward, calcRxDelay) | `examples/simple_repeater/MyMesh.cpp:429-546, 874` |
| Pfad-Übernahme am Sender | `src/helpers/BaseChatMesh.cpp:304-321` |
| Pfad-Encoding (count = `len & 0x3F`, size = `(len>>6)+1`) | `src/Packet.cpp:20-35` |
| CLI-Settings (rxdelay, txdelay, flood.max) | `src/helpers/CommonCLI.cpp:581-605, 778-784` |
| Konfig-Defaults (rx_delay_base, tx_delay_factor, flood_max) | `examples/simple_repeater/MyMesh.cpp:874-888` |

## Bereits umgesetzt (Phase 0 + Phase 1) — siehe `docs/MHR/CHANGES_MHR.md`

Alle Stellen mit `// MHR:` markiert, alle reversibel, nie schlechter als Upstream:

1. **(P0) SNR-gewichtete Flutung als Default an:** `rx_delay_base` 0.0 → 10.0 in `examples/simple_repeater/MyMesh.cpp`. Laufzeit-Reset: CLI `set rxdelay 0`. (Repeater-Build.)
2. **(P0) Pfad nur bei ≤ Hops übernehmen:** `src/helpers/BaseChatMesh.cpp:304-316` — Adoption nur wenn `(out_path_len & 0x3F) <= aktuell` oder `OUT_PATH_UNKNOWN`. (Companion-Builds.)
3. **(P1) SNR-gewichtetes TX-Retransmit-Delay:** `getRetransmitDelay()` in `MyMesh.cpp` nutzt neuen Pref `tx_snr_weight` (float 0..1, Default 0.5) — starke Empfänger senden Flood früher (zufallserhaltend, Fenster ≥ t+1). Pref am Ende von `NodePrefs` (`CommonCLI.h`) + Persistenz-Offset 291 (`CommonCLI.cpp`) + CLI `set/get txsnrweight`. Reset: `set txsnrweight 0`. (Repeater-Build.)
4. **(P1) EWMA-Nachbar-SNR:** `putNeighbour()` in `MyMesh.cpp` glättet `snr` (α=1/4) statt Überschreiben — stabile Linkqualität, Fundament für ETX.

## Design (noch nicht im Code) — `docs/MHR/`

- **`MeshCore_Routing_Analyse_und_Optimierung.md`** — Ursachenanalyse + Stufen A–D.
- **`MeshCore_Hybrid_Routing_Entwurf.md`** (MHR v1) — fusioniert reaktives DSR + ETX-Metrik + Best-of-N + proaktiver Backbone (ZRP-Idee: proaktiv unter Repeatern, reaktiv für Clients), zugeschnitten auf 10 % Duty-Cycle (EU-Sub-Band 869.4–869.65 MHz).
- **`MeshCore_Hybrid_Routing_v2_Robustheit.md`** (MHR v2) — Härtung aus Realdaten: Regions-Hierarchie (skaliert auf 80+ Repeater), Babel-Feasibility (Schleifenfreiheit), Feasible-Successor-Backup, gedämpfte zuverlässigkeitsdominante Metrik + Knoten-Stabilitäts-Gating, Reliability-Floor, Mixed-Firmware-Koexistenz, Müll-/Doublettenhärtung.

## Simulationen — `docs/MHR/sim/`

Python (numpy/networkx/matplotlib). MeshCore (flood + first-wins, Monte-Carlo) vs. MHR (ETX-optimaler Pfad + Backbone-Short-Circuit).

- `mhr_sim.py` — synthetische Rheinland-Topologie. Ergebnis: ~29 % Umwege, −63 % Airtime.
- `mhr_sim_real.py` — **25 echte Knoten** (Position+Rolle live aus CoreScope). Ergebnis: **~60 % Umwege, schlechtester Pfad 8 Hops, Umweg-Faktor 1,35×, −82 % Airtime, Zuverlässigkeit 0,58→0,66**. Links physik-modelliert (Log-Distance) — Gelände/Antennenhöhe nicht enthalten.
- Plots: `fig_real_*.png`, `fig_*.png`. Reproduzierbar (Seed 42).

## Datenzugang (für neue Sims)

CoreScope `corescope.meshrheinland.de` ist eine SPA — nur über einen **verbundenen Chrome-Browser** erreichbar (Tab → `fetch('/api/nodes?limit=5000')`). Felder: `name, lat, lon, role[repeater|companion|room], advert_count, last_heard, public_key, …`. Es gibt **keine** Kanten-/SNR-API (Map leitet Links aus Paketpfaden ab). Echte gemessene Links müssten aus der Packets/Tools-Ansicht extrahiert werden.

## Bauen & Flashen

- Ziel-Hardware: **Heltec V4** (ESP32-S3 → `.bin`; `.uf2` nur für nRF52 wie RAK4631).
- Lokal: `pip install platformio && pio run -e heltec_v4_repeater` → `.pio/build/heltec_v4_repeater/firmware.bin`.
- CI: `.github/workflows/build.yml` baut auf GitHub Actions und legt die `.bin` als Artifact ab (weitere Targets dort einkommentierbar).
- Flash: MeshCore Web-Flasher (Custom firmware) oder `esptool --chip esp32s3 write_flash 0x10000 firmware.bin`.

## Constraints & Konventionen (unbedingt beachten)

- **Keine dynamische Speicherallokation** außerhalb von `begin()`/Setup (MeshCore-Projektregel). Tabellen fix dimensionieren, mit Verdrängung (least-stable zuerst).
- Brace-/Indent-Stil der Kern-Module beibehalten; bestehenden Code **nicht** umformatieren (vermeidet Diff-Rauschen).
- **Mixed-Firmware-Netz:** neue Paket-/DV-Typen müssen für Alt-Knoten ignorierbar sein; immer graceful Fallback auf heutiges Flood-and-cache. Hash-Größen 1/2/3 Byte respektieren (Netz nutzt real 2 Byte).
- **Airtime/Duty-Cycle** ist der eigentliche Engpass (geteilter Halbduplex-Kanal), nicht RAM/CPU. MHR v2-Tabellen ~1–3 KB → passt locker auf nRF52840 (256 KB) und ESP32-S3 (512 KB).
- Lizenz MIT (`license.txt`), Attribution in `NOTICE.md`, Upstream-README als `README.upstream.md`.

## Konkrete nächste Schritte (Roadmap)

**Neu priorisiert durch Realdaten + Mechanismus-Studie** (`docs/MHR/sim/MeshCore_Simulation_v3_Realdaten.md`, `docs/MHR/study/MeshCore_Routing_Study.md`): SNR ist ein schwacher Hebel (Distanz erklärt SNR kaum) → **Hop-Zahl priorisieren**. Realer Umweg-Median 2,1×. Mechanismen mit Adoptions-Sweep (1 Knoten → alle) auf realer 776-Knoten-Topologie getestet, Safety-Invariante „nie schlechter als Baseline".

**Stufe A — ab 1 Knoten safe & monoton (zuerst umsetzen):**
- **`flood.max` 64 → 15** (datenbelegt: Netzdurchmesser P90=18; 12 ist zu aggressiv). Reine Konfig, `set flood.max 15`.
- **Hop-gewichtetes Rebroadcast-Delay** in `getRetransmitDelay` (`MyMesh.cpp`) — Kopien mit weniger akkumulierten Hops senden früher. Ersetzt/ergänzt den schwachen SNR-Hebel.
- **Best-of-N am Ziel nach HOPS** (nicht SNR): Sammelfenster, kürzesten Pfad zurückmelden. Einstieg `src/Mesh.cpp:138` / `onPeerPathRecv`. **Kernrisiko: Dedup (`hasSeen`) nicht brechen** → sorgfältig + Bench-Test.
- **Passives Topologie-Lernen** aus den Pfad-Ketten (`path_json`-Äquivalent im Paket, 0 Airtime) → lokale Link-Tabelle → Backup-Pfad statt Re-Flood bei Linkbruch (spart 94–96 % Re-Discovery). Einstieg: neue Tabelle, gefüllt in `routeRecvPacket`/`onAdvertRecv`.

**Stufe B — adaptive Airtime-Suppression** (nur unterdrücken bei lokal bestätigter Redundanz → auch bei voller Adoption sicher): Shorter-Path-Cancel des eigenen anstehenden Rebroadcasts; Counter-Suppression (k≈3). In `routeRecvPacket` / der Outbound-Queue.

**Stufe C — proaktiver Regions-Backbone / MPR-CDS** (großer Eingriff): ignorierbarer DV-Payload-Typ, Zero-Hop-Austausch, Bellman-Ford + Seqno + Babel-Feasibility + Feasible-Successor; Regionen via `region_map` als Cluster-Grenzen. Nur mit harter Redundanz-Garantie (MPR kippt sonst bei voller Adoption in Coverage-Verlust — in der Studie belegt).

**Validierung:** vorhanden — v3-Realdaten-Sim + Adoptions-Sweep-Studie (`docs/MHR/study/study_sim.py`). Vor Hardware-Tests Mechanismen dort gegenrechnen; Safety-Invariante (Lieferquote ≥ Baseline, Airtime ≤ Baseline) je Adoptionsgrad prüfen.

## Existierende Forks (Stand der Recherche)

Keiner setzt metrik-/proaktives Routing um. `mattzzw/MeshCore-Evo` und `weebl2000/meshcore` verbessern nur Flood-Advert-Handling / TX-Duty-Cycle / `denyf`. Es gibt einen Zephyr-Port mit adaptivem Contention-Window. Das Feld ist offen.
