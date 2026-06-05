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

## Best-of-N am Ziel — hop-/SNR-beste Pfadwahl statt „first wins" (im Code)

### Patch 8 — Sammelfenster am Ziel, kürzesten Pfad zurückmelden
- Dateien: `src/Mesh.{h,cpp}` (gemeinsame Basis → greift für Companion/Room/Repeater-Ziele),
  `src/helpers/CommonCLI.{h,cpp}`, Default-Aktivierung in `examples/simple_repeater/MyMesh.cpp`.
- Statt sofort den *zuerst* eingetroffenen Flood-Pfad per `createPathReturn` zurückzumelden, sammelt
  das Ziel kurz mehrere Kopien (fixe Tabelle `_bofn[8]`) und meldet den **kürzesten Pfad (Hops, dann
  SNR als Tiebreaker)** zurück. v4-belegt: Hop dominiert, SNR ist guter Reliability-Tiebreak.
- **Dedup-sicher (adversarial reviewt, kein Blocker):** die Payload wird weiterhin GENAU EINMAL im
  Erstempfang zugestellt; `hasSeen()`/Dedup unverändert; nur der reziproke Path-Return wird um EIN
  Fenster (Default 1500 ms, einmalig je Pfadaufbau) verzögert. Bei einer Kopie identisch zu first-wins.
- Mixed-firmware-safe: kein Paketformat-Eingriff (Standard-`PAYLOAD_TYPE_PATH`-Return). Prefs
  `bofn_enable`(Default 1 im Repeater), `bofn_window_ms`(1500) @304/305, CLI `set/get bofn.enable|window`.
  In der Basis default-AUS (andere Builds unverändert). ~0,9 KB fixe Tabelle.

## Adaptiver Selbst-Regler — geprüft und VERWORFEN (NO-GO)
Ein langsamer (1–2 h) Regler, der `supp_prob` an die lokale Umgebung anpasst, wurde entworfen und
simuliert (`docs/MHR/study/Adaptive_Controller_Design.md`). Ergebnis: oszilliert nicht, bringt aber
nur **+0,76 pp** Airtime über den statischen sicheren Satz (< 2 pp Schwelle) und ist minimal weniger
strikt-safe → **nicht codiert** (Qualität/Stabilität vor letzter Optimierung). Der statische sichere
Satz ist die Empfehlung.

## Phase 2 — proaktiver Regions-Backbone (im Code, **default-AUS**, Bench-gegated)

Konvergenz-Gate bestanden (`docs/MHR/study/Phase2_Convergence_Validation.md`: GO), danach codiert.

### Patch 9 — DV-Control-Plane (Backbone)
- Dateien: neu `examples/simple_repeater/Backbone.{h,cpp}`; `src/Packet.h` (`PAYLOAD_TYPE_DV=0x0C`);
  `src/Mesh.{h,cpp}` (virtueller `onDVDataRecv` + zero-hop DV-Dispatch + `createDVData`);
  `src/helpers/CommonCLI.{h,cpp}` (Prefs); `examples/simple_repeater/MyMesh.{h,cpp}` (Verdrahtung).
- Verteiltes DV: per-Ziel-Seqno + **origin-unabhängige Babel-Feasibility** (Loop-Breaker),
  Feasible-Successor-Backup, Hold-down + Route-Poisoning, Trigger-on-change (rate-limitiert),
  periodische Zero-Hop-Annonce. ETX aus EWMA-SNR. Region aus `region_map`.
- **Mixed-firmware-safe:** DV ist ein **ignorierbarer, zero-hop** Payload-Typ (0x0C) → Stock-Knoten
  verwerfen ihn ohne Reflood. Kein bestehender Typ/Format berührt.
- **default-AUS = bit-identisch inert** (`bb_enable=0`): kein DV-Versand/-Empfang, kein Timer, keine
  Routenänderung. Prefs `bb_enable`(0), `bb_period_s`(600), `bb_holddown_s`(1200) @307/308/310,
  CLI `set/get bb.enable|bb.period|bb.holddown`. ~1,25 KB fixe Tabellen.
