# MHR-Simulationen & Realdatensatz

Dieser Ordner enthält die Routing-Simulationen des MHR-Forks **und den echten CoreScope-Datensatz**,
mit dem sie kalibriert wurden — damit jede:r die Ergebnisse reproduzieren oder eigene Auswertungen
bauen kann.

## Voraussetzungen
```bash
pip install numpy networkx matplotlib
# zum Entpacken der Rohdaten: xz (macOS: brew install xz, Debian/Ubuntu: apt install xz-utils)
```

## Datensatz (`data/`)
| Datei | Inhalt | Größe |
|---|---|---|
| `data/packets.jsonl.xz` | **vollständige Rohdaten**: 109.980 reale Pakete (1 JSON/Zeile), je mit `path_json` (Hop-Kette), `snr`, `rssi`, `observer_id`, `route_type`, `payload_type`, `timestamp` | 16,6 MB (entpackt 188 MB) |
| `data/nodes.json` | 1962 Knoten: `public_key`, `lat`/`lon`, `role`, `advert_count`, `relay_count_*`, `usefulness_score`, … | 1,3 MB |
| `data/observers.json` | 38 Observer (Empfangs-Knoten) | 24 KB |
| `data/topology_edges.json` | aus den Pfaden abgeleitete beobachtete Relay-Kanten (aggregiert) | 0,7 MB |
| `data/neighbor_graph.json` | **bestes Link-Modell**: server-aufgelöste reale Nachbar-Topologie (1034 Knoten, 1956 Kanten) mit vollen Pubkeys, `avg_snr` **pro Kante** und `ambiguous`-Flag (nur 8,8 % ambig) | 0,6 MB |
| `data/hash_sizes.json` | Hash-Größen-Verteilung (1/2/3-Byte) im Netz | 0,5 MB |
| `data/snr_calibration.json`, `data/real_detour_stats.json` | abgeleitete Kennzahlen (SNR-Fit, reale Detour-Statistik) | klein |

### Reichere CoreScope-Endpoints (für genauere/neue Simulationen)
Die CoreScope-API bietet mehr als die anfangs genutzten Basis-Endpoints — für ein echtes Link-Modell sind besonders nützlich:
- **`/api/analytics/neighbor-graph`** — fertige reale Link-Topologie mit `avg_snr` pro Kante + `ambiguous`-Flag (Quelle von `data/neighbor_graph.json`). Ersetzt das geometrische v3-Modell durch **echte gemessene Kanten** → deutlich genauer.
- **`/api/packets?expand=observations`** — liefert pro Paket ALLE Observer-Empfänge (je eigenes `snr`/`rssi`), statt nur einer Messung → viel dichtere Link-Qualitäts-Daten.
- **`/api/resolve-hops`** (POST) / **`/api/paths/inspect`** (POST) — server-seitige Hash→Knoten-Auflösung (umgeht die 1-Byte-Hash-Ambiguität).
- **`/api/nodes/{pubkey}`**, **`/api/analytics/hash-sizes`**, time-Pagination (`?before=`/`?since=`).

**Quelle:** öffentliche CoreScope-API `https://corescope.meshrheinland.de/api/{nodes,observers,packets}`
(MeshCore Rheinland). Der Datensatz ist eine **Momentaufnahme** (30.05.2026). Rohdaten bewusst als
`.xz` eingebunden (xz packt JSON-Lines ~11×; 188 MB → 16,6 MB, unter GitHubs Limit).

## Schnellstart — v3-Realdaten-Sim aus dem Repo reproduzieren
Die Skripte erwarten die Rohdaten unter `/tmp/cs_data/`. Einmalig vorbereiten:
```bash
mkdir -p /tmp/cs_data
xz -dkc data/packets.jsonl.xz > /tmp/cs_data/packets.jsonl     # entpacken (~188 MB)
cp data/nodes.json data/observers.json /tmp/cs_data/
python3 mhr_sim_real_v3.py        # → sim_results_v3.json, data/*, fig_v3_*.png
```

## Vollständig frische Daten holen (statt Repo-Snapshot)
```bash
python3 mhr_collect_corescope.py            # lädt aktuelle Daten nach /tmp/cs_data/ (paginiert ~110k Pakete)
python3 mhr_sim_real_v3.py
```
Der Collector nutzt urllib mit Browser-User-Agent + offset-Pagination (siehe Kopf der Datei).

## Die Simulationen im Überblick
| Skript | Was | Datenbasis |
|---|---|---|
| `mhr_sim.py` | v1 — synthetische Rheinland-Topologie, MeshCore-Flood vs. MHR | synthetisch *(illustrativ — das angenommene SNR-Modell wird von den Realdaten widerlegt)* |
| `mhr_sim_real.py` | v1 auf 25 echten Knoten | klein, angenommenes Linkmodell |
| `mhr_sim_v2.py` | Stress-Szenarien (Churn, Linkausfall, Partition) | reale 25-Knoten-Topologie |
| `mhr_sim_real_v3.py` | **v3 — auf vollem Realdatensatz kalibriert**: misst reale Topologie, SNR-Fit, reale Detours (Median 2,1×), simuliert Baseline vs. MHR | `data/` + Rohpakete |
| `../study/study_sim.py` | **Mechanismus × Adoptions-Sweep** (1 Knoten → alle), Safety-Invariante | reale 776-Knoten-Topologie |

Alle reproduzierbar mit **Seed 42**. Ergebnisse landen als `*_results*.json` + `fig_*.png` daneben.

## Eigene Auswertungen bauen
- Pakete streamen (nie komplett in den RAM): `for line in open('/tmp/cs_data/packets.jsonl'): p = json.loads(line)`.
  Direkt aus dem `.xz` ohne Entpacken: `import lzma, json; [json.loads(l) for l in lzma.open('data/packets.jsonl.xz','rt')]`.
- **Node→Hash-Mapping:** der MeshCore-Hash eines Knotens = die ersten `hash_size` Bytes des
  `public_key` (Hex-Präfix). 2-Byte-Hashes sind ~99 % eindeutig, 1-Byte kollidieren stark — bei
  1-Byte über Geografie disambiguieren oder als ambig zählen (nicht raten).
- `path_json` ist die reale Hop-Kette eines Flood-Pakets; aufeinanderfolgende Hashes = reale Kante.
- Methodik & Limitierungen ehrlich dokumentiert in `MeshCore_Simulation_v3_Realdaten.md` und
  `../study/MeshCore_Routing_Study.md`.

> Hinweis: Das v3-Linkmodell ist geometrisch idealisiert (reales Gelände/Antennenhöhe fehlen) —
> **relative** Mechanismus-Vergleiche sind robuster als **absolute** Airtime-Zahlen.
