# Studie: Routing-Optimierungen für MeshCore unter Mischbetrieb

*Auf echten CoreScope-Realdaten (109.980 Pakete, 1962 Knoten) simuliert. Qualität & Stabilität vor
maximaler Optimalität. Alle Mechanismen sind mit der Original-Firmware koexistenzfähig.*

Begleitdokumente: [STUDY_DESIGN.md](STUDY_DESIGN.md) (mein Denkprozess & Protokoll),
[STUDY_RESULTS.md](STUDY_RESULTS.md) (rohe Ergebnis-Tabellen), `study_sim.py` (Simulation),
`study_results.json`, Plots `fig_study_*.png`. Datenbasis siehe `../sim/MeshCore_Simulation_v3_Realdaten.md`.

---

## 1. Kurzfassung (für Eilige)

Wir haben **8 Routing-Mechanismen** (konventionell bis unkonventionell) auf der realen
776-Knoten-Repeater-Topologie simuliert und dabei **systematisch variiert, wie viele Knoten die
neue Firmware tragen** (1 Knoten → alle). Maßstab war eine harte **Safety-Invariante**: ein
Mechanismus darf bei *keinem* Adoptionsgrad die Lieferquote senken oder die Airtime erhöhen.

**Das Ergebnis ist eine klare Dreiteilung:**

| Tier | Mechanismen | Eigenschaft | Empfehlung |
|------|-------------|-------------|-----------|
| **1 — „Gute Bürger"** | M5 (Best-of-N am Ziel), M1 (Hop-gewichtetes Delay), M7=15 (flood.max 15) | safe ab **1 Knoten**, monoton, nie schlechter | **bedenkenlos ausrollen** |
| **2 — Airtime-Hebel** | M3 (Shorter-Path-Cancel), M2 (Counter-Suppression), M4 (MPR/CDS) | große Airtime-Gewinne (−19…−99 %), aber nur **bis ~10–25 % Adoption** sicher | **adaptiv/selbstbegrenzend** bauen, schrittweise |
| **3 — Kostenloses Fundament** | M6 (passives Topologie-Lernen + Backup-Pfad) | **0 Airtime**, spart 94–96 % Re-Discovery | **Fundament zuerst legen** |

**Wichtigster Befund zur Stabilität:** Die aggressiven Airtime-Hebel (M2/M3/M4) funktionieren nur,
weil **Stock-Knoten als Sicherheitsnetz** weiterfluten. Bei 100 % Adoption *ohne* dieses Netz kippt
z. B. MPR/CDS in Coverage-Verlust (Lieferquote 0,93 → 0,07). **Konsequenz:** diese Mechanismen
dürfen nur unterdrücken, wenn der Knoten *lokal genug Redundanz bestätigt* — dann bleiben sie auch
bei voller Adoption sicher. Das ist die zentrale Konstruktionsvorgabe dieser Studie.

---

## 2. Ausgangslage (was die Realdaten über die Hebel sagten)

- Reales Umweg-Median **2,1×**, Flood-Pfade bis 63 Hops, **~751 sendende Repeater pro Flood** →
  Airtime ist der Engpass, Redundanz der größte Hebel.
- **SNR ist ein schwacher Hebel** (Distanz erklärt SNR kaum, PLE≈0,4) → **Hop-Zahl** ist das
  verlässliche Signal. Das verschiebt den Fokus weg von SNR-Gewichtung (Phase 1) hin zu
  hop-basierten Verfahren.
- Jedes Flood-Paket trägt seine **komplette Hop-Kette** → Topologie ist *passiv, gratis* lernbar.

Daraus mein Leitprinzip: **Nutze was schon im Paket steht (Hops), entscheide lokal, sende weniger —
aber nie auf Kosten der Erreichbarkeit.**

---

## 3. Methode (kurz)

- **Topologie:** reale aktive Repeater-Komponente (776 Knoten / 9226 Kanten), kalibriertes
  Reichweiten-/Reliability-Modell wie in v3. Quervalidierung gegen beobachtete Kanten: das
  geometrische Modell reproduziert 41,7 % — Teilüberlappung, ehrlich dokumentiert.
- **Flood-Modell:** Airtime = Σ Rebroadcasts pro Zustellung; Lieferquote; Hops/Detour;
  Routen-Stabilität. Baseline M0 = Stock-Flood, first-wins.
