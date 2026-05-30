# MeshCore – Analyse der Pfadfindung und Vorschläge gegen Umwege

Analyse-Basis: Quellcode von `meshcore-dev/MeshCore`, Branch `main` (geklont am 29.05.2026). Alle Code-Verweise beziehen sich auf diesen Stand (`src/Mesh.cpp`, `src/Dispatcher.cpp`, `src/helpers/BaseChatMesh.cpp`, `examples/simple_repeater/MyMesh.cpp`).

---

## 1. Kurzfassung

MeshCore nutzt **kein** klassisches Routing mit Metrik (kein OLSR/Batman/AODV mit Kostenfunktion). Es ist ein **hybrides Verfahren**: Die erste Nachricht an einen Kontakt wird geflutet (Flood), die dabei entstehende Route wird zwischengespeichert, und **alle weiteren Pakete laufen fest über genau diesen einen Pfad** (Direct Routing über die gecachte Hop-Liste).

Der entscheidende Punkt für deine Beobachtung „der Traffic nimmt oft große Umwege": **Es gewinnt nicht der kürzeste oder beste Pfad, sondern der, dessen Flood-Kopie zufällig zuerst beim Ziel ankommt.** Dieser Pfad wird dann eingefroren und unbegrenzt weiterverwendet, bis er dreimal ausfällt. Ein einmal etablierter Umweg „klebt" also.

Die gute Nachricht: Die Firmware enthält bereits die Maschinerie für eine signalqualitäts-gewichtete Ausbreitung (`calcRxDelay` / `packetScore`), sie ist nur **per Default abgeschaltet**. Das ist der größte Hebel und kostet keine Code-Änderung.

---

## 2. Wie die Pfadfindung tatsächlich funktioniert

### 2.1 Die drei Phasen einer Verbindung

**Phase 1 – Discovery-Flood.** Sender kennt noch keinen Pfad (`out_path_len == OUT_PATH_UNKNOWN`, `BaseChatMesh.cpp:42`). Das Paket wird als `ROUTE_TYPE_FLOOD` ausgesendet. Jeder Repeater, der es hört und weiterleiten darf, hängt seinen eigenen Hash an die Pfadliste an und sendet erneut:

```
// src/Mesh.cpp:330  routeRecvPacket()
self_id.copyHashTo(&packet->path[n * packet->getPathHashSize()], ...);
packet->setPathHashCount(n + 1);
uint32_t d = getRetransmitDelay(packet);
return ACTION_RETRANSMIT_DELAYED(packet->getPathHashCount(), d);
```

Die Pfadliste im Paket ist damit eine wachsende Kette der durchlaufenen Repeater.

**Phase 2 – Path Return.** Das Ziel empfängt typischerweise **mehrere Kopien** desselben Pakets über verschiedene Wege. Verarbeitet wird aber nur die **erste**:

```
// src/Mesh.cpp:138
// NOTE: this is a 'first packet wins' impl. When receiving from multiple paths, the first to arrive wins.
//       For flood mode, the path may not be the 'best' in terms of hops.
// FUTURE: could send back multiple paths, using createPathReturn(), and let sender choose which to use(?)
```

Das Ziel extrahiert den Pfad aus dieser ersten Kopie und schickt ihn dem Sender per `createPathReturn()` **direkt rückwärts** zurück (`Mesh.cpp:167`).

**Phase 3 – Cached Direct.** Sender speichert den Pfad und nutzt ihn von nun an für **jede** weitere Nachricht. Die Übernahme erfolgt kommentarlos und ohne Vergleich:

```
// src/helpers/BaseChatMesh.cpp:304  onContactPathRecv()
// NOTE: default impl, we just replace the current 'out_path' regardless, whenever sender sends us a new out_path.
// FUTURE: could store multiple out_paths per contact, and try to find which is the 'best'(?)
from.out_path_len = mesh::Packet::copyPath(from.out_path, out_path, out_path_len);
```

