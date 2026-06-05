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

---
## 🇩🇪 Deutsche Übersetzung

# MHR — Dokumentationsübersicht & Einstiegspunkt

**MHR = MeshCore Hybrid Routing.** Diese Seite erklärt, *was* der Fork tut, *wie* er funktioniert
und *wo* die Details zu finden sind. (Schnelleinstieg vom Repo-Root-README; vollständige Patch-Liste
in [`CHANGES_MHR.md`](CHANGES_MHR.md).)

---

## 1. Worum geht es?

MeshCore (LoRa-Mesh) nutzt **kein** metrik-basiertes Routing: Die erste Nachricht wird geflutet, der
**zuerst eintreffende** Pfad wird gecacht und danach unbegrenzt wiederverwendet — oft ein
**Umweg**. Gemessen an echten Daten: **medianer Umweg 2,1×**. Umwege = verschwendete **Airtime**,
der eigentliche Engpass.

MHR fügt eine **unsichtbare, rein knotenlokal arbeitende Optimierungsschicht** darüber ein: Sie
richtet die Flutung an **Hop-Zahl / Linkqualität** aus, wählt den **kürzesten** Pfad und
unterdrückt **redundante** Übertragungen — **ohne** das Paketformat anzutasten,
**mixed-firmware-kompatibel** mit Standard-Knoten, und **„nie schlechter als Upstream"**.
Architekturdetails: [`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md).

## 2. Was kann es? (Stufen — was jede liefert)

| Stufe | Mechanismus | Status | Vorteil |
|---|---|---|---|
| **Phase 0** | RX-SNR-Flutung + Prefer-Shorter-Pfadübernahme | ✅ aktiv | qualitätsgesteuerte Ausbreitung |
| **Stufe A** | Hop-gewichtetes TX-Delay + adaptives `flood.max` + EWMA-SNR | ✅ aktiv | kürzere Pfade führen die Flutung an |
| **Best-of-N** | Ziel meldet kürzesten Pfad (Hops → SNR) statt „First wins" | ✅ aktiv | Umwegkiller, dedup-sicher |
| **Stufe B** | Gesicherte Unterdrückung (5 Guards + passives 2-Hop-Lernen) | 🔒 default-aus | −12…15 % Airtime, Lieferquote ≥ Baseline |
| **Phase 2** | Proaktiver DV-Backbone (Babel-Feasibility, Regionen) | 🔒 default-aus | optimale Unicast-Pfade statt Flutung |

🔒 = im Code vorhanden, aber **default-aus** bis zum Bench-Test
([`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md)).  
**Bewertet & verworfen** (kein Vorteil über die Guards hinaus): adaptiver Selbstregler,
knotenweise Kalibrierung, TX-Leistungsregelung — siehe Studien unten.

## 3. Ist das validiert? (Validierungsgeschichte)

Alles auf **echten CoreScope-Livedaten** simuliert (109.980 Pakete, 1.962 Knoten) — nicht nur Theorie:
- **Gemessen:** echter medianer Umweg 2,1× → bestätigt das Problem. ([`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md))
- **Zusammengesetzter Adoptions-Sweep** (1 %→100 % Knoten mit MHR): bis zu −12 % Airtime, monoton & sicher ab 1 Knoten. ([`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md))
- **Phase-2-Konvergenz-Gate: GO** (0 Schleifen, konvergiert unter Churn neu). ([`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md))
- Datensatz + Reproduktion: [`sim/README.md`](sim/README.md), Herkunft: [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md).

## 4. Wo anfangen?

- **Bauen / flashen:** Repo-Root-README (PlatformIO / Web Flasher) → `dist/`.
- **Test auf Hardware:** [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) (gestuft, mit Abnahmekriterien; Stufe B / Phase 2 nur hier aktiviert).
- **Was genau geändert wurde:** [`CHANGES_MHR.md`](CHANGES_MHR.md) (Patches 1–9, mit CLI-Befehlen + Persistenz-Offsets).

---

## 5. Dokumentationsindex (alle Dokumente)

**Analyse & Design**
- [`MeshCore_Routing_Analyse_und_Optimierung.md`](MeshCore_Routing_Analyse_und_Optimierung.md) — Ursachenanalyse der Umwege (Stufen A–D)
- [`MeshCore_Hybrid_Routing_Entwurf.md`](MeshCore_Hybrid_Routing_Entwurf.md) — MHR v1 (DSR + ETX + Best-of-N + Backbone)
- [`MeshCore_Hybrid_Routing_v2_Robustheit.md`](MeshCore_Hybrid_Routing_v2_Robustheit.md) — v2-Härtung aus Realdaten (H1–H7)
- [`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md) — **Architektur** der knotenlokal arbeitenden Schicht
- [`CHANGES_MHR.md`](CHANGES_MHR.md) — vollständige Patch-Liste 1–9
- [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) — Hardware-Bench-Test (Heltec V4)

**Studien & Validierungen** (`study/`)
- [`study/MeshCore_Routing_Study.md`](study/MeshCore_Routing_Study.md) — Mechanismus-Studie (Adoptions-Sweep, Tiering) · Begleitdokumente: [`study/STUDY_DESIGN.md`](study/STUDY_DESIGN.md), [`study/STUDY_RESULTS.md`](study/STUDY_RESULTS.md)
- [`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md) — vollständige Schicht bei 1/10/25/50/75/100 % Adoption
- [`study/Suppression_Design.md`](study/Suppression_Design.md) + [`study/SUPPRESSION_VALIDATION.md`](study/SUPPRESSION_VALIDATION.md) — Stufe-B-Design (5 Guards) + GO
- [`study/Phase2_Backbone_Design.md`](study/Phase2_Backbone_Design.md) + [`study/Backbone_Phase2_Study.md`](study/Backbone_Phase2_Study.md) + [`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md) — Phase-2-Design, Airtime-Ökonomie, Konvergenz-Gate
- [`study/Path_Reinforcement_Study.md`](study/Path_Reinforcement_Study.md) — Pfaderfolgs-Verstärkung (GO)
- [`study/Adaptive_Controller_Design.md`](study/Adaptive_Controller_Design.md) + [`study/Local_Calibration_Study.md`](study/Local_Calibration_Study.md) — adaptiver Selbstregler (**NO-GO**, datenbelegt)

**Simulation & Daten** (`sim/`)
- [`sim/README.md`](sim/README.md) — Anleitung zum Ausführen der Simulationen + CoreScope-Endpunkte
- [`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md) — v3 auf 109.980 echten Paketen (Kernmessung)
- [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md) — Datensatzherkunft, Schema, Quellenangaben
- ältere Simulationen: [`MeshCore_Simulation_25Knoten.md`](MeshCore_Simulation_25Knoten.md), [`MeshCore_Simulation_ECHTE_Daten.md`](MeshCore_Simulation_ECHTE_Daten.md) *(illustrativ — v3/v4 sind die maßgebliche Referenz)*
- Skripte: `sim/mhr_sim*.py`, `sim/mhr_collect_corescope.py`, `study/*_sim.py`

> Leitendes Prinzip durchgehend: **Qualität und Stabilität vor Last-Mile-Optimierung.** Jeder Patch
> ist lokal, reversibel, mixed-firmware-sicher und „nie schlechter als Upstream"; riskante Stufen
> sind default-aus und bench-gegated.
