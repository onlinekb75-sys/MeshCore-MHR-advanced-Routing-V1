# MHR – Änderungen gegenüber Upstream (Phase 0 + Phase 1)

Konservativ, reversibel, rückwärtskompatibel. Alle Stellen im Code mit `// MHR:` markiert.
Build verifiziert: `pio run -e heltec_v4_repeater` → SUCCESS (RAM 3,4 %, Flash 18,1 %).

## Phase 0

### Patch 1 — SNR-gewichtete Flutung als Default an
- Datei: `examples/simple_repeater/MyMesh.cpp`
- Vorher: `_prefs.rx_delay_base = 0.0f;` (SNR-Gewichtung aus)
- Nachher: `_prefs.rx_delay_base = 10.0f;`
- Wirkung: Knoten mit starkem Empfang senden Flood-Kopien zuerst und unterdrücken redundante Umweg-Kopien (Dedup via hasSeen). Flood folgt der Linkqualität statt reinem Zufall.
- Reversibel zur Laufzeit ohne Reflash: `set rxdelay 0`. Wert 0–20 einstellbar (`set rxdelay <n>`).
- Greift im Repeater-Build.

### Patch 2 — Pfad nur bei Verbesserung übernehmen
- Datei: `src/helpers/BaseChatMesh.cpp`, `onContactPathRecv()`
- Vorher: jeder empfangene `out_path` überschreibt den gecachten bedingungslos.
- Nachher: Übernahme nur, wenn neue Hop-Zahl `(out_path_len & 0x3F)` ≤ aktueller, oder noch kein Pfad bekannt (`OUT_PATH_UNKNOWN`).
- Wirkung: ein später eintreffender längerer Detour-Pfad verdrängt einen guten kurzen nicht mehr. Bei nur einem angebotenen Pfad identisch zu Upstream → nie schlechter.
- Sicherheit: bricht ein Pfad, setzt `resetPathTo()` `out_path_len = OUT_PATH_UNKNOWN` → nächster Pfad wird wieder angenommen, kein Festhängen.
- Greift in Companion-/Chat-Builds (BaseChatMesh).

## Phase 1 (neu)

Lokale, additive Verbesserungen — weiterhin **kein** Paketformat-Eingriff, **keine** Dedup-Änderung
→ vollständig mixed-firmware-kompatibel und „nie schlechter als Upstream". Adversarial reviewt
(Persistenz, Integer-Arithmetik, EWMA, CLI) — keine Bugs gefunden.

### Patch 3 — SNR-gewichtetes TX-Retransmit-Delay (qualitätsgeleitete Flutung, TX-Seite)
- Datei: `examples/simple_repeater/MyMesh.cpp`, `getRetransmitDelay()`
- Neuer persistierter Parameter `tx_snr_weight` (float 0..1, Default **0.5**), ergänzt am Ende von
  `NodePrefs` (`src/helpers/CommonCLI.h`) und in `loadPrefs`/`savePrefs` am Datei-Offset 291
  (forward-/backward-kompatibel: alte Config-Dateien behalten den Default).
- Wirkung: Eine Flood-Kopie mit **starkem SNR** (meist kurzer, direkter Link) zieht ihre zufällige
  Backoff-Zeit aus einem zu 0 hin geschrumpften Fenster → sendet früher und unterdrückt langsamere
  Umweg-Kopien downstream via `hasSeen()`-Dedup. Die Zufallskomponente bleibt erhalten
  (Fenster nie < `t+1`) → keine synchronen Kollisionen unter gleich starken Nachbarn.
- Ergänzt Phase-0-Patch 1 (RX-Seite `rx_delay_base`) um die **TX-Seite**: Flutung folgt nun an
  beiden Stufen der Linkqualität statt nur am Empfang.
- Reversibel zur Laufzeit ohne Reflash: `set txsnrweight 0` (= exakt Upstream). `get txsnrweight` liest aus.
- Greift im Repeater-Build.

### Patch 4 — EWMA-geglättete Nachbar-SNR (L0 Link-Sensing)
- Datei: `examples/simple_repeater/MyMesh.cpp`, `putNeighbour()`
- Vorher: `neighbour->snr` wurde bei jedem Advert mit dem letzten Rohwert überschrieben.
- Nachher: exponentiell geglättet (EWMA, α = 1/4); ein frisch belegter/verdrängter Slot wird mit
  dem Rohwert geseedet.
- Wirkung: stabile Linkqualitäts-Schätzung statt verrauschtem Momentanwert — Fundament für eine
  spätere ETX-Metrik (Phase 1+) und bessere `neighbors`-Auswertung. Kein int8-Overflow
  (gewichtetes Mittel zweier int8 bleibt in int8).

## Bewusst NICHT geändert
- `flood.max` (Default 64): netz­abhängig, besser per CLI setzen (`set flood.max <n>`).
- Kein neues Paketformat, keine Metrik im Paket, kein Backbone, kein echtes Best-of-N am Ziel —
  Letzteres würde die `hasSeen()`-Dedup aufbohren (Risiko von Nachrichten-Duplikaten) und gehört
  in eine eigene, gründlich getestete Stufe. Siehe Design-Dokumente (Phase 2).

## Validierung (Simulation)
- `docs/MHR/sim/mhr_sim_v2.py` — Stress-Szenarien auf der realen 25-Knoten-Topologie:
  - **Churn**: Pfad-Flattern 78 % → 17 %, bessere Lieferquote, kürzere Pfade.
  - **Linkausfall (0–30 %)**: Re-Discovery ≤ 1,6 % statt bis 49,5 %, Airtime nahezu flach.
  - **Partition**: −98 % Airtime (Baseline läuft in Endlos-Flood, MHR flutet einmal + Fallback).
  - Ergebnisse in `sim/sim_results_v2.json`, Plots `fig_v2_*.png`. Reproduzierbar (Seed 42).
- `docs/MHR/sim/mhr_sim_real_v3.py` — **auf echten CoreScope-Live-Daten** (109.980 Pakete, 1962 Knoten) kalibriert. GEMESSEN: realer Umweg-Median **2,1×** (78,8 % der Pakete > 1,5×) — belegt das „first-wins"-Problem mit Produktionsdaten; das alte Log-Distance-SNR-Modell trägt für dieses Netz nicht (real PLE ≈ 0,4). Datensatz reproduzierbar via `mhr_collect_corescope.py`.
- `docs/MHR/study/MeshCore_Routing_Study.md` — **Mechanismus-Studie mit Adoptions-Sweep** (1 Knoten → alle) auf der realen 776-Knoten-Topologie, Safety-Invariante „nie schlechter als Baseline". Ergebnis: hop-basierte Pfadwahl (Best-of-N nach Hops, Hop-Delay) + `flood.max` 15 sind ab 1 Knoten safe; Airtime-Suppression (Cancel/Counter/MPR) braucht adaptive Redundanz-Bedingung. Verschiebt die Priorität von SNR weg hin zur Hop-Zahl.