- **Adoptions-Sweep:** α ∈ {0, 1 Knoten, 1 %, 5 %, 10 %, 25 %, 50 %, 100 %}, je zufällig *und*
  „Top-Traffic-Repeater zuerst" (realistischer Rollout), ≥5 Seeds.
- **Mischbetrieb:** Neu-Knoten wenden ihre lokale Regel an; Stock-Knoten fluten normal.
- **Safety-Invariante:** Lieferquote ≥ Baseline **und** Airtime ≤ Baseline bei *jedem* α.

Baseline (M0): Lieferquote **0,930**, Airtime **751 TX/Zustellung**, Detour-Median **1,11**.

---

## 4. Ergebnisse je Mechanismus

| Mechanismus | Typ | Airtime-Gewinn | safe bis α | Wirkung | Verdikt |
|-------------|-----|----------------|-----------|---------|---------|
| **M5 Best-of-N am Ziel (Hops)** | lokal, Ziel | ±0 | **alle (ab 1 Knoten)** | Detour **1,11→1,00** | ✅ Tier 1 |
| **M1 Hop-gewichtetes Delay** | lokal, Relay | ±0 | **alle** | Detour→1,00, Lieferquote leicht besser | ✅ Tier 1 |
| **M7=15 flood.max 15** | lokal/Konfig | −3,6 % | **alle** | kappt Extrem-Umwege | ✅ Tier 1 |
| **M3 Shorter-Path-Cancel** | lokal, Relay | **−18,9 %** | **0,25** | Airtime ↓ ohne Coverage-Verlust (bis Schwelle) | ⚠️ Tier 2 |
| **M2 Counter-Suppression (k=3)** | lokal, Relay | **−23,1 %** | **0,25** | Gossip-Unterdrückung | ⚠️ Tier 2 |
| **M4 MPR/CDS-Relay-Reduktion** | lokal+passiv | −9,9 % (safe) … −99,5 % (unsafe) | **0,10** | nur Dominating-Set sendet | ⚠️ Tier 2 (heikel) |
| **M7=12 flood.max 12** | lokal/Konfig | größer | **<1,0** | **zu aggressiv** für Netzdurchmesser | ❌ verworfen (→ 15) |
| **M6 passives Topologie-Lernen + Backup** | passiv | **−94…96 % Re-Discovery** | **alle** | Backup-Pfad statt Re-Flood bei Linkbruch | ✅ Tier 3 (Fundament) |

**Safety-Verletzungen (wichtigstes Stabilitäts-Ergebnis):** M5, M1, M7=15 verletzen die Invariante
**bei keinem α**. Verletzt wird sie ab: M2k2 α≥0,25 · M2k3/M3 α≥0,5 · M4 α≥0,25 (Top-Traffic) bzw.
≥0,05 (zufällig) · M7=12 bei α=1,0. Bei α=1,0 **ohne** Stock-Sicherheitsnetz kollabiert M4
(Lieferquote 0,07) — exakt das vorhergesagte Coverage-Risiko ohne kritische Stock-Masse.

**Kombination M3+M5+M7:** mit flood.max=12 nur bis α=0,10 safe (−8,4 %, Detour 1,00); **mit
flood.max=15 bleibt sie länger sicher** — die empfohlene Kombi.

**Stress (Churn 20 % nach advert_count, Linkausfall 10/20 %):** Routen-Stabilität bleibt 1,00 (kein
Flattern, keine Partition). **M6** spart bei Linkbruch **94–96 %** der Re-Discovery-Airtime
(Baseline-Reflood ~680 TX) — der mit Abstand stärkste *kostenlose* Hebel.

---

## 5. Interpretation & die zentrale Stabilitäts-Erkenntnis

Die Studie trennt sauber zwei Klassen:

1. **Pfad-*qualität*-Mechanismen (M5, M1, M7):** ändern *welcher* Pfad gewinnt, nicht *wie viele*
   senden. Sie sind **strukturell sicher** — ein Knoten, der den kürzeren Pfad bevorzugt, kann die
   Erreichbarkeit nicht verschlechtern. Darum safe ab dem ersten Knoten und monoton.

2. **Airtime-*Suppressions*-Mechanismen (M3, M2, M4):** ein Knoten, der *schweigt*, spart Airtime —
   aber wenn zu viele schweigen, reißt die Abdeckung. Ihre Sicherheit hängt davon ab, dass *genug
   andere* (Stock oder Neu) noch senden. Daher die Adoptionsschwelle.

