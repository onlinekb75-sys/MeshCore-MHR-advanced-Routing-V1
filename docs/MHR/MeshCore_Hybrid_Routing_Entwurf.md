# MeshCore Hybrid Routing (MHR) – ein fusionierter Routing-Entwurf für 10 % Duty-Cycle

Entwurf für einen MeshCore-Fork. Baut auf der Code-Analyse von `meshcore-dev/MeshCore` (`main`) auf und fusioniert mehrere Routing-Theorien zu einem Verfahren, das gezielt das 10 %-Duty-Cycle-Budget (Sub-Band 869.4–869.65 MHz) ausnutzt und die heutigen Umwege beseitigt.

---

## 1. Leitidee

Der entscheidende Hebel ist eine Eigenschaft, die MeshCore bisher **nicht** ausnutzt: das Netz besteht aus **zwei sehr unterschiedlichen Knotenklassen**.

- **Repeater** – meist netzgebunden, ortsfest, dauerstrombetrieben, Topologie ändert sich selten.
- **Clients/Companions** – mobil, batteriebetrieben, schlafend, kommen und gehen.

Heute behandelt MeshCore beide gleich: jede Erstverbindung wird **netzweit geflutet**, und der zuerst eintreffende (zufällige) Pfad wird eingefroren. Genau das erzeugt Umwege *und* den größten Airtime-Verbrauch.

**MHR-Kernidee:** Die 10 %-Headroom wird *bewusst investiert*, um auf dem **stabilen Repeater-Backbone** ein sparsames, **proaktives, metrik-basiertes** Routing laufen zu lassen. Sobald der Backbone seine eigene Topologie kennt, kollabiert die teure netzweite Flutung zu „flute nur bis zum nächsten Repeater – ab da routet der Backbone gezielt". Clients bleiben **reaktiv** und damit schlaf-/mobilfreundlich.

Das ist – theoretisch sauber eingeordnet – ein **Zone Routing Protocol (ZRP)**, aber statt nach Radius wird nach **Knotenklasse** getrennt: proaktiv im Backbone, reaktiv an der Kante.

Wichtig: Der zusätzliche Kontroll-Traffic des Backbones ist **kleiner** als die netzweiten Discovery-Floods, die dadurch wegfallen. Netto soll die Airtime **sinken**, nicht steigen. Die 10 % sind das Polster, das diese Investition risikolos macht.

---

## 2. Welche Ansätze fusioniert werden

| Theorie / Verfahren | Was übernommen wird | Wohin in MHR |
|---|---|---|
| **ZRP** (Zone Routing) | Hybrid proaktiv-innen / reaktiv-außen | Trennung Backbone ↔ Client |
| **BATMAN / DSDV** (Distance-Vector) | Hop-für-Hop Vektoraustausch, kein Voll-Topologie-Bild | Backbone-Routing (L1) |
| **ETX / ETT** | Link-Metrik aus Lieferrate × Funkqualität | Kostenfunktion |
| **AODV** | Best-Path-Auswahl + aktive Route-Maintenance | Reaktive Kante + Upgrade |
| **Selective/Opportunistic Flooding** | SNR-gewichtete Rebroadcast-Verzögerung | Flood-Pruning (`rx_delay_base`) |
| **Connected Dominating Set / Backbone** | Repeater als tragendes Gerüst | Klassendefinition |
| **Geo-Routing (GPSR)** | Richtungs-Bias der Discovery | optionale Scoped-Discovery |

MHR erfindet nichts Exotisches – es kombiniert bewährte Bausteine entlang der MeshCore-Realität.

---

## 3. Architektur in drei Ebenen

### L0 – Link Sensing (alle Knoten)
Jeder Knoten pflegt pro direkt gehörtem Nachbarn:
- **EWMA-SNR** (gleitend geglättet, Marge über Noise-Floor),
- **Advert-Empfangsrate** über ein Zeitfenster (gehörte / erwartete Zero-Hop-Adverts) → Schätzer für Lieferwahrscheinlichkeit in *einer* Richtung.

