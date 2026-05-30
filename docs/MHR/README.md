# MHR — Doku-Übersicht & Einstieg

**MHR = MeshCore Hybrid Routing.** Diese Seite erklärt, *was* der Fork kann, *wie* er funktioniert und
*wo* die Details stehen. (Schnelleinstieg fürs README im Repo-Root; vollständige Patch-Liste in
[`CHANGES_MHR.md`](CHANGES_MHR.md).)

---

## 1. Worum geht es?
MeshCore (LoRa-Mesh) macht **kein** metrik-basiertes Routing: die erste Nachricht wird geflutet, der
**zuerst** eintreffende Pfad gecacht und fest weiterverwendet — oft ein **Umweg**. Auf echten Daten
gemessen: **Median-Umweg 2,1×**. Umwege = verschwendete **Airtime**, der eigentliche Engpass.

MHR legt eine **unsichtbare, rein node-lokale Optimierungs-Schicht** darüber: sie richtet die Flutung
an der **Hop-Zahl/Linkqualität** aus, wählt den **kürzesten** Pfad, unterdrückt **redundante**
Aussendungen — **ohne** Paketformat-Eingriff, **mischbetriebs-kompatibel** mit Stock-Knoten und
**„nie schlechter" als Upstream**. Architektur-Detail: [`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md).

## 2. Was kann es? (Stufen — was jede bringt)

| Stufe | Mechanismus | Status | Nutzen |
|---|---|---|---|
| **Phase 0** | RX-SNR-Flutung + prefer-shorter Pfad-Adoption | ✅ aktiv | qualitätsgeleitete Ausbreitung |
| **Stufe A** | Hop-gewichtetes TX-Delay + `flood.max` 15 + EWMA-SNR | ✅ aktiv | kürzere Pfade führen den Flood |
| **Best-of-N** | Ziel meldet kürzesten Pfad (Hops→SNR) statt „first wins" | ✅ aktiv | Detour-Killer, dedup-sicher |
| **Stufe B** | guarded Suppression (5 Guards + passives 2-Hop-Lernen) | 🔒 default-AUS | −12…15 % Airtime, Lieferquote ≥ Baseline |
| **Phase 2** | proaktiver DV-Backbone (Babel-Feasibility, Regionen) | 🔒 default-AUS | optimale Unicast-Pfade statt Flood |

🔒 = im Code, aber **default-aus** bis Bench-Test ([`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md)).
**Datenbelegt verworfen** (bringen über die Guards hinaus nichts): adaptiver Selbst-Regler, per-Node-
Kalibrierung, TX-Leistungsregelung — siehe Studien unten.

## 3. Ist das belegt? (Validierungs-Story)
Alles auf **echten CoreScope-Live-Daten** (109.980 Pakete, 1962 Knoten) simuliert — nicht nur Theorie:
- **Gemessen:** realer Median-Umweg 2,1× → belegt das Problem. ([`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md))
- **Komposit-Adoptions-Sweep** (1 %→100 % Knoten mit MHR): bis −12 % Airtime, monoton & sicher ab 1 Knoten. ([`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md))
- **Phase-2-Konvergenz-Gate: GO** (0 Schleifen, re-konvergiert unter Churn). ([`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md))
- Datensatz + Reproduktion: [`sim/README.md`](sim/README.md), Herkunft: [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md).

## 4. Wo anfangen?
- **Bauen/Flashen:** Repo-Root-README (PlatformIO / Web-Flasher) → `dist/`.
- **Auf Hardware testen:** [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) (gestuft, Akzeptanzkriterien; Stufe B/Phase 2 erst hier aktivieren).
- **Was genau geändert wurde:** [`CHANGES_MHR.md`](CHANGES_MHR.md) (Patch 1–9, mit CLI-Befehlen + Persistenz-Offsets).

---

## 5. Doku-Index (alle Dokumente)

**Analyse & Design**
- [`MeshCore_Routing_Analyse_und_Optimierung.md`](MeshCore_Routing_Analyse_und_Optimierung.md) — Ursachenanalyse der Umwege (Stufen A–D)
- [`MeshCore_Hybrid_Routing_Entwurf.md`](MeshCore_Hybrid_Routing_Entwurf.md) — MHR v1 (DSR + ETX + Best-of-N + Backbone)
- [`MeshCore_Hybrid_Routing_v2_Robustheit.md`](MeshCore_Hybrid_Routing_v2_Robustheit.md) — v2-Härtung aus Realdaten (H1–H7)
- [`study/Invisible_Optimizing_Layer.md`](study/Invisible_Optimizing_Layer.md) — **Architektur** der node-lokalen Schicht
- [`CHANGES_MHR.md`](CHANGES_MHR.md) — vollständige Patch-Liste 1–9
- [`BENCH_TEST_PLAN.md`](BENCH_TEST_PLAN.md) — Hardware-Bench-Test (Heltec V4)

**Studien & Validierungen** (`study/`)
- [`study/MeshCore_Routing_Study.md`](study/MeshCore_Routing_Study.md) — Mechanismus-Studie (Adoptions-Sweep, Tiering) · Begleit: [`study/STUDY_DESIGN.md`](study/STUDY_DESIGN.md), [`study/STUDY_RESULTS.md`](study/STUDY_RESULTS.md)
- [`study/Composite_Adoption_Study.md`](study/Composite_Adoption_Study.md) — die ganze Schicht bei 1/10/25/50/75/100 % Adoption
- [`study/Suppression_Design.md`](study/Suppression_Design.md) + [`study/SUPPRESSION_VALIDATION.md`](study/SUPPRESSION_VALIDATION.md) — Stufe B (5 Guards) Design + GO
- [`study/Phase2_Backbone_Design.md`](study/Phase2_Backbone_Design.md) + [`study/Backbone_Phase2_Study.md`](study/Backbone_Phase2_Study.md) + [`study/Phase2_Convergence_Validation.md`](study/Phase2_Convergence_Validation.md) — Phase 2 Design, Airtime-Ökonomie, Konvergenz-Gate
- [`study/Path_Reinforcement_Study.md`](study/Path_Reinforcement_Study.md) — Pfad-Erfolgs-Reinforcement (GO)
- [`study/Adaptive_Controller_Design.md`](study/Adaptive_Controller_Design.md) + [`study/Local_Calibration_Study.md`](study/Local_Calibration_Study.md) — adaptive Selbst-Anpassung (**NO-GO**, datenbelegt)

**Simulation & Daten** (`sim/`)
- [`sim/README.md`](sim/README.md) — wie man die Sims fährt + reiche CoreScope-Endpoints
- [`sim/MeshCore_Simulation_v3_Realdaten.md`](sim/MeshCore_Simulation_v3_Realdaten.md) — v3 auf 109.980 echten Paketen (Kernmessung)
- [`sim/data/PROVENANCE.md`](sim/data/PROVENANCE.md) — Datensatz-Herkunft, Schema, Attribution
- ältere Sims: [`MeshCore_Simulation_25Knoten.md`](MeshCore_Simulation_25Knoten.md), [`MeshCore_Simulation_ECHTE_Daten.md`](MeshCore_Simulation_ECHTE_Daten.md) *(illustrativ — v3/v4 sind die belastbare Referenz)*
- Skripte: `sim/mhr_sim*.py`, `sim/mhr_collect_corescope.py`, `study/*_sim.py`

> Leitprinzip durchgängig: **Qualität & Stabilität vor letzter Optimierung.** Jeder Eingriff ist lokal,
> reversibel, mischbetriebs-sicher und „nie schlechter als Upstream"; riskante Stufen sind default-aus
> und bench-gegated.