- Adversarial reviewt: Auslieferungszustand sicher; ein gefundener Loop-BLOCKER (Seqno pro-Annoncierer
  statt pro-Ziel) + 2 WARNs **gefixt** (B1/W1/W2), Build grün auf repeater/room_server/companion.
- **Zurückgestellt (bewusst):** das Daten-Plane-Short-Circuit (Backbone-Unicast statt Flood) ist NICHT
  verdrahtet — es gehört an den Endpunkt und würde Dedup/Pfad-Semantik berühren (Hochrisiko). Aktiviert
  man `bb_enable`, ändert sich daher NUR der Control-Plane; Daten laufen weiter über Flood-and-cache →
  „nie schlechter" gilt. `lookupRoute()` ist bereit für diese spätere Integration.
- ⚠️ `bb.enable 1` erst nach **Bench-Test** (`docs/MHR/BENCH_TEST_PLAN.md` §4).

## Bewusst NICHT geändert / noch offen
- Kein neues Paketformat bei bestehenden Typen, keine Metrik im bestehenden Paket.
- Phase-2 **Daten-Plane-Short-Circuit** (Endpunkt-Integration) — eigene, getestete Stufe (Dedup-Risiko).

## Validierung (Simulation)
- `docs/MHR/sim/mhr_sim_v2.py` — Stress-Szenarien auf der realen 25-Knoten-Topologie:
  - **Churn**: Pfad-Flattern 78 % → 17 %, bessere Lieferquote, kürzere Pfade.
  - **Linkausfall (0–30 %)**: Re-Discovery ≤ 1,6 % statt bis 49,5 %, Airtime nahezu flach.
  - **Partition**: −98 % Airtime (Baseline läuft in Endlos-Flood, MHR flutet einmal + Fallback).
  - Ergebnisse in `sim/sim_results_v2.json`, Plots `fig_v2_*.png`. Reproduzierbar (Seed 42).
- `docs/MHR/sim/mhr_sim_real_v3.py` — **auf echten CoreScope-Live-Daten** (109.980 Pakete, 1962 Knoten) kalibriert. GEMESSEN: realer Umweg-Median **2,1×** (78,8 % der Pakete > 1,5×) — belegt das „first-wins"-Problem mit Produktionsdaten; das alte Log-Distance-SNR-Modell trägt für dieses Netz nicht (real PLE ≈ 0,4). Datensatz reproduzierbar via `mhr_collect_corescope.py`.
- `docs/MHR/study/MeshCore_Routing_Study.md` — **Mechanismus-Studie mit Adoptions-Sweep** (1 Knoten → alle) auf der realen 776-Knoten-Topologie, Safety-Invariante „nie schlechter als Baseline". Ergebnis: hop-basierte Pfadwahl (Best-of-N nach Hops, Hop-Delay) + `flood.max` 15 sind ab 1 Knoten safe; Airtime-Suppression (Cancel/Counter/MPR) braucht adaptive Redundanz-Bedingung. Verschiebt die Priorität von SNR weg hin zur Hop-Zahl.

---
## 🇬🇧 English Translation

# MHR – Changes vs. Upstream (Phase 0 + Phase 1)

Conservative, reversible, backward-compatible. All changed locations in the code are marked with `// MHR:`.
Build verified: `pio run -e heltec_v4_repeater` → SUCCESS (RAM 3.4 %, Flash 18.1 %).

## Phase 0

### Patch 1 — SNR-weighted flooding enabled by default
- File: `examples/simple_repeater/MyMesh.cpp`
- Before: `_prefs.rx_delay_base = 0.0f;` (SNR weighting off)
- After: `_prefs.rx_delay_base = 10.0f;`
- Effect: Nodes with strong reception transmit flood copies first and suppress redundant detour copies (dedup via hasSeen). Flooding follows link quality instead of pure chance.
- Reversible at runtime without reflash: `set rxdelay 0`. Value adjustable 0–20 (`set rxdelay <n>`).
- Active in the repeater build.

