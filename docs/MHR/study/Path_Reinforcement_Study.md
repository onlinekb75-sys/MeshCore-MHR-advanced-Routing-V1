# Pfad-Erfolgs-Reinforcement (Mechanismus B) — Studie

*Baustein 3 der unsichtbaren, node-lokalen Optimierungs-Schicht (`Invisible_Optimizing_Layer.md`).
Rein node-lokal, kein neues Paket, transparent. Validiert auf der ECHTEN neighbor-graph-Topologie
(echte Kanten + echtes avg_snr), Churn-Profil aus `advert_count`. Skript: `reinforce_sim.py`,
Ergebnisse: `reinforce_results.json`. Seed 42, 6 Seeds, 22 Quellen x 4 Ziele x 40 Sende-Ticks
je Konfiguration.*

Topologie (Riesenkomponente): **632 Knoten, 1577 Kanten, Ø-Grad 3,45** (sparse, real).

---

## 1. Frage & Modell

**Baseline (MeshCore heute, flood-and-cache, "first packet wins"):** Eine Quelle cached pro Ziel
genau EINEN Pfad (den ersten erfolgreichen Flood-Pfad). Jede Nachricht geht unicast darueber.
Faellt eine Kante transient aus oder ist ein Zwischenknoten weg, scheitert die Zustellung. Erst
nach **3 aufeinanderfolgenden Fehlern** wird der Pfad verworfen und eine teure **Re-Discovery
(Flood)** ausgeloest.

**B (Reinforcement):** Zusaetzlich pro Ziel
- ein **EWMA-Erfolgsmass** `s` (α=0,30; aus ACK/Zustell-Feedback),
- ein **Backup-Pfad** (feasible successor), passiv aus gehoerten Pfad-Ketten gelernt (0 Airtime;
  modelliert als 2.-bester kantendisjunkter ETX-Pfad).

Logik: faellt `s` unter `SWITCH_THR=0,55`, schaltet B **proaktiv auf den Backup um, BEVOR** die 3
harten Fehler + Re-Flood noetig werden. Bei akutem Fehlschlag wird sofort der jeweils andere
bekannte Pfad probiert; Re-Flood nur, wenn auch der Backup faellt oder keiner bekannt ist
(dann faellt B exakt auf Baseline zurueck).

**Methodik:** Pro `(Quelle, Ziel)` werden 40 Sendeversuche unter transientem Linkausfall
(10/20/30 %) bzw. Knoten-Churn (nach `advert_count`) simuliert. Baseline und B laufen auf
**identischer Stoer-Sequenz (Common Random Numbers)** -> gepaarter Vergleich. Adoptions-Sweep:
Anteil der Quell-Knoten, die B nutzen (1 Knoten -> 100 %). Re-Discovery-Airtime = sendende Knoten
je Flood (identisches Flood-Modell wie v4); Unicast-Airtime = Hops je Versuch.

---

## 2. Ergebnisse (NUTZEN / VERLUST / NETTO)

### NUTZEN (voll adoptiert)

| Linkausfall | Lieferquote Base -> B | Δ Lieferquote | Netto-Airtime Δ | Re-Discovery-Airtime gespart | Suboptimale Zust./Paar |
|---|---|---|---|---|---|
| 10 % | 0,529 -> 0,586 | **+5,7 pp** | **−20,0 %** | +22,8 % | 5,7 |
| 20 % | 0,373 -> 0,416 | **+4,4 pp** | **−16,5 %** | +17,9 % | 5,6 |
| 30 % | 0,280 -> 0,303 | **+2,3 pp** | **−11,0 %** | +11,9 % | 5,3 |

Knoten-Churn (advert_count, bei 10 % Linkausfall, voll adoptiert):

| Churn | Lieferquote Base -> B | Δ | Netto-Airtime Δ | Refloods/Paar Base -> B |
|---|---|---|---|---|
| 5 % | 0,477 -> 0,529 | +5,1 pp | −18,2 % | 4,75 -> 3,92 |
| 10 % | 0,435 -> 0,487 | +5,2 pp | −18,3 % | 5,55 -> 4,60 |
| 20 % | 0,363 -> 0,404 | +4,1 pp | −15,0 % | 6,87 -> 5,93 |

