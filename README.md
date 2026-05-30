# MeshCore-MHR-advanced Routing V1

Ein **Fork von [meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT) V1.15 mit dem Ziel, die Pfadfindung robuster zu machen und die durch zufällige Flood-Umwege verursachte Airtime-Last zu senken.

**MHR** steht für **M**eshCore **H**ybrid **R**outing.

> ⚠️ **Experimentell & auf Hardware ungetestet.** Aktiv (default-an): Phase 0, Stufe A und Best-of-N — alle „nie schlechter als Upstream". **Stufe B** (Suppression) und **Phase 2** (DV-Backbone) sind im Code, aber **default-AUS** und erst nach Bench-Test zu aktivieren (`docs/MHR/BENCH_TEST_PLAN.md`). Flashe **zuerst auf ein Ersatz-/Bench-Gerät**, nicht auf produktive Repeater. Voller Status: Abschnitt „Stand der Optimierungs-Schicht".

---

## 🇩🇪 Kernvorteile

### Was ist MHR-MeshCore?
Ein Fork von **[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT), der die **Pfadfindung** im LoRa-Mesh robuster macht und die durch zufällige Flood-Umwege verursachte **Airtime-Last** senkt. MeshCore ist ein hybrides Mesh-Routing-Protokoll für LoRa-Funkgeräte (ESP32/nRF52, C++/PlatformIO).

