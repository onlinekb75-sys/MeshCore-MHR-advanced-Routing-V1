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
