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

---
## 🇬🇧 English Translation

# Phase 2 — Proactive Regional Backbone: Implementation Design + Validation Plan

*Design-first. Phase 2 is the largest intervention (proactive control plane). The backbone simulation
(`Backbone_Phase2_Study.md`) showed: net-positive ONLY with regional hierarchy + DV period ≥ 300 s +
sufficient traffic. Convergence/loop-freedom has NOT yet been validated in time-resolved fashion. Hence
the concrete protocol design here **and the validation gate that must pass before a single line of firmware is written.***

Builds on `../MeshCore_Hybrid_Routing_v2_Robustheit.md` (H1–H7) and the invisible layer
(`Invisible_Optimizing_Layer.md`). Distinction: the invisible layer is free/passive; the backbone is
more powerful but costs control airtime → its own, careful stage.

---

## 1. Architecture (ZRP principle: proactive at the core, reactive at the edge)
```
L2  Client edge   : reactive. Discovery short-circuit: flood only to the nearest backbone repeater,
                    then backbone unicast from there. Fallback to today's flood-and-cache when no
                    backbone route exists (mixed-FW / partition).
L1b Inter-region  : border repeaters exchange AGGREGATED info: "Region X reachable via me, cost C".
L1a Intra-region  : proactive DV ONLY within the region (few nodes, fast convergence).
L0  Link sensing  : EWMA-ETX from passive learning (exists: putNeighbour + 2-hop table).
```
**H1 (regional hierarchy) is MANDATORY** — the sim showed: flat network-wide DV tips into airtime
deficit at full adoption. Regions come from the existing `region_map`.

## 2. Wire Format (mixed-firmware-safe)
- **New, ignorable payload type** `PAYLOAD_TYPE_DV = 0x0C` (reserved; legacy nodes discard it
  harmlessly → graceful). NO changes to existing types.
- **DV update** (compact, one packet): `{ seqno(u16), n, [ dest(region-id | repeater-hash), metric(ETX, u16), fd(feasible distance, u16) ] × n ]`. Intra-region lists repeaters; inter-region (border) lists aggregated regions.
- **Zero-hop transmission:** as a local route type processed only by direct neighbors and **not**
  re-flooded (like adverts). This is the key against control-airtime explosion — no network-wide
  LSA flooding (the OSPF trap).

## 3. Protocol Rules
- **Period ≥ 300 s** (sim: below this, control traffic consumes the benefit; default 600 s, CLI-tunable).
- **Babel feasibility + seqno (H2):** a neighbor becomes next-hop only if its announced cost is
  **strictly less than** the most recently achieved feasible distance → loop-freedom *during*
  convergence, without a global topology view. Per-destination sequence number.
- **Feasible-successor backup (H3):** per destination, one primary + one pre-validated loop-free
  backup next-hop. Primary failure → immediately use backup, no re-flood.
- **Metric:** ETX from EWMA-SNR + advert reception rate (reliability-dominant, v2-H4), with hysteresis
  (switch only at ≥ ~15% improvement) to prevent flapping.
- **Data routing:** destination known via backbone → unicast along DV path (saves flood airtime).
  Otherwise → discovery short-circuit, otherwise → full flood (today's behavior, always as fallback).
- **Stability gating (H4):** flapping nodes (advert_count profile) may be endpoints but not preferred
  transit nodes.

### 3a. Churn Hardening (MANDATORY — proven necessary by Gate 3)
The purely periodic DV (points above only) failed the time-resolved churn gate: after churn stopped,
**persistent inter-region aggregate loops** remained (two border routers mutually pointed at a still-live
aggregate of a third destination region; the per-origin Babel FD rejected neither of them). The
following three mechanisms are therefore **part of the specification and not optional** — they bring
Gate 3 from FAIL (OLD) to PASS (see `study/Phase2_Convergence_Validation.md`):

1. **Trigger-on-change (rate-limited).** On metric/next-hop change, immediately send a DV update
   instead of waiting for the periodic interval → faster re-convergence and immediate propagation of
   retractions. **Rate limit mandatory** (`discover_limiter` style): per-node minimum interval between
   triggered updates (sim: ≥ 2 ticks/60 s) **and** network-wide ceiling on triggered senders per time
   window → no update storm / no airtime explosion. Excess triggers remain "dirty" and fire in the
   next available slot.
2. **Hold-down + route poisoning on node/link failure.** A lost route with no live feasible-successor
   backup is **explicitly withdrawn with an incremented seqno to ∞ (poison)** — so the retraction
   overwrites stale aggregates at the receiver instead of being outvoted by them. Followed by a short
   **hold-down period** (sim: ~2 announcement periods) for this destination, during which **no worse
   alternative** is accepted → prevents premature adoption of a loop route and thus eliminates
   residual loops. (Aggregate retraction runs trigger-on-change, see point 1.)
3. **Origin-independent aggregate feasibility (Babel invariant for H1).** The feasible distance for
   a region aggregate `("R", dreg)` is tracked **per destination region, not per ABR origin**: an
   aggregate may become next-hop only if its cost is **strictly less than** the most recently achieved
   FD for *that* destination region — regardless of which border router/ABR it comes from. This
   restores the "one seqno owner per destination" character of the Babel condition for the aggregated
   hierarchy and is the core loop-breaker; hold-down/poisoning clean up the stale generation alongside it.

Hysteresis (≥ 15%, point above) remains additionally active. Additional memory cost: one FD value
per destination region + one hold-down timer per affected destination (negligible compared to the DV
table).

## 4. Mixed Firmware & "Never Worse"
- DV packets ignorable → stock nodes unaffected. Backbone routes are ONLY used when present and
  better; otherwise exactly today's flood-and-cache. With x% backbone-capable repeaters: sim showed
  graceful + net-positive from the first node (with H1). Default-OFF until bench approval.

## 5. Memory (fixed, no dynamic allocation)
DV table per region + neighbor costs + feasible successors. v2 estimate ~1–3 KB → fits on
nRF52840 (256 KB) and ESP32-S3 (512 KB). Tables fixed-size, eviction least-stable.

## 6. VALIDATION GATE (must pass BEFORE firmware code)
The backbone sim provided the airtime economics but NOT correctness over time. Before code:
1. **Time-resolved convergence:** does the DV converge on the real topology? **Zero transient
   loops** thanks to feasibility? Measure convergence time.
2. **Churn/flapping:** under node churn (advert_count profile) — do routes remain stable and
   fully re-converge after churn stops (residual loops=0, residual switches=0)? Requires the
   churn hardening from Section 3a (trigger-on-change + hold-down/poisoning + origin-independent
   aggregate FD); without it, persistent H1 aggregate loops remain (FAIL). Quantify switch rate.
3. **Mixed-firmware sweep:** x% backbone-capable, remainder stock — graceful? net-positive at
   what threshold? never worse?
4. **Control budget under real advert intervals/duty cycle:** does DV overhead fit alongside
   existing traffic within the 10%-sub-band budget?
Only when 1–4 pass → multi-part firmware implementation (payload type handler, DV table, periodic
zero-hop exchange, route lookup integrated with `sendDirect`/flood fallback), **default-OFF**, bench test.

## 7. Why Design-First (honestly)
A proactive DV protocol with loop-freedom + convergence is correctness-critical: one bug creates
**routing loops/instability in a live mesh**. Coding this blindly before time-resolved convergence
is validated contradicts the project priority (quality/stability). Therefore: this design + the
validation gate are the responsible "next step" for Phase 2; the code follows after validation passes.