### Patch 2 — Adopt path only on improvement
- File: `src/helpers/BaseChatMesh.cpp`, `onContactPathRecv()`
- Before: every received `out_path` unconditionally overwrites the cached one.
- After: adoption only when the new hop count `(out_path_len & 0x3F)` ≤ current, or no path is known yet (`OUT_PATH_UNKNOWN`).
- Effect: a later-arriving longer detour path no longer displaces a good short one. With only one offered path, behavior is identical to upstream → never worse.
- Safety: if a path breaks, `resetPathTo()` sets `out_path_len = OUT_PATH_UNKNOWN` → the next path is accepted again, no sticking.
- Active in companion/chat builds (BaseChatMesh).

## Phase 1 (new)

Local, additive improvements — still **no** packet-format change, **no** dedup change
→ fully mixed-firmware-compatible and "never worse than upstream". Adversarially reviewed
(persistence, integer arithmetic, EWMA, CLI) — no bugs found.

### Patch 3 — SNR-weighted TX retransmit delay (quality-guided flooding, TX side)
- File: `examples/simple_repeater/MyMesh.cpp`, `getRetransmitDelay()`
- New persisted parameter `tx_snr_weight` (float 0..1, default **0.5**), appended at the end of
  `NodePrefs` (`src/helpers/CommonCLI.h`) and in `loadPrefs`/`savePrefs` at file offset 291
  (forward-/backward-compatible: old config files retain the default).
- Effect: A flood copy with **strong SNR** (usually a short, direct link) draws its random
  backoff time from a window shrunk towards 0 → transmits earlier and suppresses slower
  detour copies downstream via `hasSeen()` dedup. The random component is preserved
  (window never < `t+1`) → no synchronous collisions among equally strong neighbors.
- Extends Phase-0 Patch 1 (RX side `rx_delay_base`) with the **TX side**: flooding now follows
  link quality at both stages instead of only at reception.
- Reversible at runtime without reflash: `set txsnrweight 0` (= exactly upstream). `get txsnrweight` reads it back.
- Active in the repeater build.

### Patch 4 — EWMA-smoothed neighbor SNR (L0 link sensing)
- File: `examples/simple_repeater/MyMesh.cpp`, `putNeighbour()`
- Before: `neighbour->snr` was overwritten with the latest raw value on every advert.
- After: exponentially smoothed (EWMA, α = 1/4); a freshly allocated/evicted slot is seeded with
  the raw value.
- Effect: stable link-quality estimate instead of a noisy instantaneous value — foundation for a
  later ETX metric (Phase 1+) and better `neighbors` evaluation. No int8 overflow
  (weighted mean of two int8 values stays within int8).

## Level A (from the real-data study) — hop-based path selection

Justified by `docs/MHR/study/MeshCore_Routing_Study.md`: SNR is a weak lever, the
**hop count** is more reliable. Both patches are purely local, "never worse than upstream", safe
and monotone from a single node — ideal for slow rollout in mixed-firmware operation.

### Patch 5 — Hop-weighted rebroadcast delay (PRIMARY lever)
- File: `examples/simple_repeater/MyMesh.cpp`, `getRetransmitDelay()`
- New persisted parameter `tx_hop_weight` (float 0..1, default **0.6**), appended at the end of
  `NodePrefs` (`CommonCLI.h`) + persistence offset 295 (forward-/backward-compatible) + CLI
  `set/get txhopweight`.
- Effect: A flood copy with **fewer accumulated hops** (= reliable signal for a short path)
  draws its random backoff time from a window shrunk towards 0 → transmits earlier and suppresses
  slower detour copies via `hasSeen()` dedup. The hop term **dominates** the previous SNR term
  (combined, capped; window never < `t+1` → no synchronous collisions).
- `tx_hop_weight == 0` **and** `tx_snr_weight == 0` reproduce upstream exactly. Hop horizon
  `MHR_HOP_HORIZON = 12` (≈ real network diameter). Reversible: `set txhopweight 0`.