### 2.2 Wie die Sendereihenfolge im Flood entsteht (das Timing)

Hier liegt die eigentliche Ursache. Wenn ein Repeater eine Flood-Kopie hört, gibt es zwei Verzögerungs-Stufen:

1. **RX-Delay (Empfangsseite, signalabhängig).** In `Dispatcher::checkRecv()` wird pro Paket ein `score` aus SNR und Länge berechnet und daraus eine Wartezeit:

```
// src/Dispatcher.cpp:206 / 242
score = _radio->packetScore(_radio->getLastSNR(), len);
int _delay = calcRxDelay(score, air_time);
if (_delay < 50) processRecvPacket(pkt);          // sofort verarbeiten
else _mgr->queueInbound(pkt, futureMillis(_delay)); // verzögert
```

Sinn: Knoten mit **starkem** Signal (gute, meist geografisch direkte Verbindung) würden zuerst weiterleiten und so die schwachen, weiter entfernten Kopien unterdrücken (Dedup via `hasSeen()`). Das wäre eine intelligente, qualitätsgeleitete Flutung.

**Aber:** Der Repeater überschreibt `calcRxDelay` und schaltet es per Default ab:

```
// examples/simple_repeater/MyMesh.cpp:534
int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;   // <-- Default: aus
  return (int)((pow(_prefs.rx_delay_base, 0.85f - score) - 1.0) * air_time);
}
// :874  _prefs.rx_delay_base = 0.0f;   // turn off by default, was 10.0;
```

2. **TX-Delay (Sendeseite, rein zufällig).** Mit abgeschaltetem RX-Delay bleibt nur eine **zufällige** Wartezeit vor dem Weitersenden:

```
// examples/simple_repeater/MyMesh.cpp:539
uint32_t MyMesh::getRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = _radio->getEstAirtimeFor(...) * _prefs.tx_delay_factor;  // tx_delay_factor=0.5
  return getRNG()->nextInt(0, 5*t + 1);   // gleichverteilter Zufall
}
```

**Konsequenz:** Welche Kopie zuerst beim Ziel ankommt, entscheidet im Wesentlichen ein **Würfelwurf an jedem Hop** – nicht die Signalqualität und nicht die Hop-Zahl. Ein 4-Hop-Umweg, dessen Repeater zufällig kurze Delays würfeln, schlägt regelmäßig den sauberen 2-Hop-Direktweg, dessen Knoten lange Delays würfeln.

---

## 3. Ursachen der Umwege (zusammengefasst)

| # | Ursache | Code-Beleg | Wirkung |
|---|---------|-----------|---------|
| 1 | **„First packet wins"** – Ziel cached die zuerst eintreffende Kopie, nicht die beste | `Mesh.cpp:138-140` | Zufälliger statt optimaler Pfad |
| 2 | **Pfad ist klebrig** – keine periodische Neubewertung, Wechsel erst nach mehrfachem Ausfall | `BaseChatMesh.cpp:779 resetPathTo()` (nur app-getriggert) | Umweg bleibt dauerhaft bestehen |
| 3 | **Kein Pfad-Vergleich** – Sender übernimmt jeden empfangenen Pfad ungeprüft | `BaseChatMesh.cpp:304-307` | Auch ein schlechterer Pfad überschreibt einen guten |
| 4 | **SNR-Gewichtung deaktiviert** – `rx_delay_base = 0` ⇒ reines Zufalls-Timing | `MyMesh.cpp:534-536, 874` | Ausbreitung folgt nicht der Linkqualität |
| 5 | **`flood.max = 64`** – sehr hohes Hop-Limit | `MyMesh.cpp:888`, `docs/cli_commands.md:625` | Weit entfernte Umweg-Kopien bleiben „im Rennen" |

Hinzu kommt, dass es **keine Metrik im Paket** gibt: weder kumulierte SNR noch ein Kostenwert wird mitgeführt und verglichen. Die Hop-Zahl steht zwar implizit über `getPathHashCount()` zur Verfügung, wird beim Ziel aber nicht zur Auswahl genutzt.

