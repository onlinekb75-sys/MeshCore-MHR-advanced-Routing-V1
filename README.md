# MeshCore-MHR — Advanced Routing V1

A **fork of [meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT) V1.15 that makes path-finding more robust and cuts the airtime wasted on random flood detours.

**MHR** stands for **M**eshCore **H**ybrid **R**outing.

📖 **Docs overview & entry point:** [`docs/MHR/`](docs/MHR/README.md) — what MHR does, how it works, all studies/validations + bench-test plan.

> ⚠️ **Experimental — untested on hardware.** Active (default-on): Phase 0, Stage A and Best-of-N — all "never worse than upstream". **Stage B** (suppression) and **Phase 2** (DV backbone) are in the code but **default-off** and should only be enabled after bench testing (`docs/MHR/BENCH_TEST_PLAN.md`). Flash to a **spare/bench device first**, not to production repeaters. Full status: see "Optimization layer status" below.

---

## What is MHR-MeshCore?

A fork of **[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT) that makes **path-finding** in the LoRa mesh more robust and cuts the **airtime** wasted on random flood detours. MeshCore is a hybrid mesh-routing protocol for LoRa radios (ESP32/nRF52, C++/PlatformIO).

## The problem

MeshCore uses **no** metric-based routing. The first message to a contact is flooded, the resulting path is cached, and **every following packet is pinned to that one path**. Crucially, the winner is **not the shortest or best path, but whichever flood copy happens to reach the destination first** ("first packet wins"), while signal-quality-weighted propagation is off by default. From analysis of real network data: **~60 % of path setups end up on a detour** — burning airtime, the real bottleneck on a shared half-duplex radio channel.

## The solution

MHR aligns flood propagation with **link quality (SNR)** at both stages:
- **RX side:** nodes with strong reception rebroadcast first, suppressing redundant detour copies (on by default).
- **TX side:** a copy with strong SNR (usually a short, direct link) draws its random backoff from a window shrunk toward zero → it rebroadcasts earlier and "wins" the path.
- **Path adoption:** a later-arriving **longer** detour no longer overwrites a good short path.
- **EWMA link sensing:** smoothed neighbour SNR as a stable link-quality estimate — the foundation for a future ETX metric.

## Why it's safe

All changes are **local, additive and reversible** (toggle at runtime via CLI, no reflash). **No packet-format change, no change to duplicate detection** → MHR runs in a **mixed network alongside unmodified upstream nodes** and is **never worse than the original** (with the parameters disabled it behaves bit-identically to upstream).

## Evidence

Simulation on a **real 25-node topology**: **−82 % airtime**, detours markedly reduced. The gains hold under stress — path-flapping **78 % → 17 %**, link-failure re-discovery **≤ 1.6 % vs. up to 49.5 %**, network-partition airtime **−98 %** instead of endless flooding. Build verified; GitHub Actions builds the flashable **Heltec V4 firmware (.bin)** automatically. Details & evidence in `docs/MHR/`.

---

## What was changed

Minimal, backward-compatible, reversible patches — all marked `// MHR:` in the code, no packet-format change, no dedup change (mixed-firmware-safe). Build verified (`pio run -e heltec_v4_repeater` → SUCCESS). Full details in `docs/MHR/CHANGES_MHR.md`.

**Phase 0**
1. **SNR-weighted flooding on by default** (`examples/simple_repeater/MyMesh.cpp`): `rx_delay_base` from `0.0` to `10.0`. Strong (short) links rebroadcast first and suppress detour copies. **Reversible:** `set rxdelay 0`.
2. **Path adoption only on improvement** (`src/helpers/BaseChatMesh.cpp`): a later-arriving **longer** path no longer overwrites a good short one. Never worse than upstream. **Self-healing:** a detected failure counter + 30 min staleness threshold allow a longer working path to replace a pinned dead one (RAM-only, no persistence change).

**Phase 1**
3. **SNR-weighted TX retransmit delay** (`getRetransmitDelay`): extends patch 1 to the send side — strong receivers rebroadcast flood copies earlier (from a randomness-preserving shrunk window), weak ones later. New reversible parameter `tx_snr_weight` (default 0.5, `set txsnrweight 0` = upstream).
4. **EWMA-smoothed neighbour SNR** (`putNeighbour`): stable link-quality estimate (L0 link-sensing) instead of a noisy instantaneous value — foundation for ETX.

