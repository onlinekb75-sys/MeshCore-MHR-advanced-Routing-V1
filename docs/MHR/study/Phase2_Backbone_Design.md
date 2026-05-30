# Phase 2 — Proaktiver Regions-Backbone: Implementierungs-Design + Validierungsplan

*Design-first. Phase 2 ist der größte Eingriff (proaktiver Control-Plane). Die Backbone-Simulation
(`Backbone_Phase2_Study.md`) zeigte: netto-positiv NUR mit Regions-Hierarchie + DV-Periode ≥ 300 s +
genug Traffic. Konvergenz/Schleifenfreiheit ist noch NICHT zeitaufgelöst validiert. Darum hier das
konkrete Protokoll-Design **und der Validierungs-Gate, der vor jeder Firmware-Zeile bestehen muss.***

Baut auf `../MeshCore_Hybrid_Routing_v2_Robustheit.md` (H1–H7) und der unsichtbaren Schicht
(`Invisible_Optimizing_Layer.md`) auf. Abgrenzung: die unsichtbare Schicht ist gratis/passiv; der
Backbone ist mächtiger, kostet aber Kontroll-Airtime → eigene, vorsichtige Stufe.

---

## 1. Architektur (ZRP-Prinzip: proaktiv im Kern, reaktiv am Rand)
```
L2  Client-Kante  : reaktiv. Discovery-Short-Circuit: Flood nur bis zum nächsten Backbone-Repeater,
                    von dort Backbone-Unicast. Fallback auf heutiges Flood-and-cache, wenn keine
                    Backbone-Route (Mixed-FW / Partition).
L1b Inter-Region  : Border-Repeater tauschen AGGREGIERT aus: "Region X erreichbar über mich, Kosten C".
L1a Intra-Region  : proaktives DV NUR innerhalb der Region (wenige Knoten, schnelle Konvergenz).
L0  Link-Sensing  : EWMA-ETX aus passivem Lernen (existiert: putNeighbour + 2-Hop-Tabelle).
```
**H1 (Regions-Hierarchie) ist PFLICHT** — die Sim zeigte: flaches netzweites DV kippt bei voller
Adoption ins Airtime-Minus. Regionen kommen aus dem vorhandenen `region_map`.

## 2. Wire-Format (mixed-firmware-sicher)
- **Neuer, ignorierbarer Payload-Typ** `PAYLOAD_TYPE_DV = 0x0C` (reserviert; Alt-Knoten verwerfen ihn
  wirkungslos → graceful). KEINE Änderung bestehender Typen.
- **DV-Update** (kompakt, ein Paket): `{ seqno(u16), n, [ dest(region-id | repeater-hash), metric(ETX, u16), fd(feasible distance, u16) ] × n ]`. Intra-Region listet Repeater; Inter-Region (Border) listet aggregierte Regionen.
- **Zero-Hop-Versand:** als lokaler Route-Typ, den nur direkte Nachbarn verarbeiten und **nicht**
  weiterfluten (wie Adverts). Das ist der Schlüssel gegen Kontroll-Airtime-Explosion — kein
  netzweites LSA-Fluten (OSPF-Falle).

## 3. Protokoll-Regeln
- **Periode ≥ 300 s** (Sim: darunter frisst Kontroll-Traffic den Nutzen; Default 600 s, CLI-tunbar).
- **Babel-Feasibility + Seqno (H2):** ein Nachbar wird nur Next-Hop, wenn seine annoncierten Kosten
  **echt kleiner** als die zuletzt selbst erreichte feasible distance sind → Schleifenfreiheit
  *während* der Konvergenz, ohne globales Topologiebild. Pro Ziel Sequenznummer.
- **Feasible-Successor-Backup (H3):** pro Ziel primärer + ein vorab schleifenfrei validierter
  Backup-Next-Hop. Primär-Ausfall → sofort Backup, ohne Re-Flood.
- **Metrik:** ETX aus EWMA-SNR + Advert-Empfangsrate (zuverlässigkeitsdominant, v2-H4), mit Hysterese
  (Wechsel nur bei ≥ ~15 % Verbesserung) gegen Flattern.
- **Daten-Routing:** Ziel per Backbone bekannt → Unicast entlang DV-Pfad (spart Flood-Airtime).
  Sonst → Discovery-Short-Circuit, sonst → voller Flood (heutiges Verhalten, immer als Fallback).
- **Stabilitäts-Gating (H4):** flatternde Knoten (advert_count-Profil) dürfen Endpunkt, aber nicht
  bevorzugter Transit sein.

### 3a. Churn-Härtung (PFLICHT — durch Gate 3 belegt notwendig)
Das rein periodische DV (nur Punkte oben) fiel im zeitaufgelösten Churn-Gate durch: nach Churn-Stopp
blieben **persistente Inter-Region-Aggregat-Loops** (zwei Border-Router zeigten gegenseitig auf je ein
noch lebendes Aggregat einer dritten Ziel-Region; die per-Origin geführte Babel-FD lehnte keinen der
beiden ab). Folgende drei Mechanismen sind daher **Teil der Spezifikation und nicht optional** — sie
bringen Gate 3 von FAIL (ALT) auf PASS (siehe `study/Phase2_Convergence_Validation.md`):