---

## 4. Optimierungen (priorisiert)

### Stufe A – Sofort, nur Konfiguration (kein Code, niedriges Risiko)

**A1. SNR-gewichtete Flutung einschalten.** Das ist der wichtigste Hebel. `rx_delay_base` auf einen Wert > 0 setzen (Original-Default war `10.0`). Dann warten schwache/entfernte Knoten länger, starke (kurze) Links senden zuerst und unterdrücken die Umweg-Kopien automatisch. Effekt: Flood breitet sich bevorzugt entlang qualitativ guter, meist kürzerer Wege aus ⇒ die zuerst eintreffende (und damit gecachte) Route ist deutlich häufiger die direkte.

- Empfehlung: auf jedem Repeater `rx_delay_base` testweise auf `8`–`12` setzen, Verhalten über `score`-Logging beobachten.
- Trade-off: minimal höhere Latenz pro Hop, dafür weniger Kollisionen und kürzere Pfade.

**A2. `flood.max` an den Netz-Durchmesser anpassen.** 64 Hops sind für fast jedes reale Mesh massiv überdimensioniert. Auf „realer Durchmesser + 2–3 Reserve" setzen (z. B. `8`–`12`). Damit sterben weiträumige Umweg-Kopien, bevor sie als Sieger ankommen können.

```
set flood.max 10
```

**A3. Loop-Detection aktivieren** (falls nicht aktiv), verhindert kreisende Pakete, die Airtime fressen und Umwege begünstigen (`MyMesh.cpp:436`, `docs/cli_commands.md:482`, ab FW 1.14).

**A4. `tx_delay_factor` moderat senken**, wenn das Netz dünn ist – verkürzt die zufällige Streuung und damit die Latenz; bei dichten Netzen eher belassen, um Kollisionen zu vermeiden (`docs/cli_commands.md:488`).

### Stufe B – Code-Änderung am Ziel: „Best-of-N statt First-wins"

Kern des Problems ist die `first packet wins`-Logik. Statt die erste Kopie sofort als Pfad zurückzusenden, sollte das Ziel **innerhalb eines kurzen Fensters** (z. B. 1–2× erwartete Airtime) alle eintreffenden Kopien sammeln und die **beste** auswählen.

Auswahlkriterium, gestaffelt:
1. kleinste Hop-Zahl (`getPathHashCount()`),
2. bei Gleichstand: bester kumulierter/minimaler SNR entlang des Pfads.

Skizze (in `Mesh::onRecvPacket`, Zweig `PAYLOAD_TYPE_*` / bzw. `onPeerPathRecv`):

```cpp
// Pseudocode – Ersetzt das sofortige createPathReturn()
if (self_id.isHashMatch(&dest_hash) && decrypt_ok) {
    uint8_t hops = pkt->getPathHashCount();
    if (!hasPendingDiscovery(src_hash)) {
        // erste Kopie: Kandidat merken, Timer starten
        beginDiscoveryWindow(src_hash, pkt->path, pkt->path_len, hops, pkt->getSNR());
    } else if (isBetter(hops, pkt->getSNR(), pending[src_hash])) {
        // bessere Kopie innerhalb des Fensters: Kandidat ersetzen
        updateDiscoveryCandidate(src_hash, pkt->path, pkt->path_len, hops, pkt->getSNR());
    }
    // Path-Return erst NACH Ablauf des Fensters senden (im loop()/Timer)
}
```

- Nutzen: Der zurückgemeldete und damit gecachte Pfad ist nachweislich der kürzeste der gehörten Alternativen.
- Kosten: kleiner Zustandsspeicher pro laufender Discovery + ein Timer; eine zusätzliche Latenz von einem Fenster (einmalig pro Pfad-Aufbau, nicht pro Nachricht).
- Der Code-Kommentar `Mesh.cpp:140` nennt genau diesen Weg bereits als „FUTURE".

