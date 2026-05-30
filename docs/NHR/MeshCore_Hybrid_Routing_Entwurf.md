# MeshCore Hybrid Routing (NHR) – ein fusionierter Routing-Entwurf für 10 % Duty-Cycle

Entwurf für einen MeshCore-Fork. Baut auf der Code-Analyse von `meshcore-dev/MeshCore` (`main`) auf und fusioniert mehrere Routing-Theorien zu einem Verfahren, das gezielt das 10 %-Duty-Cycle-Budget (Sub-Band 869.4–869.65 MHz) ausnutzt und die heutigen Umwege beseitigt.

---

## 1. Leitidee

Der entscheidende Hebel ist eine Eigenschaft, die MeshCore bisher **nicht** ausnutzt: das Netz besteht aus **zwei sehr unterschiedlichen Knotenklassen**.

- **Repeater** – meist netzgebunden, ortsfest, dauerstrombetrieben, Topologie ändert sich selten.
- **Clients/Companions** – mobil, batteriebetrieben, schlafend, kommen und gehen.

Heute behandelt MeshCore beide gleich: jede Erstverbindung wird **netzweit geflutet**, und der zuerst eintreffende (zufällige) Pfad wird eingefroren. Genau das erzeugt Umwege *und* den größten Airtime-Verbrauch.

**NHR-Kernidee:** Die 10 %-Headroom wird *bewusst investiert*, um auf dem **stabilen Repeater-Backbone** ein sparsames, **proaktives, metrik-basiertes** Routing laufen zu lassen. Sobald der Backbone seine eigene Topologie kennt, kollabiert die teure netzweite Flutung zu „flute nur bis zum nächsten Repeater – ab da routet der Backbone gezielt". Clients bleiben **reaktiv** und damit schlaf-/mobilfreundlich.

Das ist – theoretisch sauber eingeordnet – ein **Zone Routing Protocol (ZRP)**, aber statt nach Radius wird nach **Knotenklasse** getrennt: proaktiv im Backbone, reaktiv an der Kante.

Wichtig: Der zusätzliche Kontroll-Traffic des Backbones ist **kleiner** als die netzweiten Discovery-Floods, die dadurch wegfallen. Netto soll die Airtime **sinken**, nicht steigen. Die 10 % sind das Polster, das diese Investition risikolos macht.

---

## 2. Welche Ansätze fusioniert werden

| Theorie / Verfahren | Was übernommen wird | Wohin in NHR |
|---|---|---|
| **ZRP** (Zone Routing) | Hybrid proaktiv-innen / reaktiv-außen | Trennung Backbone ↔ Client |
| **BATMAN / DSDV** (Distance-Vector) | Hop-für-Hop Vektoraustausch, kein Voll-Topologie-Bild | Backbone-Routing (L1) |
| **ETX / ETT** | Link-Metrik aus Lieferrate × Funkqualität | Kostenfunktion |
| **AODV** | Best-Path-Auswahl + aktive Route-Maintenance | Reaktive Kante + Upgrade |
| **Selective/Opportunistic Flooding** | SNR-gewichtete Rebroadcast-Verzögerung | Flood-Pruning (`rx_delay_base`) |
| **Connected Dominating Set / Backbone** | Repeater als tragendes Gerüst | Klassendefinition |
| **Geo-Routing (GPSR)** | Richtungs-Bias der Discovery | optionale Scoped-Discovery |

NHR erfindet nichts Exotisches – es kombiniert bewährte Bausteine entlang der MeshCore-Realität.

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

## 5. Ablauf einer Nachricht – heute vs. NHR

**Heute:** A flutet B netzweit (bis zu 64 Hops). Dutzende Repeater rebroadcasten zufällig verzögert. B nimmt die *erste* Kopie → evtl. 4-Hop-Umweg statt 2-Hop-Direktweg. Dieser Umweg wird eingefroren, bis er 3× ausfällt.

**Mit NHR:**
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

