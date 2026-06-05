# Studie: Routing-Optimierungen für MeshCore unter Mischbetrieb

**Leitfrage:** Welche Routing-Optimierungen senken Airtime und Umwege im realen MeshCore-Netz
*messbar*, **ohne** das Paketformat zu brechen und so, dass **jede Teil-Adoption** (ein einzelner
Knoten, einige, alle) mit unveränderter Original-Firmware koexistiert und das Netz **nie**
schlechter wird als heute? Priorität: **Qualität & Stabilität vor maximaler Optimalität.**

Basis: realer CoreScope-Datensatz (109.980 Pakete, 1962 Knoten, reale Topologie + reale Detours
Median 2,1×, siehe `../sim/MeshCore_Simulation_v3_Realdaten.md`).

---

## 1. Was die Realdaten über die Hebel sagen (Ausgangspunkt meines Denkens)

1. **Das Problem ist groß und real:** Median-Umweg 2,1×, Flood-Pfade bis 63 Hops, ~786
   Repeater senden pro Flood — das ist die eigentliche Airtime-Verschwendung.
2. **SNR ist ein schwacher Hebel:** Distanz erklärt SNR kaum (PLE≈0,4). „Starkes SNR ⇒ kürzerer
   Pfad" trägt nicht zuverlässig. ⇒ **Hop-Zahl ist das verlässlichere Signal** als SNR.
3. **Die Pfad-Hash-Kette ist Gold:** Jedes Flood-Paket trägt seine komplette Hop-Kette. Daraus
   lässt sich **passiv, ohne ein einziges Extra-Paket**, Topologie und Hop-Distanz lernen.
4. **Redundanz ist riesig:** In dichten Regionen senden Dutzende Repeater dieselbe Kopie. Hier
   liegt der größte Airtime-Hebel (Broadcast-Suppression).

Daraus folgt mein Grundprinzip: **Nutze das, was schon im Paket steht (Hop-Kette), entscheide
lokal, sende weniger — aber nie so, dass Erreichbarkeit verloren geht.**

---

## 2. Mischbetriebs-Sicherheit (die harte Nebenbedingung)

Jeder Mechanismus muss eine dieser Formen haben:
- **(L) Rein lokale Entscheidung** eines Knotens über sein *eigenes* Sende-/Cache-Verhalten —
  Alt-Knoten merken nichts davon. Kein Paketformat-Eingriff.
- **(P) Passive Beobachtung** vorhandener Felder (Pfad-Kette, SNR, Adverts) — null Extra-Airtime.
- **(O) Optionaler, ignorierbarer Payload-Typ** (nur falls nötig) — Alt-Knoten verwerfen ihn
  wirkungslos, Fallback bleibt Flood-and-cache.

**Verboten:** Änderungen an Dedup/Hash, die Alt-Knoten Nachrichten-Duplikate bescheren; neue
Pflichtfelder; alles, was bei α<100 % die Erreichbarkeit senkt.

**Safety-Invariante (für jeden Mechanismus, jedes α):** Lieferquote ≥ Baseline UND Airtime ≤
Baseline. Wird sie verletzt, ist der Mechanismus bei diesem α disqualifiziert.

---

## 3. Kandidaten-Mechanismen (konventionell → unkonventionell)