### Stufe C – Pfad-Metrik im Paket mitführen

Damit Stufe B (und die Sender-Auswahl) eine belastbare Grundlage hat: bei der Pfad-Anhängung zusätzlich zur Hash-Kette eine **Link-Qualität** akkumulieren. Der TRACE-Pfad macht das bereits vor – dort wird statt eines Hashs der SNR angehängt:

```
// src/Mesh.cpp:60 (TRACE)
pkt->path[pkt->path_len++] = (int8_t)(pkt->getSNR()*4);   // SNR statt Hash
```

Dieselbe Idee lässt sich als optionale „Cost"-Begleitinformation für Discovery-Floods nutzen (minimaler SNR oder Summe der Hop-Kosten), sodass `isBetter()` in Stufe B nicht nur Hops, sondern echte Funkqualität vergleicht. Achtung: erhöht die Paketlänge und erfordert Versionskompatibilität (vgl. die Hash-Size-Migrationshinweise in `docs/cli_commands.md:464`).

### Stufe D – Opportunistisches Pfad-Upgrade beim Sender

Statt jeden empfangenen Pfad blind zu übernehmen (`BaseChatMesh.cpp:305`), sollte der Sender vergleichen und nur **bei Verbesserung** (weniger Hops / besserer SNR) wechseln – und zusätzlich den Pfad **periodisch** (z. B. alle N Stunden oder nach M Nachrichten) per leichtgewichtigem Re-Discovery auffrischen. Das löst Ursache #2 (klebrige Pfade): ein anfänglicher Umweg wird ersetzt, sobald sich ein besserer Weg zeigt, ohne auf einen kompletten Ausfall zu warten.

```cpp
// onContactPathRecv – statt bedingungslosem Überschreiben:
if (from.out_path_len == OUT_PATH_UNKNOWN
    || newHops < currentHops
    || (newHops == currentHops && newSnr > currentSnr)) {
    from.out_path_len = copyPath(...);   // nur bei echter Verbesserung
}
```

---

## 5. Empfohlenes Vorgehen

1. **Zuerst Stufe A** auf allen Repeatern ausrollen (reine Konfig, sofort wirksam, reversibel). In den meisten Fällen verschwinden die gröbsten Umwege schon damit – insbesondere durch A1 (`rx_delay_base`) und A2 (`flood.max`).
2. **Messen**: vorher/nachher die tatsächlichen Pfade per `trace`-Kommando und das `score`/Pfad-Logging (`MyMesh.cpp:471 logRx`) auf einer repräsentativen Strecke vergleichen.
3. **Dann Stufe B** als Firmware-Fork umsetzen, wenn nach A noch systematische Umwege bleiben (löst die Wurzel „first-wins"). C und D sind die langfristige, vollständige Lösung.

## 6. Wichtige Einschränkungen

- Die Stufe-A-Werte sind **netzabhängig** – `rx_delay_base`, `flood.max` und `tx_delay_factor` müssen für deine konkrete Topologie und Knotendichte empirisch eingestellt werden; es gibt keinen universell optimalen Wert.
- Stufen B–D sind Eingriffe ins Kernprotokoll. Pfad-Metriken (C) und geänderte Paketformate brechen potenziell die Kompatibilität mit Knoten älterer Firmware im selben Mesh – gestaffelt und versioniert ausrollen.
- Diese Analyse beruht auf statischem Code-Lesen des `main`-Branch, nicht auf Messung deines konkreten Netzes. Die Diagnose „Umwege durch first-wins + Zufalls-Timing" ist code-belegt; die optimalen Parameter belegt erst dein eigener Vorher/Nachher-Test.

---

*Analysierte Dateien: `src/Mesh.cpp`, `src/Dispatcher.cpp`, `src/helpers/BaseChatMesh.cpp`, `examples/simple_repeater/MyMesh.cpp`, `docs/cli_commands.md`, `docs/packet_format.md`.*