**Stage A+ / B / Phase 2** (all adversarially reviewed)
5. **Best-of-N at the destination** (`src/Mesh.cpp`): shortest path (hops, then SNR) instead of "first wins" — dedup-safe (payload delivered exactly once). `bofn.enable` (repeater default-on).
6. **`flood.max` 64 → adaptive** (data-backed): fixed 15 was below the measured network P90 of 18 hops; replaced by an adaptive ceiling that floats between observed diameter + margin and the user ceiling (default 32). Pure local forward limit.
7. **Stage B — guarded suppression** (`supp.enable`, **default-off**): redundancy-secured rebroadcast suppression (5 guards + passive 2-hop learning).
8. **Phase 2 — DV backbone** (`bb.enable`, **default-off**): proactive control plane (Babel-feasibility, convergence-gate GO); ignorable zero-hop payload type.

> Full status (active vs. default-off) + validation → section **"Optimization layer status"** below. Complete patch list 1–9: `docs/MHR/CHANGES_MHR.md`.

---

## Building the firmware

### Option A — automatic via GitHub Actions (recommended)
After pushing to your repo, `.github/workflows/build.yml` builds the firmware automatically. Under **Actions → Build MHR firmware → Artifacts** you'll find `heltec_v4_repeater-firmware.bin` (+ `-factory.bin`). Additional targets can be uncommented in the workflow file.

### Option B — locally with PlatformIO
```bash
pip install platformio
pio run -e heltec_v4_repeater
# Output: .pio/build/heltec_v4_repeater/firmware.bin
```

---

## Flashing (Heltec V4, ESP32-S3 → .bin)

**Factory image** (single-step, recommended for fresh installs):
```bash
esptool --chip esp32s3 write_flash 0x0 heltec_v4_repeater-factory.bin
```

**Firmware only** (OTA / update):
```bash
esptool --chip esp32s3 write_flash 0x10000 heltec_v4_repeater-firmware.bin
```

Or use the **MeshCore Web Flasher** (`https://flasher.meshcore.co.uk`, "Custom firmware") with the `firmware.bin`.

**Note:** `.uf2` is only for nRF52 boards (e.g. RAK4631); Heltec V4 is ESP32 → use `.bin`.

### Reverting / disabling
- Disable patch 1 without reflashing: `set rxdelay 0` on the repeater.
- Full revert: reflash the upstream firmware.

---

## Optimization layer status

All patches are purely local, mixed-network-safe (no changes to existing packet types/dedup) and "never worse than upstream". Prioritization is data-backed: SNR is a weak lever; **hop count** is more reliable (real-data finding, see below).

**✅ In the code & active (default-on, repeater)**
- **Phase 0:** RX-SNR-weighted flooding (`rxdelay`) + prefer-shorter path adoption with self-healing.
- **Stage A:** hop-weighted rebroadcast delay (`tx_hop_weight`, primary) + SNR weighting (`tx_snr_weight`, secondary) + EWMA neighbour SNR + **adaptive `flood.max`** (floor ≥ P90 = 18, ceiling default 32).
- **Best-of-N at destination:** shortest path (hops, then SNR) instead of "first wins" — dedup-safe, payload exactly once.

**🔒 In the code, default-off (enable only after bench testing — `docs/MHR/BENCH_TEST_PLAN.md`)**
- **Stage B — guarded suppression** (`supp.enable`): suppresses redundant rebroadcasts only when local redundancy is confirmed (5 guards + passive 2-hop learning). Validated: delivery rate ≥ baseline across the full adoption sweep, −12…15 % airtime at high adoption.
- **Phase 2 — proactive DV backbone** (`bb.enable`): control plane with Babel-feasibility (loop-freedom), seqno, feasible successor, hold-down/poisoning, region hierarchy; ignorable zero-hop payload type. Convergence gate **GO** (0 loops, reconverges under churn). Data-plane short-circuit deliberately not wired yet → enabling only changes the control plane.

**🧪 Validation (simulation on real CoreScope data)** — `docs/MHR/sim/` + `docs/MHR/study/`
- 109,980 real packets: measured median detour **2.1×** (confirms the "first-wins" problem). v4 on a real neighbour graph.
- Composite adoption sweep of the full layer: up to **−12 % airtime** + better delivery rate, monotone & safe from 1 node.