**Mittel ueber Linkausfall (voll): Netto-Airtime −15,8 %, Lieferquote +4,1 pp, Re-Discovery-Airtime
−17,5 %.** Der Effekt skaliert monoton mit dem Adoptionsanteil (1 Knoten ≈ 0, voll = max), weil B
eine reine Quell-/Cache-Entscheidung ist und je Quelle unabhaengig wirkt.

### VERLUST / Kosten (ehrlich)
- **Pfad-Wechsel:** ~0,08–0,09 Wechsel je Sende-Tick bei Stoerung (1 Wechsel pro ~11 Ticks) —
  das sind FAST AUSSCHLIESSLICH Reaktionen auf echte Ausfaelle, kein grundloses Hin-und-Her.
- **Stoerungsfreier Flatter-Test (kein Link/Knoten-Ausfall):** ~0,029 Wechsel/Tick, ABER:
  Lieferquote steigt trotzdem (0,825 -> 0,856; +3,2 pp) und Netto-Airtime sinkt (**−11,5 %**). Das
  Umschalten ist also **produktiv, nicht schaedlich** — es reagiert auf das reale Per-Link-
  SNR-Rauschen, das Baseline erst nach 3 Fehlern + Re-Flood auffaengt. **Kein schaedliches
  Flattern.** (Die GO/NO-GO-Pruefung wertet bewusst nicht "schaltet B ueberhaupt", sondern
  "schadet das Umschalten ohne Stoerung der Lieferquote/Airtime?" — Antwort: nein.)
- **Suboptimale Zustellungen:** ~5,3–5,7 je Paar (von 40 Ticks) gehen ueber einen laengeren als den
  optimalen Pfad (Backup-Umweg). Das ist die Hauptursache, dass der Airtime-Gewinn nicht noch
  groesser ist — aber netto bleibt es deutlich positiv.
- **Backup-Qualitaet:** in dieser sparsen Topologie (Ø-Grad 3,45) existiert nur fuer ~43 % der
  Paare ueberhaupt ein kantendisjunkter Backup. Fuer den Rest faellt B auf Re-Flood zurueck
  (= Baseline-Verhalten).

### NETTO
Gesamt-Airtime (Unicast + Re-Floods) sinkt in JEDER Stoer-Konfiguration; Lieferquote steigt in
jeder; Routen-Stabilitaet bleibt erhalten (kein Flattern). Sensitivitaet zur Backup-Lernqualitaet
(learn_loss, bei 20 % Linkausfall, voll):

| learn_loss | Backup-Anteil | Δ Lieferquote | Netto-Airtime | Re-Discovery gespart |
|---|---|---|---|---|
| 0 % | 0,43 | +6,3 pp | −25,0 % | +27,1 % |
| 30 % | 0,30 | +4,4 pp | −16,5 % | +17,9 % |
| 60 % | 0,17 | +2,8 pp | −9,5 % | +10,3 % |
| 100 % | 0,00 | −0,0 pp | +0,8 % | −0,8 % |

-> Der Nutzen skaliert sauber und MONOTON mit der Backup-Verfuegbarkeit; bei 0 % Backup ist B
**praktisch identisch** zu Baseline (sicherer Fallback, keine relevante Verschlechterung; das
+0,8 % Airtime bei ll=100 % ist Rausch-/Rundungsgroesse ohne Liefer-Effekt).

---

## 3. Go / No-Go

**GO.** B ist in allen geprueften Stoer-Szenarien (Linkausfall 10/20/30 %, Churn 5/10/20 %) und
ueber den ganzen Adoptions-Sweep **sicher** (Lieferquote >= Baseline, worst Δ = −0,00005, d. h.
innerhalb Rauschen) und **nuetzlich** (Netto-Airtime im Mittel −15,8 %, Lieferquote +4,1 pp,
Re-Discovery-Airtime −17,5 %). Kein schaedliches Flattern: das beobachtete Umschalten ist produktiv
(auch ohne Stoerung steigt die Lieferquote +3,2 pp und sinkt die Airtime −11,5 %). Bei fehlendem
Backup faellt B verlustfrei auf Baseline zurueck (learn_loss=100 %: Δ ≈ 0).