### Das Problem
MeshCore nutzt **kein** metrik-basiertes Routing. Die erste Nachricht an einen Kontakt wird geflutet, der dabei entstehende Pfad wird gecacht, und **alle weiteren Pakete laufen fest über genau diesen einen Pfad**. Entscheidend: Es gewinnt **nicht der kürzeste oder beste Pfad, sondern derjenige, dessen Flood-Kopie zufällig zuerst beim Ziel ankommt** („first packet wins"). Die signalqualitäts-gewichtete Ausbreitung ist per Default abgeschaltet. Folge: In der Analyse echter Netzdaten landeten **~60 % der Pfadaufbauten auf einem Umweg** — das frisst Airtime, den eigentlichen Engpass im geteilten Halbduplex-Funkkanal.

### Die Lösung
MHR richtet die Flood-Ausbreitung an der **Linkqualität (SNR)** aus — an beiden Stufen:
- **RX-Seite:** Knoten mit starkem Empfang leiten zuerst weiter und unterdrücken redundante Umweg-Kopien (per Default aktiviert).
- **TX-Seite:** Eine Kopie mit starkem SNR (meist ein kurzer, direkter Link) zieht ihre zufällige Sendeverzögerung aus einem zu null hin geschrumpften Fenster → sie sendet früher und „gewinnt" den Pfad.
- **Pfad-Adoption:** Ein später eintreffender **längerer** Umweg überschreibt einen guten kurzen Pfad nicht mehr.
- **EWMA-Link-Sensing:** geglättete Nachbar-SNR als stabile Linkqualitäts-Schätzung — Fundament für eine spätere ETX-Metrik.

### Warum es sicher ist
Alle Eingriffe sind **lokal, additiv und reversibel** (zur Laufzeit per CLI ab-/zuschaltbar, ohne Reflash). **Kein Paketformat-Eingriff, keine Änderung der Duplikat-Erkennung** → MHR läuft im **gemischten Netz neben unveränderten Upstream-Knoten** und ist **nie schlechter als das Original** (bei abgeschalteten Parametern bit-identisch zu Upstream).

### Belege
Simulation auf **echter 25-Knoten-Topologie**: **−82 % Airtime**, Umwege deutlich reduziert. Unter Störung bleibt der Gewinn stabil — Pfad-Flattern **78 % → 17 %**, Re-Discovery bei Linkausfall **≤ 1,6 % statt bis 49,5 %**, bei Netz-Partition **−98 % Airtime** statt Endlos-Flood. Build verifiziert; GitHub Actions baut die flashbare **Heltec-V4-Firmware (.bin)** automatisch. Details & Belege in `docs/MHR/`.

---

## 🇬🇧 Core advantages

### What is MHR-MeshCore?
**MHR** stands for **M**eshCore **H**ybrid **R**outing. A fork of **[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** (MIT) that makes **path-finding** in the LoRa mesh more robust and cuts the **airtime** wasted on random flood detours. MeshCore is a hybrid mesh-routing protocol for LoRa radios (ESP32/nRF52, C++/PlatformIO).

### The problem
MeshCore uses **no** metric-based routing. The first message to a contact is flooded, the resulting path is cached, and **every following packet is pinned to that one path**. Crucially, the winner is **not the shortest or best path, but whichever flood copy happens to reach the destination first** ("first packet wins"), while signal-quality-weighted propagation is off by default. From analysis of real network data: **~60 % of path setups end up on a detour** — burning airtime, the real bottleneck on a shared half-duplex radio channel.

### The solution
MHR aligns flood propagation with **link quality (SNR)** at both stages:
- **RX side:** nodes with strong reception rebroadcast first, suppressing redundant detour copies (on by default).
- **TX side:** a copy with strong SNR (usually a short, direct link) draws its random backoff from a window shrunk toward zero → it rebroadcasts earlier and "wins" the path.
- **Path adoption:** a later-arriving **longer** detour no longer overwrites a good short path.
- **EWMA link sensing:** smoothed neighbour SNR as a stable link-quality estimate — the foundation for a future ETX metric.

### Why it's safe
All changes are **local, additive and reversible** (toggle at runtime via CLI, no reflash). **No packet-format change, no change to duplicate detection** → MHR runs in a **mixed network alongside unmodified upstream nodes** and is **never worse than the original** (with the parameters disabled it behaves bit-identically to upstream).

### Evidence
Simulation on a **real 25-node topology**: **−82 % airtime**, detours markedly reduced. The gains hold under stress — path-flapping **78 % → 17 %**, link-failure re-discovery **≤ 1.6 % vs. up to 49.5 %**, network-partition airtime **−98 %** instead of endless flooding. Build verified; GitHub Actions builds the flashable **Heltec V4 firmware (.bin)** automatically. Details & evidence in `docs/MHR/`.

---

## Was wurde geändert

Minimale, rückwärts­kompatible, reversible Eingriffe — alle als `// MHR:` im Code markiert, kein Paketformat-Eingriff, keine Dedup-Änderung (mixed-firmware-safe). Build verifiziert (`pio run -e heltec_v4_repeater` → SUCCESS). Details in `docs/MHR/CHANGES_MHR.md`.

**Phase 0**
1. **SNR-gewichtete Flutung als Default an** (`examples/simple_repeater/MyMesh.cpp`): `rx_delay_base` von `0.0` auf `10.0`. Starke (kurze) Links senden zuerst und unterdrücken Umweg-Kopien. **Reversibel:** `set rxdelay 0`.
2. **Pfad nur bei Verbesserung übernehmen** (`src/helpers/BaseChatMesh.cpp`): ein später eintreffender **längerer** Pfad überschreibt einen guten kurzen nicht mehr. Nie schlechter als Upstream.

**Phase 1** (neu)
3. **SNR-gewichtetes TX-Retransmit-Delay** (`getRetransmitDelay`): ergänzt Patch 1 um die Sendeseite — starke Empfänger senden Flood-Kopien früher (aus zufallserhaltendem, geschrumpftem Fenster), schwache später. Neuer reversibler Parameter `tx_snr_weight` (Default 0.5, `set txsnrweight 0` = Upstream).
4. **EWMA-geglättete Nachbar-SNR** (`putNeighbour`): stabile Linkqualitäts-Schätzung (L0 Link-Sensing) statt verrauschtem Momentanwert — Fundament für ETX.

**Stufe A+ / B / Phase 2** (alle adversarial reviewt)
5. **Best-of-N am Ziel** (`src/Mesh.cpp`): kürzester Pfad (Hops, dann SNR) statt „first wins" — dedup-sicher (Payload genau 1×). `bofn.enable` (Repeater default an).
6. **`flood.max` 64 → 15** (datenbelegt): kappt Fern-Umwege; rein lokales Forward-Limit.
7. **Stufe B — guarded Suppression** (`supp.enable`, **default-AUS**): redundanz-gesicherte Rebroadcast-Unterdrückung (5 Guards + passives 2-Hop-Lernen).
8. **Phase 2 — DV-Backbone** (`bb.enable`, **default-AUS**): proaktiver Control-Plane (Babel-Feasibility, Konvergenz-Gate GO); ignorierbarer zero-hop Payload-Typ.

> Vollständiger Status (aktiv vs. default-aus) + Validierung → Abschnitt **„Stand der Optimierungs-Schicht"** unten. Komplette Patch-Liste 1–9: `docs/MHR/CHANGES_MHR.md`.

## Firmware bauen

### Variante A — automatisch via GitHub Actions (empfohlen)
Nach dem Push in dein privates Repo baut `.github/workflows/build.yml` die Firmware selbst. Unter **Actions → Build MHR firmware → Artifacts** liegt anschließend `heltec_v4_repeater-firmware.bin` (+ `-factory.bin`) zum Download. Weitere Ziele im Workflow einfach einkommentieren.

### Variante B — lokal mit PlatformIO
```bash
pip install platformio
pio run -e heltec_v4_repeater
# Ergebnis: .pio/build/heltec_v4_repeater/firmware.bin
```

## Flashen (Heltec V4, ESP32-S3 → .bin)

- Bequem über den **MeshCore Web-Flasher** (`https://flasher.meshcore.co.uk`, „Custom firmware") mit der `firmware.bin`, **oder**
- per esptool:
  ```bash
  python -m esptool --chip esp32s3 write_flash 0x10000 heltec_v4_repeater-firmware.bin
  # alternativ das Factory-Image @0x0:
  python -m esptool --chip esp32s3 write_flash 0x0 heltec_v4_repeater-factory.bin
  ```

**Hinweis:** `.uf2` gibt es nur für nRF52-Boards (z. B. RAK4631); Heltec V4 ist ESP32 → `.bin`.

### Wieder zurück / deaktivieren
- Patch 1 ohne Reflash abschalten: am Repeater `set rxdelay 0`.
- Komplett zurück: Upstream-Firmware neu flashen.

## Eigenes privates GitHub-Repo

```bash
cd MHR-MeshCore
# (Git ist bereits initialisiert und committet)
git remote add origin git@github.com:<DEIN-USER>/MHR-MeshCore.git
git branch -M main
git push -u origin main
```
Lege das Repo auf GitHub vorher als **Private** an. (Ich kann das nicht für dich tun — Account/Push erfordern deine Anmeldedaten.)

## Stand der Optimierungs-Schicht

Alle Eingriffe sind rein lokal, mischbetriebs-sicher (kein Eingriff an bestehenden Paket-Typen/Dedup) und „nie schlechter als Upstream". Priorisierung datenbelegt: SNR ist ein schwacher Hebel, **Hop-Zahl** ist verlässlicher (Realdaten-Befund, siehe unten).

**✅ Im Code & aktiv (default-an, Repeater)**
- **Phase 0:** RX-SNR-gewichtete Flutung (`rxdelay`) + prefer-shorter Pfad-Adoption.
- **Stufe A:** hop-gewichtetes Rebroadcast-Delay (`tx_hop_weight`, primär) + SNR-Gewichtung (`tx_snr_weight`, sekundär) + EWMA-Nachbar-SNR + **`flood.max` 64→15** (datenbelegt, realer Durchmesser P90≈12–18).
- **Best-of-N am Ziel:** kürzester Pfad (Hops, dann SNR) statt „first wins" — dedup-sicher, Payload genau 1×.

**🔒 Im Code, default-AUS (erst nach Bench-Test aktivieren — `docs/MHR/BENCH_TEST_PLAN.md`)**
- **Stufe B — guarded Suppression** (`supp.enable`): unterdrückt redundante Rebroadcasts nur bei lokal bestätigter Redundanz (5 Guards + passives 2-Hop-Lernen). Validiert: Lieferquote ≥ Baseline über den ganzen Adoptions-Sweep, −12…15 % Airtime bei hoher Adoption.
- **Phase 2 — proaktiver DV-Backbone** (`bb.enable`): Control-Plane mit Babel-Feasibility (Schleifenfreiheit), Seqno, Feasible-Successor, Hold-down/Poisoning, Regions-Hierarchie; ignorierbarer zero-hop Payload-Typ. Konvergenz-Gate **GO** (0 Loops, re-konvergiert unter Churn). Data-Plane-Short-Circuit bewusst noch nicht verdrahtet → Aktivieren ändert nur die Control-Plane.

**🧪 Validierung (Simulation auf echten CoreScope-Daten)** — `docs/MHR/sim/` + `docs/MHR/study/`
- 109.980 reale Pakete: realer Umweg-Median **2,1×** (belegt das „first-wins"-Problem). v4 auf echtem `neighbor-graph`.
- Komposit-Adoptions-Sweep der ganzen Schicht: bis **−12 % Airtime** + bessere Lieferquote, monoton & sicher ab 1 Knoten.

**❌ Geprüft & verworfen (datenbelegt)** — adaptiver Selbst-Regler (2× NO-GO), per-Node-Kalibrierung, TPC: bringen über die Guards hinaus nichts.

**↗️ Offen** — Phase-2-Daten-Plane-Short-Circuit (Endpunkt-Integration, eigene getestete Stufe); Hardware-Bench-Tests aller Stufen.

## Lizenz & Attribution

MIT (wie Upstream, siehe `license.txt`). Dieser Fork basiert auf **meshcore-dev/MeshCore**; das ursprüngliche README liegt als `README.upstream.md` bei. Alle Marken-/Projektrechte an MeshCore verbleiben beim Upstream-Projekt.