**❌ Evaluated & rejected (data-backed)** — adaptive self-tuning controller (2× NO-GO), per-node calibration, TPC: no benefit beyond the guards.

**↗️ Open** — Phase 2 data-plane short-circuit (endpoint integration, own tested stage); hardware bench tests for all stages.

---

## License & attribution

MIT (same as upstream, see `license.txt`). This fork is based on **meshcore-dev/MeshCore**; the original README is included as `README.upstream.md`. All trademarks and project rights for MeshCore remain with the upstream project.

---
## 🇩🇪 Deutsche Übersetzung

# MeshCore-MHR — Erweitertes Routing V1

Ein **Fork von [meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT) V1.15, der die Pfadfindung robuster macht und die durch zufällige Flood-Umwege verschwendete Airtime reduziert.

**MHR** steht für **M**eshCore **H**ybrid **R**outing.

📖 **Dokumentationsübersicht & Einstiegspunkt:** [`docs/MHR/`](docs/MHR/README.md) — was MHR tut, wie es funktioniert, alle Studien/Validierungen + Bench-Test-Plan.

> ⚠️ **Experimentell — auf Hardware ungetestet.** Aktiv (default-an): Phase 0, Stufe A und Best-of-N — alle „nie schlechter als Upstream". **Stufe B** (Suppression) und **Phase 2** (DV-Backbone) sind im Code, aber **default-aus** und sollten erst nach Bench-Tests aktiviert werden (`docs/MHR/BENCH_TEST_PLAN.md`). Zuerst auf ein **Ersatz-/Bench-Gerät** flashen, nicht auf Produktions-Repeater. Vollständiger Status: siehe „Status der Optimierungsschichten" unten.

---

## Was ist MHR-MeshCore?

Ein Fork von **[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT), der die **Pfadfindung** im LoRa-Mesh robuster macht und die durch zufällige Flood-Umwege verschwendete **Airtime** reduziert. MeshCore ist ein hybrides Mesh-Routing-Protokoll für LoRa-Funkgeräte (ESP32/nRF52, C++/PlatformIO).

## Das Problem