MeshCore liefert die Bausteine bereits: `putNeighbour(id, timestamp, snr)` (`simple_repeater/MyMesh.cpp:641,824`) und die periodischen Zero-Hop-Adverts (`advert.interval`, Default 2 min). L0 erweitert nur die Neighbour-Tabelle um die beiden Messgrößen.

### L1 – Repeater-Backbone (proaktiv, Distance-Vector)
Nur Repeater nehmen teil. Jeder Repeater sendet **zyklisch per Zero-Hop-Broadcast** (nur direkte Nachbarn hören es!) einen kompakten **Distanzvektor**:

```
{ seqno, [ (dest_repeater_hash, path_cost), ... ] }
```

Nachbarn integrieren per **Bellman-Ford** (`neue Kosten = Linkkosten + announced cost`), behalten je Ziel den günstigsten Next-Hop und re-annoncieren beim nächsten Zyklus. Die Information wandert so **Hop für Hop ausschließlich über Zero-Hop-Broadcasts** durchs Backbone – **niemals netzweiter Flood**. Airtime-Aufwand pro Repeater = ein kleiner Broadcast je Zyklus, gehört nur lokal ⇒ O(Nachbarn), nicht O(Netz).

Schleifenschutz: **Sequenznummern** (DSDV) + **Split-Horizon/Poisoned-Reverse**. Selbstheilung erfolgt rein über den Zyklus – **kein** Re-Flood bei Ausfall nötig.