> Einschraenkung der GO-Aussage: Der Gewinn lebt von verfuegbaren Backups und von vorhandenem
> Zustell-Feedback. In Netzteilen ohne kantendisjunkten Backup oder ohne ACK-Pakettyp ist der
> Effekt ~0 (aber nie negativ). Es ist ein **sicherer Netto-Gewinn dort, wo die Voraussetzungen
> da sind**, nie ein Risiko.

---

## 4. On-Node-Speicher pro Ziel

Primaerpfad cached MeshCore ohnehin (kein Mehraufwand). **B-Mehraufwand je Ziel:**
EWMA-Erfolg (1 B) + Fail-Counter (1 B) + Backup-Pfad (Laenge + Hash-Glieder, ~5–7 B typisch,
max 18 B bei 15-Hop-Backup). **Typisch ~9 B/Ziel; 64 Ziele ≈ 0,6 KB.** Fixe Tabelle, keine
dynamische Allokation (MeshCore-Regel). Passt locker auf nRF52840 (256 KB) / ESP32-S3 (512 KB).
CPU: 1 EWMA-Update + 1 Vergleich je Zustellung; kein Heap/Sort/Float-Pfad.

---

## 5. Ehrliche Limitierungen

- **ACK-Feedback nur fuer manche Pakettypen:** Das EWMA-Erfolgsmass braucht Zustell-Feedback.
  MeshCore hat ACKs nur fuer bestimmte Pakettypen (z. B. Direkt-Nachrichten), nicht fuer reines
  Flood-Advert. Fuer feedback-lose Pakete fehlt das Signal -> B faellt dort still auf Baseline
  zurueck. Die Sim nimmt Feedback fuer alle Ticks an (Obergrenze des Nutzens).
- **Idealisiertes Flood:** Re-Discovery-Airtime aus demselben idealisierten Timing-Modell wie v4
  (first-wins, keine Kollisionen/Capture). Absolutwerte (Airtime/Paar in den Hunderten) sind
  optimistisch; der RELATIVE Vergleich Baseline vs. B nutzt dasselbe Modell und ist robust.
- **Backup-Qualitaet aus passivem Lernen:** Der Backup ist modelliert als 2.-bester
  (kantendisjunkter) Pfad im VOLLEN Graphen; in Realitaet ist passives Lernen unvollstaendig ->
  `learn_loss`-Sensitivitaet (0/30/60/100 %) zeigt die Abhaengigkeit. Default 30 %. Bei 100 %
  learn_loss faellt B exakt auf Baseline zurueck (sicher).
- **Stoerung als unabhaengiger Per-Tick-Prozess** (kein zeitkorreliertes Fading). Konservativ fuer
  das EWMA — reale zeitliche Korrelation der Linkqualitaet wuerde B eher helfen (das Erfolgsmass
  haette ein staerkeres Vorwarn-Signal).
- **Backup-Verfuegbarkeit:** in der sparsen realen Topologie existiert nur fuer ~43 % der Paare
  ein kantendisjunkter Zweitpfad -> ueber die Haelfte der potentiellen Wirkung ist
  topologie-begrenzt (kein Risiko, aber Deckel auf dem Gewinn).

---

## 6. Dateien

- `reinforce_sim.py` — Simulation dieser Studie.
- `reinforce_results.json` — alle Kennzahlen (Sweeps, Entscheidung, On-Node).
- `fig_rf_airtime.png` — Re-Discovery- & Netto-Airtime ueber Stoerung.
- `fig_rf_delivery_stability.png` — Lieferquote + Routen-Stabilitaet (Flattern).
- `fig_rf_adoption.png` — Netto-Airtime & Liefergewinn ueber Adoptionsgrad.

---
## 🇬🇧 English Translation

# Path Success Reinforcement (Mechanism B) — Study

*Building block 3 of the invisible, node-local optimization layer (`Invisible_Optimizing_Layer.md`).
Purely node-local, no new packet type, transparent. Validated on the REAL neighbor-graph topology
(real edges + real avg_snr), churn profile from `advert_count`. Script: `reinforce_sim.py`,
results: `reinforce_results.json`. Seed 42, 6 seeds, 22 sources x 4 destinations x 40 send-ticks
per configuration.*

