# MHR v2 – Härtung für Robustheit & Stabilität (aus Realdaten)

Überarbeitung des MHR-Entwurfs auf Basis der **echten CoreScope-Beobachtungen** (Live-Abruf Raum Bonn–Rhein-Sieg–Siebengebirge–Lohmar–Leverkusen). Die Realität weicht an mehreren Stellen von den Annahmen der v1 ab – v2 schließt genau diese Lücken.

---

## 1. Was die Realität gezeigt hat – und was daraus folgt

| Beobachtung (echt) | Annahme in v1 | Implikation für v2 |
|---|---|---|
| **80 Repeater** vs. nur 20 Companions im aktiven Gebiet | „wenige stabile Repeater, viele Clients" | Backbone ist **groß & dicht** → naives flächiges DV skaliert nicht; Hierarchie nötig |
| Pfade bis **8 Hops**, ~50 km gestreckt | kurze Backbone-Pfade | Lange Ketten → **Schleifenfreiheit & Konvergenz** werden kritisch |
| Ende-zu-Ende-Zuverlässigkeit nur **0,58** | Links „meist ok" | Metrik muss **Zuverlässigkeit** vor Hopzahl gewichten; Redundanz nötig |
| advert_count **7 … 455** (extrem heterogen) | Repeater ~gleich stabil | Flatternde Knoten dürfen **nicht als Transit** dienen → Stabilitäts-Gating |
| 1 Knoten **funkisoliert** (Leverkusen) | Backbone zusammenhängend | **Partitionstoleranz** + sauberer Reactive-Fallback Pflicht |
| Doubletten (Lohmar #17 ×2), „Pending", **2-Byte-Hashes**, multi-byte-Status gemischt | sauberes, homogenes Netz | **Mixed-Firmware-Koexistenz** + Müll-/Doublettenhärtung als Erstklasse-Anforderung |
| Deployment nutzt **Regionen** (de-nw, rheinland, bonn, koeln, eifel, leverkusen) zum Scoping | Regionen ignoriert | Regionen als **fertige Cluster-Grenzen** wiederverwenden |

Kernbefund der Sim (echte Topologie): **60 % der Pfadaufbauten** liefen auf Umwege, **−82 % Airtime** mit MHR – der Gewinn ist real groß, aber v1 wäre in diesem großen, lossy, heterogenen Netz **instabil** geworden (DV-Last, Flattern, Schleifen). v2 priorisiert daher Stabilität über letzte Optimalität.

---

## 2. Die sieben Härtungen

### H1 – Regions-Hierarchie statt flachem Backbone *(skaliert auf 80+ Repeater)*
Statt ein DV über alle Repeater zu fahren, werden die **bereits existierenden Regionen** als Cluster genutzt:

- **Intra-Region:** proaktives DV nur innerhalb einer Region (z. B. „bonn", „koeln") – wenige Knoten, schnelle Konvergenz, geringe Last.
- **Inter-Region:** wenige **Border-Repeater** (Knoten, die Adverts mehrerer Regionen hören) bilden einen dünnen Backbone-2.-Ordnung. Nur sie tauschen *aggregierte* „Region erreichbar über mich, Kosten X"-Einträge aus.

Effekt: DV-Tabellen und Kontroll-Traffic skalieren mit *Regionsgröße*, nicht mit Gesamtnetz. Das ist klassische **hierarchische/Area-Routing**-Logik (OSPF-Areas-Idee), aufgesetzt auf die reale Regions-Struktur. Code-seitig: Region wird heute schon pro Paket bestimmt (`region_map.findMatch`, `filterRecvFloodPacket`) – die Information ist vorhanden.

### H2 – Schleifenfreie Konvergenz (Babel-Feasibility statt reinem DSDV) *(lange Ketten)*
Bei bis zu 8 Hops auf lossy Links sind transiente Schleifen teuer. v2 übernimmt die **Feasibility-Bedingung** von Babel/EIGRP: ein Nachbar wird nur dann als Next-Hop akzeptiert, wenn seine annoncierten Kosten **echt kleiner** sind als die zuletzt selbst erreichten (feasible distance). Plus **Sequenznummern** je Ziel. Das garantiert Schleifenfreiheit *während* der Konvergenz – nicht nur danach – ohne globales Topologiebild.

### H3 – Feasible-Successor-Backup *(Partitions-/Linkausfall-Toleranz)*
Jeder Repeater hält pro Ziel **zwei** Routen: primären Next-Hop + einen vorab als schleifenfrei validierten **Backup-Next-Hop**. Fällt der primäre Hop aus, wird ohne neue Flutung sofort auf den Backup umgeschaltet (EIGRP-Prinzip). Das adressiert direkt die beobachteten Ausfälle/Isolation: ein einzelner Linkverlust löst **keine** teure Re-Discovery mehr aus.

### H4 – Metrik-Härtung: zuverlässigkeitsdominant, gedämpft, knoten-bewusst *(0,58-Reliability + Flattern)*
Die ETX-Metrik aus v1 wird gehärtet:

- **Zuverlässigkeit dominiert Hopzahl:** Kosten = ETX (nicht Hops); ein 3-Hop-Weg mit guten Links schlägt einen 2-Hop-Weg mit Wackel-Link.
- **EWMA + Hysterese:** Linkkosten werden geglättet, und ein neuer Pfad ersetzt den alten nur, wenn er **spürbar** besser ist (z. B. ≥ 15 %). Das verhindert das Hin-und-Her-Flattern, das auf lossy Links sonst entsteht. (Gegenstück zum „klebrigen Pfad" aus der Analyse – v2 sucht das stabile Mittel: wechseln bei *deutlicher* Verbesserung, sonst halten.)
- **Knoten-Stabilitäts-Gating:** aus der Advert-Regelmäßigkeit (real: advert_count 7…455) wird ein Verfügbarkeits-Score gebildet. Knoten unter Schwelle dürfen **Endpunkt**, aber **nicht bevorzugter Transit** sein. Flatternde Repeater vergiften so keine Backbone-Routen.

### H5 – Zuverlässigkeits-Untergrenze + Hop-für-Hop-Retry *(Lieferquote)*
Best-of-N wählt nicht nur „beste Metrik", sondern verwirft Pfade unter einer **geschätzten Lieferwahrscheinlichkeits-Schwelle** (z. B. < 0,5) zugunsten eines etwas längeren, aber zuverlässigeren Wegs. Auf dem Backbone bleibt MeshCores Hop-für-Hop-Direct-Retry aktiv; optional **Dual-Path** für wichtige Nachrichten (zwei disjunkte Routen) – bewusst nur on demand, da es Airtime kostet.

### H6 – Mixed-Firmware-Koexistenz als Erstklasse-Anforderung *(2-Byte-Hashes, „Pending", Forks)*
Real laufen verschiedene Firmware-Stände und Hash-Größen nebeneinander. v2 fordert:

- DV-/Backbone-Pakete als **eigener, ignorierbarer Payload-Typ** → Alt-Knoten verwerfen sie wirkungslos.
- Wo MHR-Knoten fehlen oder die Region keine Backbone-Route hat, **automatischer Rückfall auf das heutige Flood-and-cache**. MHR darf das bestehende Verhalten **nie** verschlechtern, nur ergänzen.
- Respektiere die vorhandene **Hash-Size-Migration** (1/2/3-Byte) und Loop-Detection.

### H7 – Müll-, Doubletten- & Missbrauchshärtung *(messy reality)*
- **Doubletten** (gleicher Schlüssel/quasi-gleiche Koordinaten, vgl. Lohmar #17 ×2) werden über den Public-Key dedupliziert, nicht über den Namen.
- **Rate-Limiting** der DV-Updates pro Nachbar (greift Hand in Hand mit dem bestehenden `discover_limiter`).
- Sequenznummern + Plausibilitätsprüfung verhindern, dass ein einzelner fehlkonfigurierter/böser Knoten Routen vergiftet oder einen Paketsturm auslöst (knüpft an die reale Loop-Detection-Begründung in den MeshCore-Docs an).

---

## 3. Architektur v2 (kompakt)

```
L2  Client-Kante  : reaktiv, attach an Region-Repeater, Discovery-Short-Circuit
                    -> Fallback auf Flood, wenn keine Backbone-Route
L1b Inter-Region  : Border-Repeater, aggregiertes DV "Region X via mich, Kosten"
L1a Intra-Region  : proaktives DV, Babel-Feasibility + Seqno + Feasible-Successor
L0  Link-Sensing  : EWMA-ETX (zuverlässigkeitsdominant) + Knoten-Stabilitäts-Score
```

Leitprinzip v2: **Stabilität vor letzter Optimalität.** Lieber ein leicht suboptimaler, aber stabiler und schleifenfreier Pfad als ein theoretisch kürzester, der flattert. Genau das fehlt MeshCore heute (Zufalls-Umwege) *und* würde einer naiven v1 in diesem Netz fehlen (DV-Instabilität).

---

## 4. Abdeckung der Fehlermodi (v1 → v2)

| Fehlermodus | v1 | v2 |
|---|---|---|
| DV skaliert nicht auf 80+ Repeater | Risiko | **H1** Regions-Hierarchie |
| Transiente Schleifen auf langen Pfaden | seqno/split-horizon | **H2** Feasibility-Bedingung |
| Linkausfall → teure Re-Discovery | Re-Flood | **H3** Backup-Successor |
| Routen-Flattern auf lossy Links | – | **H4** EWMA+Hysterese |
| Unzuverlässige/flatternde Transit-Knoten | – | **H4** Stabilitäts-Gating |
| Niedrige Lieferquote | kürzester Pfad | **H5** Reliability-Floor + Retry/Dual-Path |
| Mixed-Firmware/Hashes | „graceful" (vage) | **H6** harte Anforderung + Fallback |
| Doubletten/Müll/Missbrauch | – | **H7** Dedup + Rate-Limit + Seqno |
| Backbone-Partition | Fallback | **H3 + H6** Backup + Reactive-Fallback |

---

## 5. Erwartete Wirkung & Validierung

v2 zielt nicht auf bessere Bestwerte als v1, sondern auf **gehaltene Gewinne unter realen Störungen**: die ~82 % Airtime-Ersparnis und die Umweg-Reduktion sollen auch bei Knoten-Churn, Linkausfällen und gemischter Firmware *stabil* bleiben, statt in Flattern/Schleifen zu kippen.

**Validierungsplan (nächster Schritt):** die bestehende Real-Daten-Sim (`sim/mhr_sim_real.py`) um Störszenarien erweitern und v1 vs. v2 vergleichen:

- **Churn:** Knoten gemäß ihrem realen advert_count-Profil zufällig an/abschalten → Routen-Wechselrate (Flattern) und Lieferquote messen.
- **Linkausfall:** zufällige Links/Hochlast-Knoten ausfallen lassen → Re-Discovery-Häufigkeit (v1) vs. Backup-Umschaltung (v2).
- **Partition:** isolierte Knoten (real: Leverkusen) → sauberer Fallback statt Endlos-Flood?
- **Metriken:** Routen-Stabilität (Wechsel/Stunde), Konvergenzzeit, Airtime unter Störung, Lieferquote.

---

*Baut auf `MeshCore_Hybrid_Routing_Entwurf.md` (v1), `MeshCore_Simulation_ECHTE_Daten.md` und `MeshCore_Routing_Analyse_und_Optimierung.md` auf. Der 4-Phasen-Rollout aus v1 gilt weiter; H1/H2/H3 gehören in Phase 2 (Backbone), H4/H5 in Phase 1, H6/H7 durchgängig.*

---
## 🇬🇧 English Translation

# MHR v2 – Hardening for Robustness & Stability (from Real Data)

Revision of the MHR design based on **real CoreScope observations** (live snapshot, Bonn–Rhein-Sieg–Siebengebirge–Lohmar–Leverkusen area). Reality deviates from the v1 assumptions in several places – v2 closes exactly those gaps.

---

## 1. What Reality Has Shown – and What Follows from It

| Observation (real) | Assumption in v1 | Implication for v2 |
|---|---|---|
| **80 repeaters** vs. only 20 companions in the active area | "few stable repeaters, many clients" | Backbone is **large & dense** → naive flat DV does not scale; hierarchy needed |
| Paths up to **8 hops**, ~50 km stretched | short backbone paths | Long chains → **loop-freedom & convergence** become critical |
| End-to-end reliability only **0.58** | links "mostly ok" | Metric must weight **reliability** over hop count; redundancy needed |
| advert_count **7 … 455** (extremely heterogeneous) | repeaters ~equally stable | Flapping nodes must **not serve as transit** → stability gating |
| 1 node **RF-isolated** (Leverkusen) | backbone connected | **Partition tolerance** + clean reactive fallback mandatory |
| Duplicates (Lohmar #17 ×2), "Pending", **2-byte hashes**, multi-byte status mixed | clean, homogeneous network | **Mixed-firmware coexistence** + garbage/duplicate hardening as first-class requirement |
| Deployment uses **regions** (de-nw, rheinland, bonn, koeln, eifel, leverkusen) for scoping | regions ignored | Reuse regions as **ready-made cluster boundaries** |

Key finding from the sim (real topology): **60% of path setups** took detours, **−82% airtime** with MHR – the gain is genuinely large, but v1 would have become **unstable** in this large, lossy, heterogeneous network (DV load, flapping, loops). v2 therefore prioritizes stability over last-mile optimality.

---

## 2. The Seven Hardenings

### H1 – Regional Hierarchy Instead of a Flat Backbone *(scales to 80+ repeaters)*
Instead of running DV across all repeaters, the **already-existing regions** are used as clusters:

- **Intra-region:** proactive DV only within one region (e.g. "bonn", "koeln") – few nodes, fast convergence, low load.
- **Inter-region:** a small number of **border repeaters** (nodes that hear adverts from multiple regions) form a thin second-order backbone. Only they exchange *aggregated* "region reachable via me, cost X" entries.

Effect: DV tables and control traffic scale with *region size*, not with the total network. This is classic **hierarchical/area routing** logic (OSPF-areas idea), applied to the real region structure. On the code side: region is already determined per packet today (`region_map.findMatch`, `filterRecvFloodPacket`) – the information is available.

### H2 – Loop-Free Convergence (Babel Feasibility Instead of Pure DSDV) *(long chains)*
With up to 8 hops on lossy links, transient loops are expensive. v2 adopts the **feasibility condition** from Babel/EIGRP: a neighbor is only accepted as a next-hop if its advertised cost is **strictly less** than the locally last-reached cost (feasible distance). Plus **sequence numbers** per destination. This guarantees loop-freedom *during* convergence – not just after – without a global topology view.

### H3 – Feasible-Successor Backup *(partition/link-failure tolerance)*
Each repeater maintains **two** routes per destination: primary next-hop + a pre-validated loop-free **backup next-hop**. If the primary hop fails, the backup is switched to immediately without a new flood (EIGRP principle). This directly addresses the observed failures/isolation: a single link loss no longer triggers **any** expensive re-discovery.

### H4 – Metric Hardening: Reliability-Dominant, Damped, Node-Aware *(0.58 reliability + flapping)*
The ETX metric from v1 is hardened:

- **Reliability dominates hop count:** cost = ETX (not hops); a 3-hop path with good links beats a 2-hop path with a shaky link.
- **EWMA + hysteresis:** link costs are smoothed, and a new path only replaces the old one when it is **noticeably** better (e.g. ≥ 15%). This prevents the back-and-forth flapping that otherwise occurs on lossy links. (Counterpart to the "sticky path" from the analysis – v2 seeks the stable middle ground: switch on *clear* improvement, otherwise hold.)
- **Node stability gating:** from the advert regularity (real: advert_count 7…455), an availability score is formed. Nodes below the threshold may be **endpoints**, but **not preferred transit**. Flapping repeaters thus do not poison backbone routes.

### H5 – Reliability Floor + Hop-by-Hop Retry *(delivery ratio)*
Best-of-N does not just select "best metric", but discards paths below an **estimated delivery probability threshold** (e.g. < 0.5) in favor of a slightly longer but more reliable path. On the backbone, MeshCore's hop-by-hop direct retry remains active; optionally **dual-path** for important messages (two disjoint routes) – deliberately on-demand only, as it costs airtime.

### H6 – Mixed-Firmware Coexistence as a First-Class Requirement *(2-byte hashes, "Pending", forks)*
In reality, different firmware versions and hash sizes run side by side. v2 requires:

- DV/backbone packets as **their own, ignorable payload type** → legacy nodes discard them without effect.
- Where MHR nodes are absent or the region has no backbone route, **automatic fallback to today's flood-and-cache**. MHR must **never** degrade existing behavior, only complement it.
- Respect the existing **hash-size migration** (1/2/3-byte) and loop detection.

### H7 – Garbage, Duplicate & Abuse Hardening *(messy reality)*
- **Duplicates** (same key/quasi-identical coordinates, cf. Lohmar #17 ×2) are deduplicated via public key, not name.
- **Rate-limiting** of DV updates per neighbor (works hand in hand with the existing `discover_limiter`).
- Sequence numbers + plausibility checks prevent a single misconfigured/malicious node from poisoning routes or triggering a packet storm (ties into the real loop-detection rationale in the MeshCore docs).

---

## 3. v2 Architecture (Compact)

```
L2  Client edge   : reactive, attach to region repeater, discovery short-circuit
                    -> fallback to flood if no backbone route
L1b Inter-region  : border repeaters, aggregated DV "region X via me, cost"
L1a Intra-region  : proactive DV, Babel-feasibility + seqno + feasible-successor
L0  Link sensing  : EWMA-ETX (reliability-dominant) + node stability score
```

Guiding principle of v2: **Stability over last-mile optimality.** A slightly suboptimal but stable and loop-free path is preferable to a theoretically shortest path that flaps. This is exactly what MeshCore lacks today (random detours) *and* what a naive v1 would lack in this network (DV instability).

---

## 4. Failure Mode Coverage (v1 → v2)

| Failure mode | v1 | v2 |
|---|---|---|
| DV does not scale to 80+ repeaters | risk | **H1** regional hierarchy |
| Transient loops on long paths | seqno/split-horizon | **H2** feasibility condition |
| Link failure → expensive re-discovery | re-flood | **H3** backup successor |
| Route flapping on lossy links | – | **H4** EWMA+hysteresis |
| Unreliable/flapping transit nodes | – | **H4** stability gating |
| Low delivery ratio | shortest path | **H5** reliability floor + retry/dual-path |
| Mixed firmware/hashes | "graceful" (vague) | **H6** hard requirement + fallback |
| Duplicates/garbage/abuse | – | **H7** dedup + rate-limit + seqno |
| Backbone partition | fallback | **H3 + H6** backup + reactive fallback |

---

## 5. Expected Impact & Validation

v2 does not aim for better peak values than v1, but for **sustained gains under real disturbances**: the ~82% airtime savings and the detour reduction should remain *stable* even under node churn, link failures, and mixed firmware, rather than tipping into flapping/loops.

**Validation plan (next step):** extend the existing real-data sim (`sim/mhr_sim_real.py`) with disturbance scenarios and compare v1 vs. v2:

- **Churn:** randomly turn nodes on/off according to their real advert_count profile → measure route change rate (flapping) and delivery ratio.
- **Link failure:** drop random links/high-load nodes → re-discovery frequency (v1) vs. backup switchover (v2).
- **Partition:** isolated nodes (real: Leverkusen) → clean fallback instead of endless flood?
- **Metrics:** route stability (changes/hour), convergence time, airtime under disturbance, delivery ratio.

---

*Builds on `MeshCore_Hybrid_Routing_Entwurf.md` (v1), `MeshCore_Simulation_ECHTE_Daten.md`, and `MeshCore_Routing_Analyse_und_Optimierung.md`. The 4-phase rollout from v1 still applies; H1/H2/H3 belong in Phase 2 (backbone), H4/H5 in Phase 1, H6/H7 throughout.*