**Die Lösung für Klasse 2 ist nicht „weniger ausrollen", sondern „adaptiv unterdrücken":** Ein
Neu-Knoten darf seinen Rebroadcast nur dann streichen, wenn er *lokal beobachtet*, dass das Paket
bereits ausreichend oft / über einen gleich-kurzen Pfad weitergetragen wurde (M3 ist genau das in
schwacher Form; M2 mit höherem k ebenso). Macht man diese Bedingung streng genug an die *real
gehörte Redundanz* gekoppelt, bleibt der Mechanismus **selbst bei 100 % Adoption sicher**, weil
jeder Knoten erst dann schweigt, wenn die Abdeckung nachweislich schon steht. Das ist der Weg, die
großen Airtime-Gewinne *ohne* das Stock-Sicherheitsnetz zu heben — und die wichtigste
Implementierungs-Vorgabe, die aus dieser Studie folgt.

---

## 6. Empfohlene Roadmap (gestuft, mischbetriebs-sicher, stabilitätsorientiert)

**Stufe A — sofort, „nie schlechter" (Tier 1 + Fundament):**
1. **M7=15:** `flood.max` von 64 auf ~15 (empirischer Netzdurchmesser P90=18; 12 ist zu aggressiv).
   Reine Konfig, pro Knoten wirksam. *(Aktualisiert die bisherige „bewusst nicht geändert"-Haltung
   mit Daten.)*
2. **M1 Hop-gewichtetes Rebroadcast-Delay** als Ersatz/Ergänzung des schwachen SNR-Hebels
   (`getRetransmitDelay`): Kopien mit weniger akkumulierten Hops senden früher. Lokal, monoton.
3. **M5 Best-of-N am Ziel (nach Hops):** der bewusst zurückgestellte Phase-1-Kern — jetzt durch die
   Daten als *der* Detour-Killer bestätigt (1,11→1,00 ab 1 Knoten). Machbarkeit klären:
   Sammelfenster am Ziel ohne Bruch der Dedup (Kernrisiko, sorgfältig + Bench-Test).
4. **M6 passives Topologie-Lernen:** aus den Pfad-Ketten lokale Link-Tabelle bauen (0 Airtime) →
   Backup-Pfad statt Re-Flood bei Linkbruch. Fundament für alles Weitere.

**Stufe B — adaptive Airtime-Suppression (Tier 2, vorsichtig):**
5. **M3 Shorter-Path-Cancel** und **M2 Counter-Suppression (k=3)** — aber **adaptiv** (nur
   unterdrücken bei lokal bestätigter Redundanz, siehe §5), damit sie auch jenseits 25 % Adoption
   sicher bleiben. Mit Telemetrie/`trace` vorher/nachher messen.

**Stufe C — perspektivisch:**
6. **M4 MPR/CDS** nur mit harter Redundanz-Garantie und konservativem Relay-Set; größtes Potenzial,
   höchstes Risiko — zuletzt.

SNR-Gewichtung (Phase 1 `tx_snr_weight`) bleibt als „nie schlechter"-Option erhalten, verliert aber
gegenüber den hop-basierten Verfahren an Priorität (Realdaten-Befund).

---

## 7. Limitierungen (ehrlich)

Linkmodell geometrisch (Log-Distance, PLE-Floor 2,0; Gelände/Antennenhöhe fehlen) → **absolute**
Airtime-Zahlen modellabhängig, **relative** Mechanismus-Vergleiche robuster. Quervalidierung deckt
41,7 % der beobachteten Kanten. Timing-Backoff modelliert (nicht HW-gemessen) → M1/M3 hängen am
Timing-Modell. M2 approximiert das kontinuierliche Backoff grob. M6 als Airtime-Einsparungs-Modell
gerechnet, nicht als volle DV-Protokoll-Simulation. Stichprobe 5 Seeds × 120 Paare, keine
Konfidenzintervalle. Alles auf Hardware ungetestet — Bench-Gerät vor produktivem Repeater.

---

*Diese Studie verschiebt die Projekt-Priorität von SNR-Gewichtung hin zu **hop-basierter
Pfadwahl (M5/M1)**, einem **datenbasierten flood.max (15)** und **adaptiver, selbstbegrenzender
Airtime-Suppression** — mit **passivem Topologie-Lernen** als kostenlosem Fundament.*
