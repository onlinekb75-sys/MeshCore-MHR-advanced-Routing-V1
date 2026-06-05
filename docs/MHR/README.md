# MHR — Documentation Overview & Entry Point

**MHR = MeshCore Hybrid Routing.** This page explains *what* the fork does, *how* it works and
*where* the details are. (Quick entry point for the repo root README; full patch list in
[`CHANGES_MHR.md`](CHANGES_MHR.md).)

---

## 1. What is this about?

MeshCore (LoRa mesh) uses **no** metric-based routing: the first message is flooded, the
**first-arriving** path is cached and reused indefinitely — often a **detour**. Measured on real
data: **median detour 2.1×**. Detours = wasted **airtime**, the actual bottleneck.

MHR adds an **invisible, purely node-local optimization layer** on top: it aligns flooding with
**hop count / link quality**, selects the **shortest** path, and suppresses **redundant**
transmissions — **without** touching the packet format, **mixed-firmware-compatible** with stock
nodes, and **"never worse" than upstream**. Architecture detail:
[`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md).

## 2. What can it do? (Stages — what each one delivers)

| Stage | Mechanism | Status | Benefit |
|---|---|---|---|
| **Phase 0** | RX-SNR flooding + prefer-shorter path adoption | ✅ active | quality-guided propagation |
| **Stage A** | Hop-weighted TX delay + adaptive `flood.max` + EWMA SNR | ✅ active | shorter paths lead the flood |
| **Best-of-N** | Destination reports shortest path (hops → SNR) instead of "first wins" | ✅ active | detour killer, dedup-safe |
| **Stage B** | Guarded suppression (5 guards + passive 2-hop learning) | 🔒 default-off | −12…15 % airtime, delivery ≥ baseline |
| **Phase 2** | Proactive DV backbone (Babel-feasibility, regions) | 🔒 default-off | optimal unicast paths instead of flooding |

🔒 = in the code, but **default-off** until bench testing ([`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md)).  
**Evaluated & rejected** (no benefit beyond the guards): adaptive self-tuning controller, per-node
calibration, TX power control — see studies below.

## 3. Is this validated? (Validation story)

Everything simulated on **real CoreScope live data** (109,980 packets, 1,962 nodes) — not just theory:
- **Measured:** real median detour 2.1× → confirms the problem. ([`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md))
- **Composite adoption sweep** (1 %→100 % nodes with MHR): up to −12 % airtime, monotone & safe from 1 node. ([`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md))
- **Phase 2 convergence gate: GO** (0 loops, reconverges under churn). ([`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md))
- Dataset + reproduction: [`sim/README.md`](sim/README.md), provenance: [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md).

## 4. Where to start?

- **Build / flash:** repo root README (PlatformIO / Web Flasher) → `dist/`.
- **Test on hardware:** [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) (staged, with acceptance criteria; Stage B / Phase 2 only enabled here).
- **What exactly was changed:** [`CHANGES_MHR.md`](CHANGES_MHR.md) (patches 1–9, with CLI commands + persistence offsets).

---

## 5. Documentation index (all documents)

**Analysis & design**
- [`MeshCore_Routing_Analyse_und_Optimierung.md`](MeshCore_Routing_Analyse_und_Optimierung.md) — root-cause analysis of detours (stages A–D)
- [`MeshCore_Hybrid_Routing_Entwurf.md`](MeshCore_Hybrid_Routing_Entwurf.md) — MHR v1 (DSR + ETX + Best-of-N + backbone)
- [`MeshCore_Hybrid_Routing_v2_Robustheit.md`](MeshCore_Hybrid_Routing_v2_Robustheit.md) — v2 hardening from real data (H1–H7)
- [`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md) — **architecture** of the node-local layer
- [`CHANGES_MHR.md`](CHANGES_MHR.md) — full patch list 1–9
- [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) — hardware bench test (Heltec V4)

**Studies & validations** (`study/`)
- [`study/MeshCore_Routing_Study.md`](study/MeshCore_Routing_Study.md) — mechanism study (adoption sweep, tiering) · companion: [`study/STUDY_DESIGN.md`](study/STUDY_DESIGN.md), [`study/STUDY_RESULTS.md`](study/STUDY_RESULTS.md)
- [`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md) — full layer at 1/10/25/50/75/100 % adoption
- [`study/Suppression_Design.md`](study/Suppression_Design.md) + [`study/SUPPRESSION_VALIDATION.md`](study/SUPPRESSION_VALIDATION.md) — Stage B (5 guards) design + GO
- [`study/Phase2_Backbone_Design.md`](study/Phase2_Backbone_Design.md) + [`study/Backbone_Phase2_Study.md`](study/Backbone_Phase2_Study.md) + [`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md) — Phase 2 design, airtime economics, convergence gate
- [`study/Path_Reinforcement_Study.md`](study/Path_Reinforcement_Study.md) — path success reinforcement (GO)
- [`study/Adaptive_Controller_Design.md`](study/Adaptive_Controller_Design.md) + [`study/Local_Calibration_Study.md`](study/Local_Calibration_Study.md) — adaptive self-tuning (**NO-GO**, data-backed)

**Simulation & data** (`sim/`)
- [`sim/README.md`](sim/README.md) — how to run the sims + CoreScope endpoints
- [`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md) — v3 on 109,980 real packets (core measurement)
- [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md) — dataset provenance, schema, attribution
- older sims: [`MeshCore_Simulation_25Knoten.md`](MeshCore_Simulation_25Knoten.md), [`MeshCore_Simulation_ECHTE_Daten.md`](MeshCore_Simulation_ECHTE_Daten.md) *(illustrative — v3/v4 are the authoritative reference)*
- scripts: `sim/mhr_sim*.py`, `sim/mhr_collect_corescope.py`, `study/*_sim.py`

> Guiding principle throughout: **quality and stability over last-mile optimization.** Every patch is
> local, reversible, mixed-firmware-safe and "never worse than upstream"; risky stages are default-off
> and bench-gated.