### Patch 6 — `flood.max` default 64 → 15 (data-backed)
- File: `examples/simple_repeater/MyMesh.cpp` (`flood_max` default)
- Real network diameter: median 10, P90 18 hops → 64 is massively oversized. The study
  showed: **12 is too aggressive** (cuts off distant deliveries at full adoption), **15 remains safe
  across all adoption levels**.
- `flood.max` is a purely **local forward limit** (`allowPacketForward`): stock nodes (64)
  carry longer paths further → a single MHR node is never worse, but locally kills
  long-range detour copies. Network-dependent, further adjustable via CLI: `set flood.max <n>`.

## Level B — redundancy-guarded flood suppression (in code, **default-OFF**)

Implemented but **dormant** (`supp_enable = 0`): when OFF, the node behaves **exactly like
Level A** (adversarially reviewed — not a single additional effect in the send/receive path). Only
activate after bench-testing via CLI (`set supp.enable 1`). Design + validation:
`docs/MHR/study/Suppression_Design.md`, `SUPPRESSION_VALIDATION.md`.

### Patch 7 — 5-guard suppression + passive 2-hop learning
- Files: `examples/simple_repeater/MyMesh.{h,cpp}`, `src/Dispatcher.{h,cpp}` (new virtual hook
  `allowFloodRebroadcast()`, default `true` → other builds are bit-identical), `src/helpers/CommonCLI.{h,cpp}`.
- Logic: a repeater suppresses its flood rebroadcast ONLY when all guards hold —
  **G1** degree ≥ `supp_min_degree`, **G2** ≥ `supp_k_cover` distinct cover-senders heard during backoff,
  **G3** every own neighbor is covered by a cover (via passively learned, freshness-gated 2-hop table —
  the *load-bearing* layer against "cutting the last path"), **G4** cover SNR ≥
  `supp_snr_floor`, **G5** probability `supp_prob`. Default action = transmit (never worse).
- New persisted prefs @299–303: `supp_enable`(0), `supp_min_degree`(4), `supp_k_cover`(2),
  `supp_snr_floor`(-6 dB), `supp_prob`(80 %). CLI `set/get supp.enable|mindeg|kcover|snrfloor|prob`.
- Fixed tables (~0.8 KB, no dynamic allocation). Validated: delivery rate ≥ baseline across the
  ENTIRE adoption sweep, −12…15 % airtime at high adoption. Bench test pending (cover timing in
  HW, 2-hop convergence, freshness gating).

## Best-of-N at destination — hop-/SNR-best path selection instead of "first wins" (in code)

### Patch 8 — Collection window at destination, report shortest path back
- Files: `src/Mesh.{h,cpp}` (shared base → applies to companion/room/repeater targets),
  `src/helpers/CommonCLI.{h,cpp}`, default activation in `examples/simple_repeater/MyMesh.cpp`.
- Instead of immediately reporting back the *first-arrived* flood path via `createPathReturn`, the
  destination briefly collects multiple copies (fixed table `_bofn[8]`) and reports the **shortest
  path (hops, then SNR as tiebreaker)** back. v4-backed: hop dominates, SNR is a good reliability tiebreaker.
- **Dedup-safe (adversarially reviewed, no blocker):** the payload is still delivered EXACTLY ONCE on
  first reception; `hasSeen()`/dedup unchanged; only the reciprocal path-return is delayed by ONE
  window (default 1500 ms, once per path setup). With a single copy, identical to first-wins.
- Mixed-firmware-safe: no packet format change (standard `PAYLOAD_TYPE_PATH` return). Prefs
  `bofn_enable`(default 1 in repeater), `bofn_window_ms`(1500) @304/305, CLI `set/get bofn.enable|window`.
  Default-OFF in the base (other builds unchanged). ~0.9 KB fixed table.