| ID | Mechanismus | Typ | Idee | Erwartung | Adoptions-Schwelle |
|----|-------------|-----|------|-----------|--------------------|
| **M0** | Baseline (Stock-Flood, first-wins) | — | Referenz | — | — |
| **M1** | **Hop-gewichtetes Rebroadcast-Delay** | L | Kopien mit *weniger* akkumulierten Hops senden früher → kürzere Pfade führen den Flood (ersetzt den schwachen SNR-Hebel) | weniger Umwege | hilft ab 1 Knoten, skaliert |
| **M2** | **Counter-based Broadcast-Suppression (Gossip)** | L | Repeater unterdrückt eigenen Rebroadcast, wenn er die Kopie während des Backoffs schon ≥k-mal gehört hat | starke Airtime-Senkung | braucht kritische Masse, Coverage-Risiko |
| **M3** | **Shorter-Path-Cancel (Overhear-Suppression mit Hop-Vergleich)** | L | Repeater verwirft seinen *anstehenden* Rebroadcast, wenn er dieselbe Kopie via *gleich kurzem/kürzerem* Pfad hört | Airtime ↓ ohne Coverage-Verlust | hilft ab wenigen, monoton |
| **M4** | **MPR/CDS-Relay-Reduktion (OLSR-Idee, passiv gelernt)** | L+P | Nur ein dominierender Teil-Satz Repeater flutet; Nicht-Relay-Neu-Knoten schweigen. 2-Hop-Nachbarschaft passiv aus Pfad-Ketten gelernt | größte Airtime-Senkung bei hoher Adoption | braucht Masse + lokale Topologie |
| **M5** | **Best-of-N am Ziel (nach Hops)** | L | Ziel sammelt kurz mehrere Kopien, meldet den *kürzesten* Pfad zurück (der bewusst zurückgestellte Phase-1-Kern) | weniger Umwege auf gecachten Pfaden | hilft ab 1 Ziel-Knoten |
| **M6** | **Passives Topologie-Lernen + Feasible-Successor** | P | Aus Pfad-Ketten lokale Link-Tabelle bauen (0 Airtime); bei Pfadbruch lokalen Backup statt Re-Flood | weniger Re-Discovery-Airtime, Stabilität | hilft ab 1 Knoten |
| **M7** | **flood.max empirisch senken (12–15)** | L | Hop-Limit an realen Netzdurchmesser (Median 10, P90 18); tötet Fern-Umweg-Kopien | Airtime ↓, kappt Extrem-Detours | wirkt pro Knoten |

Kombinations-Hypothese: **M3+M5+M7** sind alle „ab 1 Knoten hilfreich & monoton" → idealer
inkrementeller Rollout. **M2/M4** sind die großen Airtime-Hebel, brauchen aber kritische Masse —
genau das soll der Adoptions-Sweep quantifizieren.

---

## 4. Experiment-Design (der Adoptions-Sweep — Kern der Studie)

**Topologie:** reale aktive Repeater-Komponente aus v3 (≈831 Knoten) + kalibrierter Link-/
Reichweiten-Graph; Quervalidierung gegen die *beobachteten* Kanten (`topology_edges.json`).

**Adoptionsanteil α (neue Firmware):** {0 (=Baseline), 1 Knoten, 1 %, 5 %, 10 %, 25 %, 50 %, 100 %}.
Zuweisung zufällig **und** gezielt (Top-Traffic-Repeater zuerst — realistischer Rollout), je über
mehrere Seeds gemittelt.

**Pro (Mechanismus, α) gemessen:**
- **Airtime** = Σ Rebroadcasts pro zugestellter Nachricht (der eigentliche Engpass)
- **Lieferquote** (erreichte Ziele / Versuche)
- **Hops / Detour-Ratio** des genutzten Pfads vs. kürzester bekannter
- **Routen-Stabilität** (Pfadwechsel über wiederholte Sendungen / unter Churn)
- **Safety-Flag:** Lieferquote ≥ Baseline? Airtime ≤ Baseline? (sonst disqualifiziert bei α)

**Mischbetriebs-Semantik:** Neu-Knoten wenden ihre Regel an; Stock-Knoten fluten normal —
sie garantieren Konnektivität, falls Neu-Knoten zu aggressiv unterdrücken (das ist die
eingebaute Sicherheit, die der Sweep belegen muss).

**Stress (Stabilität):** zusätzlich Churn (nach advert_count) und Linkausfall bei ausgewählten α,
um zu prüfen, dass die Gewinne nicht in Flattern/Partition kippen.