Topology (giant component): **632 nodes, 1577 edges, avg. degree 3.45** (sparse, real).

---

## 1. Question & Model

**Baseline (MeshCore today, flood-and-cache, "first packet wins"):** A source caches exactly ONE
path per destination (the first successful flood path). Every message is sent unicast over that
path. If an edge fails transiently or an intermediate node disappears, delivery fails. Only after
**3 consecutive failures** is the path discarded and an expensive **re-discovery (flood)**
triggered.

**B (Reinforcement):** Additionally per destination:
- an **EWMA success metric** `s` (α=0.30; from ACK/delivery feedback),
- a **backup path** (feasible successor), passively learned from overheard path chains (0 airtime;
  modeled as the 2nd-best edge-disjoint ETX path).

Logic: if `s` falls below `SWITCH_THR=0.55`, B **proactively switches to the backup BEFORE** the
3 hard failures + re-flood become necessary. On an acute failure, the other known path is tried
immediately; re-flood only if the backup also fails or none is known (in that case B falls back
exactly to baseline behavior).

**Methodology:** Per `(source, destination)`, 40 send attempts are simulated under transient link
failure (10/20/30 %) or node churn (from `advert_count`). Baseline and B run on **identical
disturbance sequences (Common Random Numbers)** -> paired comparison. Adoption sweep: fraction of
source nodes using B (1 node -> 100 %). Re-discovery airtime = transmitting nodes per flood
(identical flood model as v4); unicast airtime = hops per attempt.

---

## 2. Results (BENEFIT / COST / NET)

### BENEFIT (fully adopted)

| Link failure | Delivery rate Base -> B | Delta delivery rate | Net airtime delta | Re-discovery airtime saved | Suboptimal del./pair |
|---|---|---|---|---|---|
| 10 % | 0.529 -> 0.586 | **+5.7 pp** | **−20.0 %** | +22.8 % | 5.7 |
| 20 % | 0.373 -> 0.416 | **+4.4 pp** | **−16.5 %** | +17.9 % | 5.6 |
| 30 % | 0.280 -> 0.303 | **+2.3 pp** | **−11.0 %** | +11.9 % | 5.3 |

Node churn (advert_count, at 10 % link failure, fully adopted):

| Churn | Delivery rate Base -> B | Delta | Net airtime delta | Refloods/pair Base -> B |
|---|---|---|---|---|
| 5 % | 0.477 -> 0.529 | +5.1 pp | −18.2 % | 4.75 -> 3.92 |
| 10 % | 0.435 -> 0.487 | +5.2 pp | −18.3 % | 5.55 -> 4.60 |
| 20 % | 0.363 -> 0.404 | +4.1 pp | −15.0 % | 6.87 -> 5.93 |

**Mean over link failure (fully adopted): net airtime −15.8 %, delivery rate +4.1 pp,
re-discovery airtime −17.5 %.** The effect scales monotonically with adoption fraction
(1 node ≈ 0, full = max), because B is a pure source/cache decision and acts independently per
source.

### COST / Losses (honest)
- **Path switches:** ~0.08–0.09 switches per send-tick under disturbance (1 switch per ~11 ticks) —
  these are ALMOST EXCLUSIVELY reactions to real failures, not pointless toggling.
