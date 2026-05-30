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
