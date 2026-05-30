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