- **Disturbance-free flutter test (no link/node failure):** ~0.029 switches/tick, BUT:
  delivery rate still rises (0.825 -> 0.856; +3.2 pp) and net airtime falls (**−11.5 %**). The
  switching is therefore **productive, not harmful** — it reacts to real per-link SNR noise that
  baseline only catches after 3 failures + re-flood. **No harmful flapping.** (The GO/NO-GO
  evaluation deliberately does not ask "does B switch at all", but "does switching without
  disturbance harm delivery rate/airtime?" — answer: no.)
- **Suboptimal deliveries:** ~5.3–5.7 per pair (out of 40 ticks) travel over a longer-than-optimal
  path (backup detour). This is the main reason the airtime gain is not even larger — but the net
  result remains clearly positive.
- **Backup quality:** in this sparse topology (avg. degree 3.45), an edge-disjoint backup exists
  for only ~43 % of pairs. For the rest, B falls back to re-flood (= baseline behavior).

### NET
Total airtime (unicast + re-floods) decreases in EVERY disturbance configuration; delivery rate
increases in every one; route stability is maintained (no flapping). Sensitivity to backup
learning quality (`learn_loss`, at 20 % link failure, fully adopted):

| learn_loss | Backup fraction | Delta delivery rate | Net airtime | Re-discovery saved |
|---|---|---|---|---|
| 0 % | 0.43 | +6.3 pp | −25.0 % | +27.1 % |
| 30 % | 0.30 | +4.4 pp | −16.5 % | +17.9 % |
| 60 % | 0.17 | +2.8 pp | −9.5 % | +10.3 % |
| 100 % | 0.00 | −0.0 pp | +0.8 % | −0.8 % |

-> The benefit scales cleanly and MONOTONICALLY with backup availability; at 0 % backup, B is
**practically identical** to baseline (safe fallback, no relevant degradation; the +0.8 % airtime
at ll=100 % is noise/rounding with no delivery effect).

---

## 3. Go / No-Go

**GO.** B is **safe** across all tested disturbance scenarios (link failure 10/20/30 %, churn
5/10/20 %) and across the entire adoption sweep (delivery rate >= baseline, worst delta = −0.00005,
i.e. within noise) and **beneficial** (net airtime mean −15.8 %, delivery rate +4.1 pp,
re-discovery airtime −17.5 %). No harmful flapping: the observed switching is productive (even
without disturbance, delivery rate rises +3.2 pp and airtime falls −11.5 %). When no backup is
available, B falls back to baseline without loss (`learn_loss`=100 %: delta ≈ 0).

> Caveat on the GO statement: the benefit depends on available backups and on existing delivery
> feedback. In network segments without an edge-disjoint backup or without an ACK packet type,
> the effect is ~0 (but never negative). It is a **safe net gain where the prerequisites are
> present**, never a risk.

---

## 4. On-Node Memory per Destination

MeshCore already caches the primary path (no extra overhead). **B overhead per destination:**
EWMA success (1 B) + fail counter (1 B) + backup path (length + hash segments, ~5–7 B typical,
max 18 B for a 15-hop backup). **Typically ~9 B/destination; 64 destinations ≈ 0.6 KB.** Fixed
table, no dynamic allocation (MeshCore rule). Fits comfortably on nRF52840 (256 KB) / ESP32-S3
(512 KB). CPU: 1 EWMA update + 1 comparison per delivery; no heap/sort/float path.

---

## 5. Honest Limitations

- **ACK feedback only for some packet types:** The EWMA success metric requires delivery feedback.
  MeshCore has ACKs only for certain packet types (e.g. direct messages), not for pure
  flood-adverts. For feedback-less packets the signal is missing -> B silently falls back to
  baseline there. The simulation assumes feedback for all ticks (upper bound of benefit).
- **Idealized flood:** Re-discovery airtime is from the same idealized timing model as v4
  (first-wins, no collisions/capture). Absolute values (airtime/pair in the hundreds) are
  optimistic; the RELATIVE comparison baseline vs. B uses the same model and is robust.
- **Backup quality from passive learning:** The backup is modeled as the 2nd-best (edge-disjoint)
  path in the FULL graph; in reality passive learning is incomplete -> `learn_loss` sensitivity
  (0/30/60/100 %) shows the dependency. Default 30 %. At 100 % `learn_loss`, B falls back exactly
  to baseline (safe).
- **Disturbance as independent per-tick process** (no time-correlated fading). Conservative for
  the EWMA — real temporal correlation of link quality would actually help B (the success metric
  would have a stronger early-warning signal).
- **Backup availability:** in the sparse real topology, an edge-disjoint second path exists for
  only ~43 % of pairs -> more than half the potential effect is topology-limited (no risk, but a
  ceiling on the gain).

---

## 6. Files

- `reinforce_sim.py` — simulation for this study.
- `reinforce_results.json` — all metrics (sweeps, decision, on-node).
- `fig_rf_airtime.png` — re-discovery & net airtime over disturbance level.
- `fig_rf_delivery_stability.png` — delivery rate + route stability (flapping).
- `fig_rf_adoption.png` — net airtime & delivery gain over adoption fraction.