MeshCore nutzt **kein** metrikbasiertes Routing. Die erste Nachricht an einen Kontakt wird geflutet, der entstehende Pfad wird gecacht, und **jedes folgende Paket wird an diesen einen Pfad gebunden**. Entscheidend ist: der Gewinner ist **nicht der kürzeste oder beste Pfad, sondern die Flood-Kopie, die zufällig als erste das Ziel erreicht** („First packet wins"), während signalqualitätsgewichtete Weiterleitung standardmäßig deaktiviert ist. Aus der Analyse realer Netzwerkdaten: **~60 % aller Pfad-Setups enden auf einem Umweg** — dies verbraucht Airtime, den eigentlichen Engpass auf einem gemeinsam genutzten Halbduplex-Funkkanal.

## Die Lösung

MHR richtet die Flood-Weiterleitung an der **Linkqualität (SNR)** auf beiden Seiten aus:
- **RX-Seite:** Knoten mit starkem Empfang senden zuerst weiter und unterdrücken redundante Umweg-Kopien (standardmäßig an).
- **TX-Seite:** Eine Kopie mit starkem SNR (meist ein kurzer, direkter Link) zieht ihren zufälligen Backoff aus einem gegen null verkleinerten Fenster → sie sendet früher weiter und „gewinnt" den Pfad.
- **Pfad-Übernahme:** Ein später eintreffender **längerer** Umweg überschreibt keinen guten kurzen Pfad mehr.
- **EWMA-Link-Sensing:** Geglätteter Nachbar-SNR als stabiles Linkqualitäts-Schätzmittel — Grundlage für eine künftige ETX-Metrik.

## Warum es sicher ist

Alle Änderungen sind **lokal, additiv und reversibel** (per CLI zur Laufzeit umschaltbar, kein Neu-Flashen nötig). **Kein Paketformat-Wechsel, keine Änderung der Duplikatserkennung** → MHR läuft in einem **gemischten Netz neben unveränderten Upstream-Knoten** und ist **nie schlechter als das Original** (bei deaktivierten Parametern verhält es sich bitidentisch zum Upstream).

## Belege

Simulation auf einer **echten 25-Knoten-Topologie**: **−82 % Airtime**, Umwege deutlich reduziert. Die Gewinne halten unter Stress stand — Pfad-Flapping **78 % → 17 %**, Linkausfall-Wiederentdeckung **≤ 1,6 % vs. bis zu 49,5 %**, Netzwerk-Partition-Airtime **−98 %** statt endlosem Fluten. Build verifiziert; GitHub Actions baut automatisch die flashbare **Heltec V4 Firmware (.bin)**. Details & Belege in `docs/MHR/`.

---

## Was geändert wurde

Minimale, rückwärtskompatible, reversible Patches — alle mit `// MHR:` im Code markiert, kein Paketformat-Wechsel, keine Dedup-Änderung (Mixed-Firmware-sicher). Build verifiziert (`pio run -e heltec_v4_repeater` → SUCCESS). Vollständige Details in `docs/MHR/CHANGES_MHR.md`.

**Phase 0**
1. **SNR-gewichtetes Fluten standardmäßig an** (`examples/simple_repeater/MyMesh.cpp`): `rx_delay_base` von `0.0` auf `10.0`. Starke (kurze) Links senden zuerst weiter und unterdrücken Umweg-Kopien. **Reversibel:** `set rxdelay 0`.
2. **Pfad-Übernahme nur bei Verbesserung** (`src/helpers/BaseChatMesh.cpp`): ein später eintreffender **längerer** Pfad überschreibt keinen guten kurzen Pfad mehr. Nie schlechter als Upstream. **Selbstheilung:** ein Fehler-Zähler + 30-Minuten-Veralterungsschwelle ermöglichen es, einen längeren funktionierenden Pfad einen festgepinnten toten zu ersetzen (nur RAM, keine Persistenzänderung).

**Phase 1**
3. **SNR-gewichtetes TX-Retransmit-Delay** (`getRetransmitDelay`): erweitert Patch 1 auf die Sendeseite — starke Empfänger senden Flood-Kopien früher weiter (aus einem zufälligkeitserhaltend verkleinerten Fenster), schwache später. Neuer reversibler Parameter `tx_snr_weight` (Standard 0,5, `set txsnrweight 0` = Upstream).
4. **EWMA-geglätteter Nachbar-SNR** (`putNeighbour`): stabiles Linkqualitäts-Schätzmittel (L0 Link-Sensing) statt verrauschtem Momentanwert — Grundlage für ETX.

**Stufe A+ / B / Phase 2** (alle adversarial überprüft)
5. **Best-of-N am Ziel** (`src/Mesh.cpp`): kürzester Pfad (Hops, dann SNR) statt „First wins" — Dedup-sicher (Payload genau einmal zugestellt). `bofn.enable` (Repeater default-an).
6. **`flood.max` 64 → adaptiv** (datenbelegt): fester Wert 15 lag unter dem gemessenen Netz-P90 von 18 Hops; ersetzt durch eine adaptive Obergrenze, die zwischen beobachtetem Durchmesser + Puffer und der Nutzer-Obergrenze schwankt (Standard 32). Rein lokale Weiterleitungsbegrenzung.
7. **Stufe B — guarded Suppression** (`supp.enable`, **default-aus**): redundanzgesicherte Rebroadcast-Unterdrückung (5 Guards + passives 2-Hop-Lernen).
8. **Phase 2 — DV-Backbone** (`bb.enable`, **default-aus**): proaktive Steuerungsebene (Babel-Feasibility, Convergence-Gate GO); ignorierbarer Zero-Hop-Payload-Typ.

> Vollständiger Status (aktiv vs. default-aus) + Validierung → Abschnitt **„Status der Optimierungsschichten"** unten. Vollständige Patch-Liste 1–9: `docs/MHR/CHANGES_MHR.md`.

---

## Firmware bauen

### Option A — automatisch via GitHub Actions (empfohlen)
Nach dem Push in das Repository baut `.github/workflows/build.yml` die Firmware automatisch. Unter **Actions → Build MHR firmware → Artifacts** findet sich `heltec_v4_repeater-firmware.bin` (+ `-factory.bin`). Weitere Targets können in der Workflow-Datei auskommentiert werden.

### Option B — lokal mit PlatformIO
```bash
pip install platformio
pio run -e heltec_v4_repeater
# Ausgabe: .pio/build/heltec_v4_repeater/firmware.bin
```

---

## Flashen (Heltec V4, ESP32-S3 → .bin)

**Factory-Image** (ein Schritt, empfohlen für Erstinstallationen):
```bash
esptool --chip esp32s3 write_flash 0x0 heltec_v4_repeater-factory.bin
```

**Nur Firmware** (OTA / Update):
```bash
esptool --chip esp32s3 write_flash 0x10000 heltec_v4_repeater-firmware.bin
```

Oder den **MeshCore Web Flasher** (`https://flasher.meshcore.co.uk`, „Custom firmware") mit der `firmware.bin` nutzen.

**Hinweis:** `.uf2` ist nur für nRF52-Boards (z. B. RAK4631); Heltec V4 ist ESP32 → `.bin` verwenden.

### Rückgängig machen / Deaktivieren
- Patch 1 ohne Neu-Flashen deaktivieren: `set rxdelay 0` am Repeater.
- Vollständige Rückkehr: Upstream-Firmware neu flashen.

---

## Status der Optimierungsschichten

Alle Patches sind rein lokal, Mixed-Network-sicher (keine Änderungen an bestehenden Pakettypen/Dedup) und „nie schlechter als Upstream". Die Priorisierung ist datenbelegt: SNR ist ein schwacher Hebel; **Hop-Zahl** ist zuverlässiger (Realdaten-Ergebnis, siehe unten).

**✅ Im Code & aktiv (default-an, Repeater)**
- **Phase 0:** RX-SNR-gewichtetes Fluten (`rxdelay`) + Prefer-Shorter-Pfad-Übernahme mit Selbstheilung.
- **Stufe A:** Hop-gewichtetes Rebroadcast-Delay (`tx_hop_weight`, primär) + SNR-Gewichtung (`tx_snr_weight`, sekundär) + EWMA-Nachbar-SNR + **adaptives `flood.max`** (Untergrenze ≥ P90 = 18, Obergrenze Standard 32).
- **Best-of-N am Ziel:** kürzester Pfad (Hops, dann SNR) statt „First wins" — Dedup-sicher, Payload genau einmal.

**🔒 Im Code, default-aus (nur nach Bench-Tests aktivieren — `docs/MHR/BENCH_TEST_PLAN.md`)**
- **Stufe B — guarded Suppression** (`supp.enable`): unterdrückt redundante Rebroadcasts nur, wenn lokale Redundanz bestätigt ist (5 Guards + passives 2-Hop-Lernen). Validiert: Zustellrate ≥ Baseline über den gesamten Adoptions-Sweep, −12…15 % Airtime bei hoher Adoption.
- **Phase 2 — proaktiver DV-Backbone** (`bb.enable`): Steuerungsebene mit Babel-Feasibility (Schleifenfreiheit), Seqno, Feasible Successor, Hold-Down/Poisoning, Regions-Hierarchie; ignorierbarer Zero-Hop-Payload-Typ. Konvergenz-Gate **GO** (0 Schleifen, konvergiert unter Churn neu). Data-Plane-Short-Circuit absichtlich noch nicht verdrahtet → Aktivieren ändert nur die Steuerungsebene.

**🧪 Validierung (Simulation auf echten CoreScope-Daten)** — `docs/MHR/sim/` + `docs/MHR/study/`
- 109.980 echte Pakete: gemessener Median-Umweg **2,1×** (bestätigt das „First-wins"-Problem). v4 auf echtem Nachbar-Graphen.
- Kombinierter Adoptions-Sweep der vollständigen Schicht: bis zu **−12 % Airtime** + bessere Zustellrate, monoton & sicher ab 1 Knoten.

**❌ Bewertet & verworfen (datenbelegt)** — adaptiver Selbsttuning-Regler (2× NO-GO), per-Node-Kalibrierung, TPC: kein Nutzen über die Guards hinaus.

**↗️ Offen** — Phase 2 Data-Plane-Short-Circuit (Endpunkt-Integration, eigene getestete Stufe); Hardware-Bench-Tests für alle Stufen.

---

## Lizenz & Danksagung

MIT (wie Upstream, siehe `license.txt`). Dieser Fork basiert auf **meshcore-dev/MeshCore**; das ursprüngliche README ist als `README.upstream.md` enthalten. Alle Markenzeichen und Projektrechte für MeshCore verbleiben beim Upstream-Projekt.