**Erfolgskriterium:** Mechanismen werden gerankt nach (i) Airtime-Gewinn bei gehaltener
Lieferquote, (ii) **Monotonie & Safety** über α (Qualitäts-/Stabilitätspriorität), (iii)
Firmware-Machbarkeit im Mischbetrieb.

---

## 5. Was ich erwarte (Hypothesen, vor der Messung)

- M3, M5, M7: kleiner, aber **monoton sicherer** Gewinn schon ab Einzelknoten → die „guten
  Bürger", die man bedenkenlos ausrollt.
- M2/M4: die großen Airtime-Sprünge (potenziell −50…−90 % Flood-TX), aber erst ab ~10–25 %
  Adoption spürbar; Risiko, dass bei niedrigem α nichts passiert und bei sehr hohem α (falls
  *alle* unterdrücken) Coverage leidet — Stock-Knoten als Sicherheitsnetz sollten das abfangen.
- Der unkonventionelle Liebling: **M6 passives Topologie-Lernen** — kostet *null* Airtime, weil
  die Daten schon fliegen, und ermöglicht später gerichtetes Forwarding/Backup ohne Protokoll-
  änderung. Hohe Hebelwirkung als Fundament.

Die Messung entscheidet — nicht die Intuition.

---
## 🇬🇧 English Translation

# Study: Routing Optimizations for MeshCore in Mixed-Firmware Operation

**Guiding question:** Which routing optimizations reduce airtime and detours in the real MeshCore
network in a *measurable* way, **without** breaking the packet format and in such a way that
**every partial adoption** (a single node, some, all) coexists with unchanged original firmware
and the network **never** performs worse than today? Priority: **quality & stability over maximum
optimality.**

Basis: real CoreScope dataset (109,980 packets, 1,962 nodes, real topology + real detours
median 2.1×, see `../sim/MeshCore_Simulation_v3_Realdaten.md`).

---

## 1. What the Real-World Data Says About the Levers (Starting Point of My Thinking)

1. **The problem is large and real:** Median detour 2.1×, flood paths up to 63 hops, ~786
   repeaters transmitting per flood — that is the actual airtime waste.
2. **SNR is a weak lever:** Distance barely explains SNR (PLE≈0.4). "Strong SNR ⇒ shorter
   path" does not hold reliably. ⇒ **Hop count is the more reliable signal** than SNR.
3. **The path hash chain is gold:** Every flood packet carries its complete hop chain. From this,
   topology and hop distance can be learned **passively, without a single extra packet**.
4. **Redundancy is enormous:** In dense regions, dozens of repeaters transmit the same copy. This
   is where the biggest airtime lever lies (broadcast suppression).

From this follows my core principle: **Use what is already in the packet (hop chain), decide
locally, transmit less — but never in a way that causes reachability to be lost.**

---

## 2. Mixed-Firmware Safety (the Hard Constraint)

Every mechanism must take one of these forms:
- **(L) Purely local decision** by a node about its *own* transmit/cache behavior —
  legacy nodes notice nothing. No packet format changes.
- **(P) Passive observation** of existing fields (path chain, SNR, adverts) — zero extra airtime.
- **(O) Optional, ignorable payload type** (only if necessary) — legacy nodes discard it
  harmlessly, fallback remains flood-and-cache.

**Forbidden:** changes to dedup/hash that cause legacy nodes to receive message duplicates; new
mandatory fields; anything that reduces reachability at α<100%.

**Safety invariant (for every mechanism, every α):** delivery rate ≥ baseline AND airtime ≤
baseline. If violated, the mechanism is disqualified at that α.

---

## 3. Candidate Mechanisms (conventional → unconventional)

