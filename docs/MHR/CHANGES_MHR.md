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

## Stufe A (aus der Realdaten-Studie) — hop-basierte Pfadwahl

Begründet durch `docs/MHR/study/MeshCore_Routing_Study.md`: SNR ist ein schwacher Hebel, die
**Hop-Zahl** ist verlässlicher. Beide Patches sind rein lokal, „nie schlechter als Upstream", ab
einem einzelnen Knoten sicher und monoton — ideal für langsame Verbreitung im Mischbetrieb.

### Patch 5 — Hop-gewichtetes Rebroadcast-Delay (PRIMÄRER Hebel)
- Datei: `examples/simple_repeater/MyMesh.cpp`, `getRetransmitDelay()`
- Neuer persistierter Parameter `tx_hop_weight` (float 0..1, Default **0.6**), ergänzt am Ende von
  `NodePrefs` (`CommonCLI.h`) + Persistenz-Offset 295 (forward-/backward-kompatibel) + CLI
  `set/get txhopweight`.
- Wirkung: Eine Flood-Kopie mit **weniger akkumulierten Hops** (= verlässliches Signal für kurzen
  Pfad) zieht ihre zufällige Backoff-Zeit aus einem zu 0 hin geschrumpften Fenster → sendet früher
  und unterdrückt langsamere Umweg-Kopien via `hasSeen()`-Dedup. Der Hop-Term **dominiert** den
  bisherigen SNR-Term (kombiniert, gecappt; Fenster nie < `t+1` → keine synchronen Kollisionen).
- `tx_hop_weight == 0` **und** `tx_snr_weight == 0` reproduzieren Upstream exakt. Hop-Horizont
  `MHR_HOP_HORIZON = 12` (≈ realer Netzdurchmesser). Reversibel: `set txhopweight 0`.

### Patch 6 — `flood.max`-Default 64 → 15 (datenbelegt)
- Datei: `examples/simple_repeater/MyMesh.cpp` (`flood_max` Default)
- Realer Netzdurchmesser: Median 10, P90 18 Hops → 64 ist massiv überdimensioniert. Die Studie
  zeigte: **12 ist zu aggressiv** (kappt ferne Zustellungen bei voller Adoption), **15 bleibt über
  alle Adoptionsgrade sicher**.
- `flood.max` ist ein rein **lokales Forward-Limit** (`allowPacketForward`): Stock-Knoten (64)
  tragen längere Pfade weiter → ein einzelner MHR-Knoten ist nie schlechter, tötet aber lokal
  Fern-Umweg-Kopien. Netzabhängig, weiter per CLI einstellbar: `set flood.max <n>`.

## Stufe B — redundanz-gesicherte Flood-Suppression (im Code, **default-AUS**)

Implementiert, aber **dormant** (`supp_enable = 0`): bei AUS verhält sich der Knoten **exakt wie
Stufe A** (adversarial reviewt — kein einziger Zusatzeffekt im Sende-/Empfangspfad). Erst nach
Bench-Test per CLI aktivieren (`set supp.enable 1`). Design + Validierung:
`docs/MHR/study/Suppression_Design.md`, `SUPPRESSION_VALIDATION.md`.

### Patch 7 — 5-Guard-Suppression + passives 2-Hop-Lernen
- Dateien: `examples/simple_repeater/MyMesh.{h,cpp}`, `src/Dispatcher.{h,cpp}` (neuer virtueller Hook
  `allowFloodRebroadcast()`, Default `true` → andere Builds bit-identisch), `src/helpers/CommonCLI.{h,cpp}`.
- Logik: ein Repeater unterdrückt seinen Flood-Rebroadcast NUR, wenn alle Guards halten —
  **G1** degree ≥ `supp_min_degree`, **G2** ≥ `supp_k_cover` verschiedene Cover-Sender im Backoff
  gehört, **G3** jeder eigene Nachbar ist durch einen Cover gedeckt (via passiv gelernter, frische-
  gegateter 2-Hop-Tabelle — die *tragende* Schicht gegen „letzten Pfad kappen"), **G4** Cover-SNR ≥
  `supp_snr_floor`, **G5** Prob `supp_prob`. Default-Aktion = senden (nie schlechter).
- Neue persistierte Prefs @299–303: `supp_enable`(0), `supp_min_degree`(4), `supp_k_cover`(2),
  `supp_snr_floor`(-6 dB), `supp_prob`(80 %). CLI `set/get supp.enable|mindeg|kcover|snrfloor|prob`.
- Fixe Tabellen (~0,8 KB, keine dyn. Allokation). Validiert: Lieferquote ≥ Baseline über den
  GESAMTEN Adoptions-Sweep, −12…15 % Airtime bei hoher Adoption. Bench-Test offen (Cover-Timing in
  HW, 2-Hop-Konvergenz, Frische-Gating).

## Adaptiver Selbst-Regler — geprüft und VERWORFEN (NO-GO)
Ein langsamer (1–2 h) Regler, der `supp_prob` an die lokale Umgebung anpasst, wurde entworfen und
simuliert (`docs/MHR/study/Adaptive_Controller_Design.md`). Ergebnis: oszilliert nicht, bringt aber
nur **+0,76 pp** Airtime über den statischen sicheren Satz (< 2 pp Schwelle) und ist minimal weniger
strikt-safe → **nicht codiert** (Qualität/Stabilität vor letzter Optimierung). Der statische sichere
Satz ist die Empfehlung.

## Bewusst NICHT geändert
- Kein neues Paketformat, keine Metrik im Paket, kein Backbone, kein echtes Best-of-N am Ziel —
  Letzteres würde die `hasSeen()`-Dedup aufbohren (Risiko von Nachrichten-Duplikaten) und gehört
  in eine eigene, gründlich getestete Stufe (siehe Studie Stufe A „Best-of-N am Ziel").

## Validierung (Simulation)
- `docs/MHR/sim/mhr_sim_v2.py` — Stress-Szenarien auf der realen 25-Knoten-Topologie:
  - **Churn**: Pfad-Flattern 78 % → 17 %, bessere Lieferquote, kürzere Pfade.
  - **Linkausfall (0–30 %)**: Re-Discovery ≤ 1,6 % statt bis 49,5 %, Airtime nahezu flach.
  - **Partition**: −98 % Airtime (Baseline läuft in Endlos-Flood, MHR flutet einmal + Fallback).
  - Ergebnisse in `sim/sim_results_v2.json`, Plots `fig_v2_*.png`. Reproduzierbar (Seed 42).
- `docs/MHR/sim/mhr_sim_real_v3.py` — **auf echten CoreScope-Live-Daten** (109.980 Pakete, 1962 Knoten) kalibriert. GEMESSEN: realer Umweg-Median **2,1×** (78,8 % der Pakete > 1,5×) — belegt das „first-wins"-Problem mit Produktionsdaten; das alte Log-Distance-SNR-Modell trägt für dieses Netz nicht (real PLE ≈ 0,4). Datensatz reproduzierbar via `mhr_collect_corescope.py`.
- `docs/MHR/study/MeshCore_Routing_Study.md` — **Mechanismus-Studie mit Adoptions-Sweep** (1 Knoten → alle) auf der realen 776-Knoten-Topologie, Safety-Invariante „nie schlechter als Baseline". Ergebnis: hop-basierte Pfadwahl (Best-of-N nach Hops, Hop-Delay) + `flood.max` 15 sind ab 1 Knoten safe; Airtime-Suppression (Cancel/Counter/MPR) braucht adaptive Redundanz-Bedingung. Verschiebt die Priorität von SNR weg hin zur Hop-Zahl.
