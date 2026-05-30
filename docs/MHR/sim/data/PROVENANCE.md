# Datensatz-Herkunft (Provenance) & Nutzung

Dieser Ordner enthält den **realen Datensatz**, mit dem die MHR-Simulationen kalibriert/validiert
wurden — damit jede:r die Ergebnisse reproduzieren oder eigene Auswertungen bauen kann.

## Quelle
- **CoreScope** des MeshRheinland-Netzes: `https://corescope.meshrheinland.de` (öffentliche API:
  `/api/nodes`, `/api/observers`, `/api/packets`, `/api/analytics/neighbor-graph`, `/api/analytics/hash-sizes`, `/api/stats`).
- **Erfasst:** Momentaufnahme vom **2026-05-30** (Netz-Stand siehe `stats.json`). CoreScope-Engine
  laut `stats.json`: go `v3.8.2`.
- Abgeholt mit `../mhr_collect_corescope.py` (urllib + Browser-User-Agent, offset-Pagination).

## Inhalt
| Datei | Was | Hinweis |
|---|---|---|
| `packets.jsonl.xz` | **alle 109.980 Rohpakete** (1 JSON/Zeile: `path_json`, `snr`, `rssi`, `observer_*`, `route_type`, `payload_type`, `timestamp`) | xz, 17 MB → ~188 MB entpackt. `xz -dk packets.jsonl.xz`; direkt aus Python: `lzma.open(...,'rt')` |
| `nodes.json` | 1962 Knoten: `public_key`, `lat`/`lon`, `role`, `advert_count`, `relay_count_*`, Scores | |
| `observers.json` | 38 Observer (Empfangs-Knoten) | |
| `neighbor_graph.json` | server-aufgelöste reale Link-Topologie (1034 Kn., 1956 Kanten) mit `avg_snr`/Kante + `ambiguous`-Flag | bestes Link-Modell |
| `hash_sizes.json` | Hash-Größen-Verteilung (1/2/3-Byte) | |
| `topology_edges.json` | aus Pfaden abgeleitete Relay-Kanten (aggregiert) | |
| `snr_calibration.json`, `real_detour_stats.json` | abgeleitete Kennzahlen (SNR-Fit, reale Detour-Statistik) | |
| `stats.json` | Netz-Gesamtzahlen zum Erfassungszeitpunkt | Provenance |

Schema-/Nutzungsdetails + wie man die Sims fährt: siehe `../README.md`.

## Hinweise zu Nutzung & Attribution
- Die Daten beschreiben das **öffentliche** MeshRheinland-LoRa-Mesh; Knotennamen, Positionen und
  Public-Keys stammen aus den von den Betreibern **öffentlich ausgesendeten Adverts** (auf CoreScope
  ohnehin sichtbar). Bitte respektvoll und nur für Forschung/Simulation/Verbesserung des Netzes nutzen.
- Es ist eine **Momentaufnahme** — kein Live-Feed. Für aktuelle Daten den Collector neu laufen lassen.
- Bei Weiterverwendung bitte **CoreScope / MeshRheinland** als Quelle nennen.
- Lizenz des *Forks*: MIT (siehe `../../../license.txt`). Die Daten selbst sind eine Drittquelle
  (CoreScope/MeshRheinland) und unterliegen deren Bedingungen.