Gespart wird der teuerste Posten von heute: **netzweite Discovery-Floods** (jeder Erstkontakt, jeder Pfad-Reset, jede Heilung). Bei einem Netz mit vielen Erstkontakten/Resets dominiert dieser Posten – sein Wegfall überkompensiert den DV-Overhead deutlich. Die 10 % sind dabei **Sicherheitsmarge**, nicht Dauerlast: NHR zielt darauf, die *durchschnittliche* Kanalauslastung zu senken und Lastspitzen (Flood-Stürme) zu glätten.

Hinweis Aggregat-Kanal: Da L1 nur Zero-Hop sendet, belastet es **nicht** das gesamte geteilte Medium netzweit, sondern nur die jeweilige Funkzelle – das ist der Grund, warum dieser proaktive Anteil trotz geteiltem Halbduplex-Kanal skaliert.

---

## 7. Konkrete Eingriffe im MeshCore-Code

| NHR-Element | Code-Ankerpunkt | Änderung |
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
- **Phase 2 – Backbone-DV:** proaktives L1 zwischen NHR-Repeatern. Stock-Repeater nehmen nicht teil, brechen aber nichts (DV-Pakete sind für sie unbekannter Typ → ignoriert).
- **Phase 3 – Discovery-Short-Circuit + Geo-Bias:** der eigentliche Airtime-Sprung; setzt kritische Masse an NHR-Repeatern voraus.

Interoperabilität ist Leitplanke: In einem gemischten Netz muss NHR **graceful degradieren** auf das heutige Flood-and-cache, sobald keine Backbone-Route verfügbar ist.

---

## 9. Fehlermodi & Risiken (ehrlich)

- **DV-Schleifen / Count-to-Infinity:** durch Sequenznummern + Split-Horizon/Poisoned-Reverse beherrschbar, aber sorgfältig zu implementieren.
- **Konvergenzzeit:** nach Topologieänderung braucht der Backbone einige Zyklen; in der Lücke fällt NHR auf Flood zurück.
- **Asymmetrische Links:** beidseitige ETX-Messung nötig; reine SNR-Metrik wäre fehleranfällig.
- **Client-State-Explosion:** Host-Routes für sehr viele Clients können Backbone-Tabellen sprengen → nur aktive/kürzlich gehörte Clients announcen, Rest über (billigen) Repeater-only-Flood.
- **Backbone-Partition:** getrennte Repeater-Inseln müssen sauber auf reaktiv zurückfallen.
- **Mixed-Firmware-Netz:** ohne kritische NHR-Masse bleibt der Gewinn klein.
- **Fehl-getunte Metrik:** ETX/SNR-Gewichte sind netzabhängig und müssen empirisch kalibriert werden.

---

## 10. Validierung

- **Vorher/Nachher** auf Referenzstrecken: `trace`-Kommando (reale Hop-Folge), Pfadlängen-Verteilung, Airtime-Statistik (`Radio Stats`, `docs/cli_commands.md:133`), Flood-Dups (`getNumFloodDups()`).
- **Metriken:** mittlere Hop-Zahl pro Zustellung, Anteil „kürzester Pfad gewählt", aggregierte Kanalauslastung, Zustellrate bei Pfadlänge ≥ 2 (heute lt. Community ~45 % unzuverlässig).
- **Simulation vor Feldtest:** Discrete-Event-Sim (z. B. ns-3/LoRaSim-artig) mit realer Topologie, um DV-Kadenz und Metrik-Gewichte zu kalibrieren, bevor Firmware aufs reale Netz geht.

---

## 11. Fazit

NHR ist kein Bruch mit MeshCore, sondern dessen konsequente Weiterentwicklung: das bestehende reaktive DSR bleibt das Fundament, bekommt aber eine **echte Metrik**, eine **Best-Path-Auswahl** und – ermöglicht durch die 10 %-Marge – einen **sparsamen proaktiven Backbone**, der die teuren netzweiten Floods überflüssig macht. Die Knotenklassen-Trennung (ZRP-Prinzip) ist der Trick, der proaktiv und reaktiv jeweils dort einsetzt, wo sie billig sind. Erwartetes Ergebnis: kürzere, deterministische Pfade und **trotz** zusätzlichem Kontroll-Traffic eine **niedrigere** Gesamt-Airtime.

*Verwandtes Dokument: `MeshCore_Routing_Analyse_und_Optimierung.md` (Ursachenanalyse + Stufen A–D).*