1. **Trigger-on-change (rate-limitiert).** Bei Metrik-/Next-Hop-Änderung sofort ein DV-Update senden
   statt nur periodisch → schnellere Re-Konvergenz und sofortige Ausbreitung von Retractions.
   **Rate-Limit zwingend** (`discover_limiter`-Stil): pro Knoten min. Abstand zwischen getriggerten
   Updates (Sim: ≥ 2 Ticks/60 s) **und** netzweite Obergrenze getriggerter Sender je Zeitfenster →
   kein Update-Sturm / keine Airtime-Explosion. Überzählige Trigger bleiben „dirty" und feuern im
   nächsten freien Slot.
2. **Hold-down + Route-Poisoning bei Knoten-/Link-Ausfall.** Eine verlorene Route ohne lebenden
   Feasible-Successor-Backup wird **explizit mit erhöhter Seqno auf ∞ zurückgezogen (poison)** — so
   überschreibt die Retraction stale Aggregate beim Empfänger, statt von ihnen überstimmt zu werden.
   Anschließend kurze **Hold-down-Zeit** (Sim: ~2 Annoncen-Perioden) für dieses Ziel, in der **keine
   schlechtere Alternative** akzeptiert wird → verhindert das voreilige Annehmen einer Loop-Route
   und damit die Restschleifen. (Aggregat-Retraction läuft trigger-on-change, s. Punkt 1.)
3. **Origin-unabhängige Aggregat-Feasibility (Babel-Invariante für H1).** Die feasible distance für
   ein Region-Aggregat `("R", dreg)` wird **pro Ziel-Region, nicht pro ABR-Origin** geführt: ein
   Aggregat darf nur Next-Hop werden, wenn seine Kosten **echt kleiner** als die zuletzt erreichte FD
   für *diese* Ziel-Region sind — egal von welchem Border-Router/ABR es kommt. Das stellt den
   „ein Seqno-Besitzer je Ziel"-Charakter der Babel-Bedingung für die aggregierte Hierarchie wieder
   her und ist der Kern-Loop-Breaker; Hold-down/Poisoning räumen die stale Generation dazu auf.

Hysterese (≥ 15 %, Punkt oben) bleibt zusätzlich aktiv. Speicher-Mehrbedarf: ein FD-Wert je
Ziel-Region + ein Hold-down-Timer je betroffenem Ziel (vernachlässigbar gegenüber der DV-Tabelle).

## 4. Mixed-Firmware & „nie schlechter"
- DV-Pakete ignorierbar → Stock-Knoten unberührt. Backbone-Routen werden NUR genutzt, wenn vorhanden
  und besser; sonst exakt heutiges Flood-and-cache. Bei x % backbone-fähigen Repeatern: Sim zeigte
  graceful + ab dem ersten Knoten netto-positiv (mit H1). Default-OFF bis Bench-Freigabe.

## 5. Speicher (fix, keine dyn. Allokation)
DV-Tabelle je Region + Nachbar-Kosten + Feasible-Successors. v2-Schätzung ~1–3 KB → passt auf
nRF52840 (256 KB) und ESP32-S3 (512 KB). Tabellen fix dimensioniert, Verdrängung least-stable.

## 6. VALIDIERUNGS-GATE (muss VOR Firmware-Code bestehen)
Die Backbone-Sim lieferte die Airtime-Ökonomie, aber NICHT die Korrektheit über Zeit. Vor Code:
1. **Zeitaufgelöste Konvergenz:** konvergiert das DV auf der realen Topologie? **Null transiente
   Schleifen** dank Feasibility? Konvergenzzeit messen.
2. **Churn/Flattern:** unter Knoten-Churn (advert_count-Profil) — bleiben die Routen stabil und
   re-konvergieren nach Churn-Stopp vollständig (Restschleifen=0, Restwechsel=0)? Erfordert die
   Churn-Härtung aus Abschnitt 3a (Trigger-on-change + Hold-down/Poisoning + origin-unabhängige
   Aggregat-FD); ohne sie bleiben persistente H1-Aggregat-Loops (FAIL). Wechselrate quantifizieren.
3. **Mixed-Firmware-Sweep:** x % backbone-fähig, Rest Stock — graceful? ab wann netto-positiv? nie
   schlechter?
4. **Kontroll-Budget unter realen Advert-Intervallen/Duty-Cycle:** passt der DV-Overhead neben dem
   bestehenden Traffic ins 10-%-Sub-Band-Budget?
Erst wenn 1–4 bestehen → mehrteilige Firmware-Umsetzung (Payload-Typ-Handler, DV-Tabelle, periodischer
Zero-Hop-Austausch, Routen-Lookup integriert mit `sendDirect`/Flood-Fallback), **default-OFF**, Bench-Test.

## 7. Warum design-first (ehrlich)
Ein proaktives DV-Protokoll mit Schleifenfreiheit + Konvergenz ist Korrektheit-kritisch: ein Fehler
erzeugt **Routing-Loops/Instabilität in einem Live-Mesh**. Das blind zu codieren, bevor die
zeitaufgelöste Konvergenz validiert ist, widerspricht der Projektpriorität (Qualität/Stabilität).
Daher: dieses Design + der Validierungs-Gate sind der verantwortliche „nächste Schritt" für Phase 2;
der Code folgt nach bestandener Validierung.