*Die 10 % erlauben hier eine zügige Update-Kadenz (z. B. alle 1–3 min, dichte-adaptiv – vgl. MeshCore-Discussion #2053), und damit schnelle Konvergenz und Heilung.*

### L2 – Client-Kante (reaktiv, AODV-artig)
Clients bauen **keine** Tabellen. Ein Client „registriert" sich implizit beim nächstgelegenen Repeater über seine Zero-Hop-Adverts; dieser **Home-Repeater** nimmt den Client als Host-Route (`client_hash → self`) in seinen Distanzvektor auf. Will Client A Client B erreichen:

1. A flutet eine Discovery – **aber nur so weit, bis ein Backbone-Repeater sie hört** (`flood.max` klein, SNR-Backoff aktiv).
2. Der erste Repeater mit bekannter Backbone-Route zu B's Home-Repeater **stoppt die Flutung** und leitet die Discovery als **gerichteten Unicast über den Backbone** weiter.
3. B's Home-Repeater stellt zu; der Rückpfad wird wie gewohnt als Source-Route zurückgegeben.

Damit wird aus einem O(Netz)-Flood ein **O(lokal)-Flood + Backbone-Unicast**. Das ist der zentrale Gewinn: weniger Airtime *und* kein zufälliger Umweg, weil der Backbone metrisch den günstigsten Weg wählt.

---

## 4. Die fusionierte Metrik (ETX + SNR)

Pro Link:

```
deliv = empfangsrate_hin × empfangsrate_zurück      // ETX-Idee, beidseitig (Asymmetrie!)
etx   = 1 / max(deliv, ε)
snr_pen = f(SNR-Marge)                               // kleiner Zuschlag für knappe Links
link_cost = etx + α · snr_pen
```

Pfadkosten = **Summe** der Linkkosten (additiv ⇒ Bellman-Ford/Dijkstra-tauglich). ETX sorgt für **Zuverlässigkeit** (vermeidet flapping/lossy Hops), die SNR-Komponente bricht Gleichstände zugunsten **robuster Marge**. Beide Größen liefert L0 ohne Zusatz-Traffic, weil sie aus den ohnehin laufenden Adverts gewonnen werden.

Das ersetzt das heutige „first packet wins" (`Mesh.cpp:138`) und „Pfad ungeprüft überschreiben" (`BaseChatMesh.cpp:305`) durch eine echte Kostenentscheidung.

---

## 5. Ablauf einer Nachricht – heute vs. MHR

**Heute:** A flutet B netzweit (bis zu 64 Hops). Dutzende Repeater rebroadcasten zufällig verzögert. B nimmt die *erste* Kopie → evtl. 4-Hop-Umweg statt 2-Hop-Direktweg. Dieser Umweg wird eingefroren, bis er 3× ausfällt.

**Mit MHR:**
1. A flutet lokal (klein, SNR-gepruned) → erreicht Repeater R1.
2. R1 kennt aus dem Backbone-DV den günstigsten Weg zu B's Home-Repeater R4: `R1→R2→R4` (Kostenminimum). Flutung endet bei R1.
3. Discovery läuft als Backbone-Unicast `R1→R2→R4→B`.
4. Rückpfad = Source-Route, von B's Seite metrik-bestätigt.

Ergebnis: deterministisch kürzester/zuverlässigster Pfad statt Zufall; ein Bruchteil der Sendeereignisse.

---

## 6. Einsatz des 10 %-Budgets (Airtime-Bilanz)

Das Budget wird an **genau zwei** Stellen investiert:

- **Backbone-DV-Zyklus:** ein kleiner Zero-Hop-Broadcast je Repeater alle 1–3 min. Last ist **lokal** und **konstant**, skaliert nicht mit Netzgröße, nur mit Nachbardichte.
- **Best-of-N-Sammelfenster** am Ziel (ein paar Airtimes Wartezeit, einmalig je Pfadaufbau).

Gespart wird der teuerste Posten von heute: **netzweite Discovery-Floods** (jeder Erstkontakt, jeder Pfad-Reset, jede Heilung). Bei einem Netz mit vielen Erstkontakten/Resets dominiert dieser Posten – sein Wegfall überkompensiert den DV-Overhead deutlich. Die 10 % sind dabei **Sicherheitsmarge**, nicht Dauerlast: MHR zielt darauf, die *durchschnittliche* Kanalauslastung zu senken und Lastspitzen (Flood-Stürme) zu glätten.

Hinweis Aggregat-Kanal: Da L1 nur Zero-Hop sendet, belastet es **nicht** das gesamte geteilte Medium netzweit, sondern nur die jeweilige Funkzelle – das ist der Grund, warum dieser proaktive Anteil trotz geteiltem Halbduplex-Kanal skaliert.

---

## 7. Konkrete Eingriffe im MeshCore-Code

| MHR-Element | Code-Ankerpunkt | Änderung |
|---|---|---|
| Link-Metrik (L0) | `putNeighbour()`, Neighbour-Struct | SNR-EWMA + Advert-Empfangsrate ergänzen |
| Backbone-DV (L1) | neuer `PAYLOAD_TYPE_*` (Repeater-only) oder erweitertes Advert; neue Routing-Tabelle | Zero-Hop-Vektor senden/integrieren (Bellman-Ford, seqno) |
| Flood-Suppression | `routeRecvPacket()` `Mesh.cpp:330`; `allowPacketForward()` `MyMesh.cpp:429` | Wenn Backbone-Route bekannt: nicht weiterfluten, sondern Backbone-Unicast |
| Best-of-N + Metrik | `onPeerPathRecv()` / `Mesh.cpp:138` | Sammelfenster, günstigste Kopie wählen statt erster |
| Pfad-Upgrade | `onContactPathRecv()` `BaseChatMesh.cpp:304` | nur bei niedrigeren Kosten überschreiben + periodischer Refresh |
| Opportunistic Flood | `calcRxDelay()` / `rx_delay_base` `MyMesh.cpp:534,874` | aktivieren (Default >0) |
| Scope/Geo-Bias | `flood.max` `CommonCLI.cpp:599`; scoped adverts | kleines Hop-Limit + optional Richtungsfilter |
| Tuning-Knöpfe | `CommonCLI.cpp` | neue `set`-Kommandos: `bb.interval`, `metric.mode`, `pathwin` |

---

## 8. Schrittweiser Rollout (kompatibilitätsschonend)

- **Phase 0 – reine Konfig (sofort, rückwärtskompatibel):** `rxdelay` aktivieren, `flood.max` senken, Loop-Detect an. Bringt bereits messbar weniger Umwege.
- **Phase 1 – Metrik + Best-of-N:** Pfadauswahl am Ziel + Upgrade beim Sender. Lokale Änderung, interoperiert mit Stock-Firmware (schlechtere Knoten verhalten sich nur wie bisher).
- **Phase 2 – Backbone-DV:** proaktives L1 zwischen MHR-Repeatern. Stock-Repeater nehmen nicht teil, brechen aber nichts (DV-Pakete sind für sie unbekannter Typ → ignoriert).
- **Phase 3 – Discovery-Short-Circuit + Geo-Bias:** der eigentliche Airtime-Sprung; setzt kritische Masse an MHR-Repeatern voraus.

Interoperabilität ist Leitplanke: In einem gemischten Netz muss MHR **graceful degradieren** auf das heutige Flood-and-cache, sobald keine Backbone-Route verfügbar ist.

---

## 9. Fehlermodi & Risiken (ehrlich)

- **DV-Schleifen / Count-to-Infinity:** durch Sequenznummern + Split-Horizon/Poisoned-Reverse beherrschbar, aber sorgfältig zu implementieren.
- **Konvergenzzeit:** nach Topologieänderung braucht der Backbone einige Zyklen; in der Lücke fällt MHR auf Flood zurück.
- **Asymmetrische Links:** beidseitige ETX-Messung nötig; reine SNR-Metrik wäre fehleranfällig.
- **Client-State-Explosion:** Host-Routes für sehr viele Clients können Backbone-Tabellen sprengen → nur aktive/kürzlich gehörte Clients announcen, Rest über (billigen) Repeater-only-Flood.
- **Backbone-Partition:** getrennte Repeater-Inseln müssen sauber auf reaktiv zurückfallen.
- **Mixed-Firmware-Netz:** ohne kritische MHR-Masse bleibt der Gewinn klein.
- **Fehl-getunte Metrik:** ETX/SNR-Gewichte sind netzabhängig und müssen empirisch kalibriert werden.

---

## 10. Validierung

- **Vorher/Nachher** auf Referenzstrecken: `trace`-Kommando (reale Hop-Folge), Pfadlängen-Verteilung, Airtime-Statistik (`Radio Stats`, `docs/cli_commands.md:133`), Flood-Dups (`getNumFloodDups()`).
- **Metriken:** mittlere Hop-Zahl pro Zustellung, Anteil „kürzester Pfad gewählt", aggregierte Kanalauslastung, Zustellrate bei Pfadlänge ≥ 2 (heute lt. Community ~45 % unzuverlässig).
- **Simulation vor Feldtest:** Discrete-Event-Sim (z. B. ns-3/LoRaSim-artig) mit realer Topologie, um DV-Kadenz und Metrik-Gewichte zu kalibrieren, bevor Firmware aufs reale Netz geht.

---

## 11. Fazit

MHR ist kein Bruch mit MeshCore, sondern dessen konsequente Weiterentwicklung: das bestehende reaktive DSR bleibt das Fundament, bekommt aber eine **echte Metrik**, eine **Best-Path-Auswahl** und – ermöglicht durch die 10 %-Marge – einen **sparsamen proaktiven Backbone**, der die teuren netzweiten Floods überflüssig macht. Die Knotenklassen-Trennung (ZRP-Prinzip) ist der Trick, der proaktiv und reaktiv jeweils dort einsetzt, wo sie billig sind. Erwartetes Ergebnis: kürzere, deterministische Pfade und **trotz** zusätzlichem Kontroll-Traffic eine **niedrigere** Gesamt-Airtime.

*Verwandtes Dokument: `MeshCore_Routing_Analyse_und_Optimierung.md` (Ursachenanalyse + Stufen A–D).*

---
## 🇬🇧 English Translation

# MeshCore Hybrid Routing (MHR) – A Fused Routing Design for 10 % Duty-Cycle

Design for a MeshCore fork. Built on the code analysis of `meshcore-dev/MeshCore` (`main`), fusing several routing theories into a single scheme that deliberately exploits the 10 % duty-cycle budget (sub-band 869.4–869.65 MHz) and eliminates today's detours.

---

## 1. Core Idea

The decisive lever is a property that MeshCore currently does **not** exploit: the network consists of **two very different node classes**.

- **Repeaters** – mostly mains-powered, fixed in place, always on; topology changes rarely.
- **Clients/Companions** – mobile, battery-powered, sleeping, come and go.

Today MeshCore treats both identically: every first contact is **flooded network-wide**, and the first-arriving (random) path is frozen. That is exactly what produces detours *and* the largest share of airtime consumption.

**MHR core idea:** The 10 % headroom is *deliberately invested* to run a frugal, **proactive, metric-based** routing scheme on the **stable repeater backbone**. Once the backbone knows its own topology, the expensive network-wide flood collapses to "flood only until the nearest repeater — from there the backbone routes precisely". Clients stay **reactive** and thus sleep/mobile-friendly.

Properly classified in theory, this is a **Zone Routing Protocol (ZRP)**, but instead of splitting by radius the split is by **node class**: proactive inside the backbone, reactive at the edge.

Important: the additional control traffic of the backbone is **smaller** than the network-wide discovery floods it eliminates. Net airtime should **decrease**, not increase. The 10 % is the cushion that makes this investment risk-free.

---

## 2. Which Approaches Are Fused

| Theory / Scheme | What is adopted | Where in MHR |
|---|---|---|
| **ZRP** (Zone Routing) | Hybrid proactive-inner / reactive-outer | Backbone ↔ Client separation |
| **BATMAN / DSDV** (Distance-Vector) | Hop-by-hop vector exchange, no full topology image | Backbone routing (L1) |
| **ETX / ETT** | Link metric from delivery rate × radio quality | Cost function |
| **AODV** | Best-path selection + active route maintenance | Reactive edge + upgrade |
| **Selective/Opportunistic Flooding** | SNR-weighted rebroadcast delay | Flood pruning (`rx_delay_base`) |
| **Connected Dominating Set / Backbone** | Repeaters as load-bearing framework | Class definition |
| **Geo-Routing (GPSR)** | Directional bias of discovery | optional scoped discovery |

MHR invents nothing exotic — it combines proven building blocks along the lines of MeshCore's reality.

---

## 3. Architecture in Three Layers

### L0 – Link Sensing (all nodes)
Every node maintains, per directly heard neighbour:
- **EWMA-SNR** (exponentially smoothed, margin above noise floor),
- **Advert reception rate** over a time window (heard / expected zero-hop adverts) → estimator of delivery probability in *one* direction.

MeshCore already provides the building blocks: `putNeighbour(id, timestamp, snr)` (`simple_repeater/MyMesh.cpp:641,824`) and the periodic zero-hop adverts (`advert.interval`, default 2 min). L0 only extends the neighbour table with those two measurements.

### L1 – Repeater Backbone (proactive, Distance-Vector)
Only repeaters participate. Each repeater **periodically broadcasts via zero-hop** (only direct neighbours hear it!) a compact **distance vector**:

```
{ seqno, [ (dest_repeater_hash, path_cost), ... ] }
```

Neighbours integrate via **Bellman-Ford** (`new cost = link cost + announced cost`), keep the cheapest next-hop per destination, and re-announce on the next cycle. The information travels **hop by hop exclusively via zero-hop broadcasts** through the backbone — **never a network-wide flood**. Airtime cost per repeater = one small broadcast per cycle, heard only locally ⇒ O(neighbours), not O(network).

Loop protection: **sequence numbers** (DSDV) + **split-horizon/poisoned-reverse**. Self-healing occurs purely through the cycle — **no** re-flood on failure needed.

*The 10 % permit a brisk update cadence here (e.g. every 1–3 min, density-adaptive — cf. MeshCore-Discussion #2053), and thus fast convergence and healing.*

### L2 – Client Edge (reactive, AODV-like)
Clients build **no** tables. A client "registers" implicitly with the nearest repeater via its zero-hop adverts; that **home repeater** inserts the client as a host route (`client_hash → self`) into its distance vector. When client A wants to reach client B:

1. A floods a discovery — **but only until a backbone repeater hears it** (`flood.max` small, SNR backoff active).
2. The first repeater with a known backbone route to B's home repeater **stops the flood** and forwards the discovery as a **directed unicast over the backbone**.
3. B's home repeater delivers; the return path is handed back as a source route as usual.

This turns an O(network)-flood into an **O(local)-flood + backbone unicast**. That is the central gain: less airtime *and* no random detour, because the backbone selects the cheapest path metrically.

---

## 4. The Fused Metric (ETX + SNR)

Per link:

```
deliv = delivery_rate_forward × delivery_rate_reverse   // ETX idea, bidirectional (asymmetry!)
etx   = 1 / max(deliv, ε)
snr_pen = f(SNR margin)                                  // small penalty for marginal links
link_cost = etx + α · snr_pen
```

Path cost = **sum** of link costs (additive ⇒ suitable for Bellman-Ford/Dijkstra). ETX provides **reliability** (avoids flapping/lossy hops); the SNR component breaks ties in favour of **robust margin**. Both quantities are delivered by L0 without extra traffic, since they are derived from adverts that are already running.

This replaces today's "first packet wins" (`Mesh.cpp:138`) and "overwrite path unconditionally" (`BaseChatMesh.cpp:305`) with a genuine cost-based decision.

---

## 5. Message Flow – Today vs. MHR

**Today:** A floods B network-wide (up to 64 hops). Dozens of repeaters rebroadcast with random delays. B takes the *first* copy → possibly a 4-hop detour instead of a 2-hop direct path. This detour is frozen until it fails 3 times.

**With MHR:**
1. A floods locally (small, SNR-pruned) → reaches repeater R1.
2. R1 knows from the backbone DV the cheapest path to B's home repeater R4: `R1→R2→R4` (cost minimum). Flooding ends at R1.
3. Discovery runs as backbone unicast `R1→R2→R4→B`.
4. Return path = source route, metric-confirmed from B's side.

Result: deterministically shortest/most-reliable path instead of chance; a fraction of the transmission events.

---

## 6. Use of the 10 % Budget (Airtime Balance)

The budget is invested at **exactly two** points:

- **Backbone DV cycle:** one small zero-hop broadcast per repeater every 1–3 min. Load is **local** and **constant**, scales not with network size but only with neighbour density.
- **Best-of-N collection window** at the destination (a few airtimes of wait, once per path setup).

Saved is the most expensive item today: **network-wide discovery floods** (every first contact, every path reset, every healing). In a network with many first contacts/resets this item dominates — its elimination more than compensates the DV overhead. The 10 % is a **safety margin**, not a sustained load: MHR aims to reduce *average* channel utilisation and smooth out load spikes (flood storms).

Note on shared channel: since L1 only sends zero-hop, it does **not** load the entire shared medium network-wide, only the respective radio cell — that is why this proactive share scales despite the shared half-duplex channel.

---

## 7. Concrete Changes in the MeshCore Code

| MHR element | Code anchor | Change |
|---|---|---|
| Link metric (L0) | `putNeighbour()`, Neighbour struct | Add SNR-EWMA + advert reception rate |
| Backbone DV (L1) | new `PAYLOAD_TYPE_*` (repeater-only) or extended advert; new routing table | Send/integrate zero-hop vector (Bellman-Ford, seqno) |
| Flood suppression | `routeRecvPacket()` `Mesh.cpp:330`; `allowPacketForward()` `MyMesh.cpp:429` | If backbone route known: do not forward-flood, instead backbone unicast |
| Best-of-N + metric | `onPeerPathRecv()` / `Mesh.cpp:138` | Collection window, choose cheapest copy instead of first |
| Path upgrade | `onContactPathRecv()` `BaseChatMesh.cpp:304` | overwrite only at lower cost + periodic refresh |
| Opportunistic flood | `calcRxDelay()` / `rx_delay_base` `MyMesh.cpp:534,874` | enable (default >0) |
| Scope/geo bias | `flood.max` `CommonCLI.cpp:599`; scoped adverts | small hop limit + optional direction filter |
| Tuning knobs | `CommonCLI.cpp` | new `set` commands: `bb.interval`, `metric.mode`, `pathwin` |

---

## 8. Incremental Rollout (Compatibility-Preserving)

- **Phase 0 – config only (immediate, backwards-compatible):** enable `rxdelay`, lower `flood.max`, enable loop detection. Already yields measurably fewer detours.
- **Phase 1 – metric + best-of-N:** path selection at destination + upgrade at sender. Local change, interoperates with stock firmware (weaker nodes simply behave as before).
- **Phase 2 – backbone DV:** proactive L1 between MHR repeaters. Stock repeaters do not participate but break nothing (DV packets are an unknown type for them → ignored).
- **Phase 3 – discovery short-circuit + geo bias:** the actual airtime leap; requires a critical mass of MHR repeaters.

Interoperability is the guiding constraint: in a mixed network MHR must **gracefully degrade** to today's flood-and-cache as soon as no backbone route is available.

---

## 9. Failure Modes & Risks (Honestly)

- **DV loops / count-to-infinity:** manageable via sequence numbers + split-horizon/poisoned-reverse, but must be implemented carefully.
- **Convergence time:** after a topology change the backbone needs several cycles; during the gap MHR falls back to flood.
- **Asymmetric links:** bidirectional ETX measurement required; a pure SNR metric would be error-prone.
- **Client state explosion:** host routes for very many clients can overflow backbone tables → announce only active/recently-heard clients; handle the rest via cheap repeater-only flood.
- **Backbone partition:** separated repeater islands must fall back cleanly to reactive mode.
- **Mixed-firmware network:** without a critical MHR mass the gain remains small.
- **Mis-tuned metric:** ETX/SNR weights are network-dependent and must be calibrated empirically.

---

## 10. Validation

- **Before/after** on reference links: `trace` command (real hop sequence), path-length distribution, airtime statistics (`Radio Stats`, `docs/cli_commands.md:133`), flood duplicates (`getNumFloodDups()`).
- **Metrics:** mean hop count per delivery, share of "shortest path chosen", aggregate channel utilisation, delivery rate at path length ≥ 2 (today per community ~45 % unreliable).
- **Simulation before field test:** discrete-event sim (e.g. ns-3/LoRaSim-style) with real topology to calibrate DV cadence and metric weights before firmware goes onto the live network.

---

## 11. Conclusion

MHR is not a break with MeshCore but its consistent evolution: the existing reactive DSR remains the foundation, but gains a **real metric**, a **best-path selection**, and — enabled by the 10 % margin — a **frugal proactive backbone** that makes the expensive network-wide floods unnecessary. The node-class separation (ZRP principle) is the trick that deploys proactive and reactive routing exactly where each is cheap. Expected outcome: shorter, deterministic paths and — **despite** additional control traffic — **lower** total airtime.

*Related document: `MeshCore_Routing_Analyse_und_Optimierung.md` (root-cause analysis + stages A–D).*