| ID | Mechanism | Type | Idea | Expectation | Adoption Threshold |
|----|-----------|------|------|-------------|-------------------|
| **M0** | Baseline (stock flood, first-wins) | — | Reference | — | — |
| **M1** | **Hop-weighted rebroadcast delay** | L | Copies with *fewer* accumulated hops transmit earlier → shorter paths lead the flood (replaces the weak SNR lever) | fewer detours | helps from 1 node, scales |
| **M2** | **Counter-based broadcast suppression (gossip)** | L | Repeater suppresses its own rebroadcast if it has already heard the copy ≥k times during backoff | strong airtime reduction | needs critical mass, coverage risk |
| **M3** | **Shorter-path-cancel (overhear suppression with hop comparison)** | L | Repeater discards its *pending* rebroadcast when it hears the same copy via an *equally short/shorter* path | airtime ↓ without coverage loss | helps from a few, monotone |
| **M4** | **MPR/CDS relay reduction (OLSR idea, passively learned)** | L+P | Only a dominating subset of repeaters floods; non-relay new nodes stay silent. 2-hop neighborhood passively learned from path chains | greatest airtime reduction at high adoption | needs mass + local topology |
| **M5** | **Best-of-N at destination (by hops)** | L | Destination briefly collects multiple copies, reports back the *shortest* path (the deliberately deferred Phase-1 core) | fewer detours on cached paths | helps from 1 destination node |
| **M6** | **Passive topology learning + feasible successor** | P | Build local link table from path chains (0 airtime); on path break use local backup instead of re-flood | less re-discovery airtime, stability | helps from 1 node |
| **M7** | **flood.max empirically reduced (12–15)** | L | Hop limit aligned to real network diameter (median 10, P90 18); kills far-detour copies | airtime ↓, caps extreme detours | takes effect per node |

Combination hypothesis: **M3+M5+M7** are all "helpful & monotone from 1 node" → ideal
incremental rollout. **M2/M4** are the big airtime levers, but need critical mass —
exactly what the adoption sweep is meant to quantify.

---

## 4. Experiment Design (the Adoption Sweep — Core of the Study)

**Topology:** real active repeater component from v3 (≈831 nodes) + calibrated link/range
graph; cross-validated against the *observed* edges (`topology_edges.json`).

**Adoption fraction α (new firmware):** {0 (=baseline), 1 node, 1%, 5%, 10%, 25%, 50%, 100%}.
Assignment both random **and** targeted (top-traffic repeaters first — realistic rollout),
each averaged over multiple seeds.

**Measured per (mechanism, α):**
- **Airtime** = Σ rebroadcasts per delivered message (the actual bottleneck)
- **Delivery rate** (destinations reached / attempts)
- **Hops / detour ratio** of the used path vs. shortest known
- **Route stability** (path changes across repeated transmissions / under churn)
- **Safety flag:** delivery rate ≥ baseline? Airtime ≤ baseline? (otherwise disqualified at α)

**Mixed-firmware semantics:** new nodes apply their rule; stock nodes flood normally —
they guarantee connectivity if new nodes suppress too aggressively (this is the
built-in safety net that the sweep must demonstrate).

**Stress (stability):** additionally churn (by advert_count) and link failure at selected α,
to verify that the gains do not collapse into flapping/partition.

**Success criterion:** mechanisms are ranked by (i) airtime gain with delivery rate held,
(ii) **monotonicity & safety** across α (quality/stability priority), (iii)
firmware feasibility in mixed-firmware operation.

---

## 5. What I Expect (Hypotheses, Before Measurement)

- M3, M5, M7: small but **monotonically safe** gain already from a single node → the "good
  citizens" that can be rolled out without concern.
- M2/M4: the large airtime jumps (potentially −50…−90% flood TX), but only noticeable from
  ~10–25% adoption; risk that at low α nothing happens and at very high α (if *all* suppress)
  coverage suffers — stock nodes as safety net should catch this.
- The unconventional favourite: **M6 passive topology learning** — costs *zero* airtime because
  the data is already in flight, and enables directed forwarding/backup later without protocol
  changes. High leverage as a foundation.

Measurement decides — not intuition.