## Adaptive self-controller — reviewed and REJECTED (NO-GO)
A slow (1–2 h) controller that adapts `supp_prob` to the local environment was designed and
simulated (`docs/MHR/study/Adaptive_Controller_Design.md`). Result: does not oscillate, but delivers
only **+0.76 pp** airtime over the static safe set (< 2 pp threshold) and is marginally less
strictly safe → **not coded** (quality/stability before last-mile optimization). The static safe
set is the recommendation.

## Phase 2 — proactive regional backbone (in code, **default-OFF**, bench-gated)

Convergence gate passed (`docs/MHR/study/Phase2_Convergence_Validation.md`: GO), then coded.

### Patch 9 — DV control plane (backbone)
- Files: new `examples/simple_repeater/Backbone.{h,cpp}`; `src/Packet.h` (`PAYLOAD_TYPE_DV=0x0C`);
  `src/Mesh.{h,cpp}` (virtual `onDVDataRecv` + zero-hop DV dispatch + `createDVData`);
  `src/helpers/CommonCLI.{h,cpp}` (prefs); `examples/simple_repeater/MyMesh.{h,cpp}` (wiring).
- Distributed DV: per-destination seqno + **origin-independent Babel feasibility** (loop breaker),
  feasible-successor backup, hold-down + route poisoning, trigger-on-change (rate-limited),
  periodic zero-hop announcement. ETX from EWMA-SNR. Region from `region_map`.
- **Mixed-firmware-safe:** DV is an **ignorable, zero-hop** payload type (0x0C) → stock nodes
  discard it without re-flooding. No existing type/format touched.
- **default-OFF = bit-identically inert** (`bb_enable=0`): no DV send/receive, no timer, no
  route changes. Prefs `bb_enable`(0), `bb_period_s`(600), `bb_holddown_s`(1200) @307/308/310,
  CLI `set/get bb.enable|bb.period|bb.holddown`. ~1.25 KB fixed tables.
- Adversarially reviewed: delivery state safe; one found loop-BLOCKER (seqno per-announcer
  instead of per-destination) + 2 WARNs **fixed** (B1/W1/W2), build green on repeater/room_server/companion.
- **Deliberately deferred:** the data-plane short-circuit (backbone unicast instead of flood) is NOT
  wired — it belongs at the endpoint and would touch dedup/path semantics (high risk). Enabling
  `bb_enable` therefore changes ONLY the control plane; data continues to run via flood-and-cache →
  "never worse" holds. `lookupRoute()` is ready for this later integration.
- ⚠️ `bb.enable 1` only after **bench test** (`docs/MHR/BENCH_TEST_PLAN.md` §4).

## Deliberately NOT changed / still open
- No new packet format for existing types, no metric in existing packets.
- Phase-2 **data-plane short-circuit** (endpoint integration) — its own tested stage (dedup risk).

## Validation (Simulation)
- `docs/MHR/sim/mhr_sim_v2.py` — stress scenarios on the real 25-node topology:
  - **Churn**: path flapping 78 % → 17 %, better delivery rate, shorter paths.
  - **Link failure (0–30 %)**: re-discovery ≤ 1.6 % instead of up to 49.5 %, airtime nearly flat.
  - **Partition**: −98 % airtime (baseline runs in endless flood, MHR floods once + fallback).
  - Results in `sim/sim_results_v2.json`, plots `fig_v2_*.png`. Reproducible (seed 42).
- `docs/MHR/sim/mhr_sim_real_v3.py` — **calibrated on real CoreScope live data** (109,980 packets, 1,962 nodes). MEASURED: real detour median **2.1×** (78.8 % of packets > 1.5×) — proves the "first-wins" problem with production data; the old log-distance SNR model does not apply to this network (real PLE ≈ 0.4). Dataset reproducible via `mhr_collect_corescope.py`.
- `docs/MHR/study/MeshCore_Routing_Study.md` — **mechanism study with adoption sweep** (1 node → all) on the real 776-node topology, safety invariant "never worse than baseline". Result: hop-based path selection (Best-of-N by hops, hop delay) + `flood.max` 15 are safe from 1 node; airtime suppression (cancel/counter/MPR) requires an adaptive redundancy condition. Shifts priority away from SNR towards hop count.
