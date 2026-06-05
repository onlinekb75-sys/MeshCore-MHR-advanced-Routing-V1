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
