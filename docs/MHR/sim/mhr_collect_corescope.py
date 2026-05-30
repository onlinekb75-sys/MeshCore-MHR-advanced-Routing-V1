#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mhr_collect_corescope.py — Reproduzierbarer Collector fuer den CoreScope-Datensatz
==================================================================================

Holt den kompletten Live-Datensatz von CoreScope (corescope.meshrheinland.de), der
mhr_sim_real_v3.py als Eingabe dient:

  - /api/nodes      -> nodes.json      ({"nodes": [...]}, ~1962 Knoten)
  - /api/observers  -> observers.json  ({"observers": [...]}, ~38 Observer)
  - /api/packets    -> packets.jsonl   (offset-paginiert, 1 JSON je Zeile, ~110k Pakete)
  - /api/stats      -> stats.json

Methodik (wie das urspruengliche /tmp/cs_collect.py):
  * urllib mit Browser-User-Agent (CoreScope ist eine SPA, blockt nackte Clients).
  * ssl._create_unverified_context() (Self-Signed/Proxy-tolerant).
  * Offset-Pagination ueber /api/packets?limit=5000&offset=N bis eine Seite < limit
    zurueckkommt; Deduplizierung ueber p['id'].
  * Robustheit: 3 Wiederholungen je Seite, Pause zwischen Seiten (rate-limit-schonend).

ACHTUNG: packets.jsonl wird ~200 MB gross. NICHT ins Repo committen — nur die kompakten
Derivate aus mhr_sim_real_v3.py/data/ gehoeren ins Repo.

Aufruf:
  python3 mhr_collect_corescope.py [ZIELORDNER]      (Default: /tmp/cs_data)
"""

import ssl
import sys
import json
import time
import os
import urllib.request

BASE = "https://corescope.meshrheinland.de"
LIMIT = 5000
MAX_OFFSET = 2_000_000          # Sicherheitsobergrenze gegen Endlosschleife
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")

CTX = ssl._create_unverified_context()


def fetch_json(path, retries=3, timeout=120):
    """GET <BASE><path> -> dekodiertes JSON. Mit Browser-UA + unverified SSL + Retry."""
    url = BASE + path
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.load(r)
        except Exception as e:                          # noqa: BLE001
            last_err = e
            print(f"  Versuch {attempt+1}/{retries} fuer {path} fehlgeschlagen: {e}",
                  flush=True)
            time.sleep(3)
    raise RuntimeError(f"Aufgabe nach {retries} Versuchen: {path} ({last_err})")


def collect_simple(api_path, out_file, key):
    """Holt einen einfachen Endpoint (nodes/observers/stats) und speichert ihn."""
    print(f"-> {api_path}", flush=True)
    data = fetch_json(api_path)
    with open(out_file, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    n = len(data.get(key, [])) if key else "?"
    print(f"   gespeichert: {out_file} ({n} Eintraege)", flush=True)
    return data


def collect_packets(out_file):
    """Offset-paginiert /api/packets, dedupliziert ueber id, schreibt JSONL."""
    print("-> /api/packets (offset-Pagination)", flush=True)
    seen = set()
    total = 0
    with open(out_file, "w") as out:
        off = 0
        while off < MAX_OFFSET:
            d = fetch_json(f"/api/packets?limit={LIMIT}&offset={off}")
            pk = d.get("packets", [])
            new = 0
            for p in pk:
                pid = p.get("id")
                if pid in seen:
                    continue
                seen.add(pid)
                out.write(json.dumps(p, ensure_ascii=False) + "\n")
                total += 1
                new += 1
            print(f"   offset {off:7d}: +{new} neu (kumuliert {total}), "
                  f"api_total={d.get('total')}", flush=True)
            if len(pk) < LIMIT:
                print("   Ende erreicht (Seite < limit).", flush=True)
                break
            off += LIMIT
            time.sleep(0.5)        # schont das Backend
    print(f"   gespeichert: {out_file} ({total} eindeutige Pakete)", flush=True)
    return total


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cs_data"
    os.makedirs(out_dir, exist_ok=True)
    print(f"Zielordner: {out_dir}\n", flush=True)

    collect_simple("/api/nodes?limit=5000", os.path.join(out_dir, "nodes.json"), "nodes")
    collect_simple("/api/observers", os.path.join(out_dir, "observers.json"), "observers")
    try:
        collect_simple("/api/stats", os.path.join(out_dir, "stats.json"), None)
    except Exception as e:                              # noqa: BLE001
        print(f"   (stats optional, uebersprungen: {e})", flush=True)
    collect_packets(os.path.join(out_dir, "packets.jsonl"))

    print("\nFERTIG. Nun mhr_sim_real_v3.py ausfuehren (erwartet die Dateien in /tmp/cs_data).",
          flush=True)


if __name__ == "__main__":
    main()
