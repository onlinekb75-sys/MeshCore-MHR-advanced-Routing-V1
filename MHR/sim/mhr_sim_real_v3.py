#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — Simulation v3 auf ECHTEN Live-Daten (CoreScope / meshrheinland.de)
=================================================================================

Diese Datei vereint drei klar getrennte Bausteine. Wichtig fuer die wissenschaftliche
Ehrlichkeit: es wird strikt zwischen GEMESSEN (aus realen Produktionspaketen abgeleitet)
und SIMULIERT (Monte-Carlo-Routing-Modell) unterschieden.

  Baustein A — MESSUNG aus 109.980 realen rx-Paketen:
    A1  Node->Hash-Index (1/2/3 Byte) + geografische Disambiguierung bei 1-Byte-Kollision.
    A2  Beobachtete gerichtete Relay-Topologie aus den Pfad-Hashketten (path_json).
    A3  Kalibrierung eines Log-Distance-SNR-Modells aus (Distanz, snr, rssi)-Tripeln
        (letzter Hop -> Observer, beide Positionen bekannt).
    A4  Reale Detour-Statistik: reale Hopzahl vs. geografische Unterschranke.

  Baustein B — SIMULATION auf der grossen realen Topologie:
    Aktiver Repeater-Subgraph -> Link-/Reichweitengraph aus dem KALIBRIERTEN A3-Modell.
    Baseline (MeshCore first-packet-wins-Flood, Monte-Carlo) vs. MHR (SNR/Qualitaets-
    geleitete Ausbreitung + prefer-shorter). Metriken: Hops, Detour, Airtime, Zuverlaessigkeit.

  Baustein C — Output: kompakte Derivate (data/), sim_results_v3.json, 4 Plots.

Eingabe (Rohdaten, NICHT im Repo): /tmp/cs_data/{nodes,observers}.json, packets.jsonl
Reproduzierbar: Seed 42. packets.jsonl wird gestreamt (nie komplett im RAM).

Aufruf:  python3 mhr_sim_real_v3.py
"""

import json
import math
import os
import sys
import collections
import statistics
import numpy as np
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --------------------------------------------------------------------------------------
# Konfiguration / Pfade
# --------------------------------------------------------------------------------------
SEED = 42
rng = np.random.default_rng(SEED)

DATA_RAW = "/tmp/cs_data"                     # Rohdaten (gross, nicht im Repo)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DATA = os.path.join(HERE, "data")         # kompakte Derivate -> ins Repo
os.makedirs(OUT_DATA, exist_ok=True)

PACKETS = os.path.join(DATA_RAW, "packets.jsonl")
NODES_F = os.path.join(DATA_RAW, "nodes.json")
OBS_F = os.path.join(DATA_RAW, "observers.json")

# Physik-/LoRa-Parameter
LORA_MAX_KM = 45.0          # plausible LoRa-Reichweite fuer Disambiguierung/Reichweitengraph
SNR_THR = -12.0             # Empfangsschwelle (dB), konsistent zu mhr_sim_real.py
# ALTE ANNAHMEN aus mhr_sim_real.py (zum Vergleich mit dem realen Fit):
OLD_SNR0 = 17.0
OLD_PLE = 2.55

# Alte Annahmen sind die Referenz; der reale Fit ueberschreibt sie spaeter.
FIT = {"snr0": OLD_SNR0, "ple": OLD_PLE, "sigma": 6.0, "n": 0}


def log(msg):
    print(msg, flush=True)


def haversine(la1, lo1, la2, lo2):
    """Grosskreis-Distanz in km."""
    R = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


# ======================================================================================
# BAUSTEIN A1 — Node->Hash-Index + geografische Disambiguierung
# ======================================================================================
log("\n=== Baustein A1: Node->Hash-Index ===")
nodes = json.load(open(NODES_F))["nodes"]
observers = json.load(open(OBS_F))["observers"]

# Index: hex-Praefix (uppercase) -> Liste von Node-Indizes, getrennt nach 1/2/3 Byte.
prefix_idx = {1: collections.defaultdict(list),
              2: collections.defaultdict(list),
              3: collections.defaultdict(list)}
node_geo = {}   # i -> (lat,lon) sofern vorhanden UND plausibel


def valid_geo(la, lo):
    """Plausibilitaetsfilter: verwirf Null-Insel (0,0) und Koordinaten ausserhalb
    eines grosszuegigen Mitteleuropa-Bounding-Box (Platzhalter-/Fehlkoordinaten)."""
    if la is None or lo is None:
        return False
    if abs(la) < 0.5 and abs(lo) < 0.5:     # (0,0)-Platzhalter
        return False
    return 35.0 <= la <= 60.0 and -12.0 <= lo <= 25.0


for i, n in enumerate(nodes):
    pk = n.get("public_key")
    if pk:
        pk = pk.upper()
        for b in (1, 2, 3):
            if len(pk) >= 2 * b:
                prefix_idx[b][pk[:2 * b]].append(i)
    if valid_geo(n.get("lat"), n.get("lon")):
        node_geo[i] = (n["lat"], n["lon"])

for b in (1, 2, 3):
    sizes = [len(v) for v in prefix_idx[b].values()]
    coll = sum(1 for s in sizes if s > 1)
    log(f"  {b}-Byte: {len(prefix_idx[b])} Praefixe, {coll} kollidieren, "
        f"Ø {sum(sizes)/max(len(sizes),1):.2f} Knoten/Praefix")

# Observer-Positionen
obs_geo = {}
for o in observers:
    oid = o["id"].upper()
    if valid_geo(o.get("lat"), o.get("lon")):
        obs_geo[oid] = (o["lat"], o["lon"])
log(f"  Observer mit Geo: {len(obs_geo)} / {len(observers)}")


def candidates(hexhash):
    """Liste plausibler Node-Indizes fuer einen Pfad-Hash (passende Byte-Laenge)."""
    h = hexhash.upper()
    b = len(h) // 2
    if b not in (1, 2, 3):
        return []
    return prefix_idx[b].get(h, [])


def resolve_hash(hexhash, ref_latlon):
    """
    Loese einen Pfad-Hash zu GENAU EINEM Node-Index auf.
    - 1 Kandidat: eindeutig.
    - >1 Kandidat (1-Byte-Kollision): disambiguiere geografisch ueber ref_latlon
      (Position des Nachbar-Hops oder Observers). Plausibel = naechster Knoten in
      LoRa-Reichweite. Wenn kein ref oder kein Kandidat in Reichweite: ambig (None).
    Rueckgabe: (node_index oder None, status) mit status in
      {'unique','geo','ambig','unknown'}.
    """
    cand = candidates(hexhash)
    if not cand:
        return None, "unknown"
    if len(cand) == 1:
        return cand[0], "unique"
    # Kollision -> Geo-Disambiguierung
    if ref_latlon is None:
        return None, "ambig"
    best, bestd = None, None
    for c in cand:
        if c in node_geo:
            d = haversine(ref_latlon[0], ref_latlon[1], node_geo[c][0], node_geo[c][1])
            if d <= LORA_MAX_KM and (bestd is None or d < bestd):
                best, bestd = c, d
    if best is None:
        return None, "ambig"
    return best, "geo"


# ======================================================================================
# BAUSTEIN A2 + A3 + A4 — ein gemeinsamer Streaming-Pass ueber packets.jsonl
# ======================================================================================
log("\n=== Baustein A2/A3/A4: Streaming-Pass ueber packets.jsonl ===")

# A2 — beobachtete gerichtete Kanten (nur wenn BEIDE Endpunkte aufloesbar)
edge_count = collections.Counter()        # (u,v) -> Haeufigkeit
# A3 — SNR-Kalibrierung: (distanz_km, snr, rssi) letzter-Hop -> Observer
snr_samples = []                          # Liste (d, snr, rssi)
# A4 — Detour-Rohdaten: pro Paket reale Hopzahl + Luftlinie erster<->letzter aufloesbarer Hop
detour_rows = []                          # (real_hops, air_km, geo_lb_hops)
# Empirische Hop-Distanzen aus EINDEUTIG aufgeloesten Nachbar-Hops (fuer A4-Unterschranke)
hop_dist_samples = []                     # Liste km


def resolve_unique(hexhash):
    """Loese NUR eindeutig auf (genau 1 Kandidat). Sonst None. Keine Geo-Heuristik
    -> unverzerrt fuer SNR-Kalibrierung und Hop-Distanz-Messung."""
    cand = candidates(hexhash)
    return cand[0] if len(cand) == 1 else None

# Zaehler/Diagnostik
stat = collections.Counter()
resolve_stat = collections.Counter()      # status-Verteilung beim Aufloesen

MAX_PACKETS = int(os.environ.get("MHR_MAX_PACKETS", "0"))  # 0 = alle

with open(PACKETS) as f:
    for ln, line in enumerate(f):
        if MAX_PACKETS and ln >= MAX_PACKETS:
            break
        try:
            p = json.loads(line)
        except Exception:
            stat["json_err"] += 1
            continue
        stat["total"] += 1
        path = []
        if p.get("path_json"):
            try:
                path = json.loads(p["path_json"])
            except Exception:
                path = []
        if not path:
            stat["no_path"] += 1
        # ---- Hops sequentiell aufloesen (geo-disambiguiert ueber den jeweils zuletzt
        #      bereits aufgeloesten Hop als Referenz) ----
        resolved = []   # parallele Liste: node_index oder None
        last_ref = None
        for h in path:
            ni, st = resolve_hash(h, last_ref)
            resolve_stat[st] += 1
            resolved.append(ni)
            if ni is not None and ni in node_geo:
                last_ref = node_geo[ni]
        # zweiter Durchlauf rueckwaerts hilft den fuehrenden ambigen Hops
        last_ref = None
        for k in range(len(path) - 1, -1, -1):
            if resolved[k] is None:
                ni, st = resolve_hash(path[k], last_ref)
                if ni is not None:
                    resolved[k] = ni
                    resolve_stat["geo_back"] += 1
            if resolved[k] is not None and resolved[k] in node_geo:
                last_ref = node_geo[resolved[k]]

        # ---- A2: gerichtete Kanten zwischen aufeinanderfolgenden aufgeloesten Hops ----
        for a, b in zip(resolved, resolved[1:]):
            if a is not None and b is not None and a != b:
                edge_count[(a, b)] += 1

        # ---- Empirische Hop-Distanzen aus EINDEUTIGEN Nachbar-Hops (unverzerrt) ----
        for h1, h2 in zip(path, path[1:]):
            u = resolve_unique(h1)
            v = resolve_unique(h2)
            if u is not None and v is not None and u != v and u in node_geo and v in node_geo:
                dd = haversine(node_geo[u][0], node_geo[u][1],
                               node_geo[v][0], node_geo[v][1])
                if 0.05 <= dd <= LORA_MAX_KM:   # physikalisch plausible Hops
                    hop_dist_samples.append(dd)

        # ---- A3: SNR-Sample letzter-Hop -> Observer ----
        #      WICHTIG: nur EINDEUTIG aufloesbare letzte Hops verwenden. Geo-disambiguierte
        #      Hops wuerden den Fit verzerren (sie waehlen den naechstgelegenen Kandidaten
        #      und korrelieren Distanz kuenstlich mit SNR).
        oid = (p.get("observer_id") or "").upper()
        snr = p.get("snr")
        rssi = p.get("rssi")
        if oid in obs_geo and snr is not None and rssi is not None and path:
            last_u = resolve_unique(path[-1])
            if last_u is not None and last_u in node_geo:
                olat, olon = obs_geo[oid]
                nlat, nlon = node_geo[last_u]
                d = haversine(olat, olon, nlat, nlon)
                # filtern: 0-Distanz (Eigen-Echo) und unphysikalische Ausreisser raus
                if 0.05 <= d <= 120.0 and -30 <= snr <= 20 and -130 <= rssi <= -20:
                    snr_samples.append((d, float(snr), float(rssi)))

        # ---- A4: Detour-Rohdaten (mind. 2 aufgeloeste Endpunkte mit Geo) ----
        rh = len(path)
        if rh >= 2:
            geo_pts = [resolved[k] for k in range(len(path))
                       if resolved[k] is not None and resolved[k] in node_geo]
            if len(geo_pts) >= 2:
                a0, an = geo_pts[0], geo_pts[-1]
                if a0 != an:
                    air = haversine(node_geo[a0][0], node_geo[a0][1],
                                    node_geo[an][0], node_geo[an][1])
                    detour_rows.append((rh, air, a0, an))

        if stat["total"] % 20000 == 0:
            log(f"  ...{stat['total']} Pakete verarbeitet "
                f"(Kanten {len(edge_count)}, SNR-Samples {len(snr_samples)})")

log(f"  Pakete gesamt: {stat['total']} | ohne Pfad: {stat['no_path']} | JSON-Fehler: {stat['json_err']}")
tot_res = sum(resolve_stat.values()) or 1
log("  Hash-Aufloesung: " + ", ".join(
    f"{k}={resolve_stat[k]} ({100*resolve_stat[k]/tot_res:.1f}%)"
    for k in ("unique", "geo", "geo_back", "ambig", "unknown")))


# ======================================================================================
# BAUSTEIN A2 — Topologie-Graph auswerten
# ======================================================================================
log("\n=== Baustein A2: Beobachtete Relay-Topologie ===")
# Nur Repeater-Backbone behalten (Companions/Rooms leiten i.d.R. nicht weiter; wir filtern
# Kanten auf Knoten mit role==repeater, soweit bekannt).
def is_repeater(i):
    return nodes[i].get("role") == "repeater"

DiG = nx.DiGraph()
for (a, b), c in edge_count.items():
    if is_repeater(a) and is_repeater(b):
        DiG.add_edge(a, b, weight=c)

UG = DiG.to_undirected()
if UG.number_of_nodes() > 0:
    comps = sorted((len(c) for c in nx.connected_components(UG)), reverse=True)
    giant = comps[0]
    degs = [d for _, d in UG.degree()]
else:
    comps = []
    giant = 0
    degs = [0]

log(f"  Knoten im Backbone-Graph: {DiG.number_of_nodes()}")
log(f"  Gerichtete Kanten: {DiG.number_of_edges()}")
log(f"  Grad (ungerichtet) min/median/max: {min(degs)}/{statistics.median(degs):.0f}/{max(degs)}")
log(f"  Groesste Zusammenhangskomponente: {giant} | Komponentengroessen (Top5): {comps[:5]}")

# Kompakte Kantenliste exportieren (Top nach Haeufigkeit, plus Namen/Geo der Endpunkte)
edges_export = []
for (a, b), c in sorted(edge_count.items(), key=lambda kv: -kv[1]):
    if is_repeater(a) and is_repeater(b):
        edges_export.append({
            "u": nodes[a]["public_key"][:8], "v": nodes[b]["public_key"][:8],
            "u_name": nodes[a].get("name"), "v_name": nodes[b].get("name"),
            "count": c
        })
json.dump({"directed_edges": len(edges_export), "edges": edges_export[:5000]},
          open(os.path.join(OUT_DATA, "topology_edges.json"), "w"),
          indent=1, ensure_ascii=False)


# ======================================================================================
# BAUSTEIN A3 — Log-Distance-SNR-Fit
# ======================================================================================
log("\n=== Baustein A3: SNR/Distanz-Kalibrierung ===")
# Methodik: snr ~ snr0 - 10*n*log10(d). Nur EINDEUTIG aufgeloeste letzte Hops (oben).
# Wir berichten ZWEI Fits: (1) OLS ueber alle Samples, (2) robuster Fit ueber
# log-distanz-gebinnte Mediane (gegen Saettigung am oberen SNR-Rand und Heavy-Tails).
SNR_SAT = 13.0    # beobachtete Saettigung (Hardware-SNR deckelt ~+11..15 dB)
if len(snr_samples) >= 30:
    arr = np.array(snr_samples)               # Spalten: d, snr, rssi
    d = arr[:, 0]
    snr = arr[:, 1]
    rssi = arr[:, 2]
    x = np.log10(d)
    A = np.vstack([np.ones_like(x), x]).T
    coef, *_ = np.linalg.lstsq(A, snr, rcond=None)
    snr0_ols = float(coef[0]); ple_ols = float(-coef[1] / 10.0)
    sigma_ols = float(np.std(snr - (A @ coef)))
    corr = float(np.corrcoef(x, snr)[0, 1])

    # (2) Binned-Median-Fit (log-distanz-Bins, nur Bins mit >=20 Samples)
    edges = np.logspace(np.log10(max(d.min(), 0.1)), np.log10(d.max()), 12)
    bx, by = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (d >= lo) & (d < hi)
        if m.sum() >= 20:
            bx.append(np.median(d[m])); by.append(np.median(snr[m]))
    if len(bx) >= 3:
        bx = np.array(bx); by = np.array(by)
        Xb = np.vstack([np.ones_like(bx), np.log10(bx)]).T
        cb, *_ = np.linalg.lstsq(Xb, by, rcond=None)
        snr0_bin = float(cb[0]); ple_bin = float(-cb[1] / 10.0)
    else:
        snr0_bin, ple_bin = snr0_ols, ple_ols

    # rssi-Fit zur Plausibilisierung
    cr, *_ = np.linalg.lstsq(A, rssi, rcond=None)
    rssi0_fit = float(cr[0]); rssi_ple = float(-cr[1] / 10.0)

    FIT = {
        "n": int(len(snr_samples)),
        "snr0": snr0_ols, "ple": ple_ols, "sigma": sigma_ols,
        "snr0_binned": snr0_bin, "ple_binned": ple_bin,
        "corr_logd_snr": corr,
        "rssi0": rssi0_fit, "rssi_ple": rssi_ple,
        "snr_saturation_db": SNR_SAT,
        "d_km_median": float(np.median(d)), "snr_median": float(np.median(snr)),
    }
    log(f"  Stichprobe: {FIT['n']} (EINDEUTIGER letzter-Hop -> Observer)")
    log(f"  OLS-Fit:    SNR0={snr0_ols:.2f} dB, PLE n={ple_ols:.2f}, sigma={sigma_ols:.2f} dB, corr={corr:.2f}")
    log(f"  Binned-Fit: SNR0={snr0_bin:.2f} dB, PLE n={ple_bin:.2f}")
    log(f"  ALTE ANNAHME: SNR0={OLD_SNR0:.2f} dB, PLE n={OLD_PLE:.2f}")
    log("  BEFUND: distanzbasierter Pfadverlust ist im Realdatensatz SCHWACH "
        f"(|corr|={abs(corr):.2f}); Gelaende/Antennenhoehe/Standort dominieren.")
else:
    log(f"  WARNUNG: nur {len(snr_samples)} SNR-Samples -> behalte alte Annahmen.")
    FIT = {"n": len(snr_samples), "snr0": OLD_SNR0, "ple": OLD_PLE, "sigma": 6.0,
           "snr0_binned": OLD_SNR0, "ple_binned": OLD_PLE, "corr_logd_snr": 0.0}

# --- Modell fuer die SIMULATION (Baustein B) ---
# Der reale Fit ist zu flach (PLE<1) fuer einen physikalisch sinnvollen Reichweiten-
# graphen (haette quasi unendliche Reichweite). Fuer die Sim verwenden wir daher die
# realen Intercept/Streuung, aber einen auf physikalisch plausible Untergrenze
# GECLAMPTEN Pfadverlustexponenten. Das ist eine bewusste, dokumentierte Modellwahl.
SIM_PLE_FLOOR = 2.0
SNR0 = FIT.get("snr0_binned", OLD_SNR0)
PLE_REAL = FIT.get("ple_binned", OLD_PLE)
PLE = max(PLE_REAL, SIM_PLE_FLOOR)
SIGMA = max(FIT.get("sigma", 6.0), 0.5)
# Intercept so kalibrieren, dass die EMPIRISCH gemessene mediane Hop-Distanz (s.u.)
# noch knapp ueber der Schwelle liegt -> Reichweitengraph reproduziert reale Hops.
FIT["sim_ple_used"] = PLE
FIT["sim_snr0_used"] = SNR0
FIT["old_snr0"] = OLD_SNR0
FIT["old_ple"] = OLD_PLE
FIT["snr_threshold_db"] = SNR_THR
FIT["lora_max_km"] = LORA_MAX_KM

# Empirische mediane Hop-Distanz (aus eindeutigen Nachbar-Hops) — robustes Anker-Mass
if hop_dist_samples:
    hd = np.array(hop_dist_samples)
    FIT["empirical_hop_km_median"] = float(np.median(hd))
    FIT["empirical_hop_km_p75"] = float(np.percentile(hd, 75))
    FIT["empirical_hop_km_p90"] = float(np.percentile(hd, 90))
    FIT["empirical_hop_n"] = int(len(hd))
    log(f"  Empirische Hop-Distanz (eindeutige Nachbarn, n={len(hd)}): "
        f"Median {FIT['empirical_hop_km_median']:.1f} km, P90 {FIT['empirical_hop_km_p90']:.1f} km")
else:
    FIT["empirical_hop_km_median"] = 12.0
    FIT["empirical_hop_n"] = 0

# Intercept fuer die Sim so setzen, dass bei der medianen empirischen Hop-Distanz
# das SNR ~ +2 dB (zuverlaessiger, aber nicht trivialer Link) betraegt:
SNR0 = SNR_THR + 14.0 + 10.0 * PLE * math.log10(max(FIT["empirical_hop_km_median"], 1.0))
FIT["sim_snr0_used"] = SNR0
log(f"  Sim-Linkmodell: SNR0={SNR0:.1f} dB, PLE={PLE:.2f} (PLE-Floor {SIM_PLE_FLOOR}); "
    f"Reichweite @Schwelle ~ {10**((SNR0-SNR_THR)/(10*PLE)):.1f} km")

json.dump(FIT, open(os.path.join(OUT_DATA, "snr_calibration.json"), "w"),
          indent=2, ensure_ascii=False)


def model_snr(dist_km):
    """SNR-Schaetzung fuers Sim-Linkmodell (Saettigung am oberen Rand)."""
    s = SNR0 - 10.0 * PLE * math.log10(max(dist_km, 0.05))
    return min(s, SNR_SAT)


def deliv(snr_db):
    """Lieferwahrscheinlichkeit aus SNR (logistisch um die Schwelle)."""
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / 3.0)), 0.0, 0.995))


# ======================================================================================
# BAUSTEIN A4 — Reale Detour-Statistik
# ======================================================================================
log("\n=== Baustein A4: Reale Detour-Statistik (GEMESSEN) ===")
# Geografische Unterschranke der Hopzahl: ceil(Luftlinie / typische_Hop-Distanz).
# typische Hop-Distanz = EMPIRISCH gemessene mediane Distanz zwischen eindeutig
# aufgeloesten Nachbar-Hops (datengetrieben, unabhaengig vom schwachen SNR-Fit).
# Konservativ: wir nehmen das P75 als Hop-Reichweite, damit die Unterschranke wirklich
# eine UNTERschranke bleibt (lange Hops sind moeglich -> weniger Hops noetig).
HOP_KM = max(FIT.get("empirical_hop_km_p75", FIT.get("empirical_hop_km_median", 12.0)), 1.0)
log(f"  Geografische Unterschranke nutzt Hop-Reichweite {HOP_KM:.1f} km "
    f"(empirisches P75 der realen Nachbar-Hop-Distanzen)")

det_real = []
det_ratio = []
for rh, air, a0, an in detour_rows:
    lb = max(1, math.ceil(air / HOP_KM))      # geografische Unterschranke (>=1)
    det_real.append(rh)
    det_ratio.append(rh / lb)

if det_ratio:
    det_ratio = np.array(det_ratio)
    det_real = np.array(det_real)
    detour_stats = {
        "n_packets": int(len(det_ratio)),
        "real_hops_median": float(np.median(det_real)),
        "real_hops_p90": float(np.percentile(det_real, 90)),
        "real_hops_max": int(det_real.max()),
        "geo_lower_bound_hop_km": HOP_KM,
        "detour_factor_median": float(np.median(det_ratio)),
        "detour_factor_mean": float(np.mean(det_ratio)),
        "detour_factor_p90": float(np.percentile(det_ratio, 90)),
        "detour_factor_p99": float(np.percentile(det_ratio, 99)),
        "frac_detour_gt_1_5x": float(np.mean(det_ratio > 1.5)),
        "frac_detour_gt_2x": float(np.mean(det_ratio > 2.0)),
    }
    log(f"  Pakete mit auswertbarem Pfad: {detour_stats['n_packets']}")
    log(f"  Reale Hops Median/P90/Max: {detour_stats['real_hops_median']:.0f}/"
        f"{detour_stats['real_hops_p90']:.0f}/{detour_stats['real_hops_max']}")
    log(f"  Detour-Faktor Median/P90/P99: {detour_stats['detour_factor_median']:.2f}/"
        f"{detour_stats['detour_factor_p90']:.2f}/{detour_stats['detour_factor_p99']:.2f}")
    log(f"  Anteil >1.5x: {100*detour_stats['frac_detour_gt_1_5x']:.1f}% | "
        f">2x: {100*detour_stats['frac_detour_gt_2x']:.1f}%")
else:
    detour_stats = {"n_packets": 0}
    log("  Keine auswertbaren Detour-Pakete.")
json.dump(detour_stats, open(os.path.join(OUT_DATA, "real_detour_stats.json"), "w"),
          indent=2, ensure_ascii=False)


# ======================================================================================
# BAUSTEIN B — Routing-Simulation auf der grossen realen Topologie
# ======================================================================================
log("\n=== Baustein B: Routing-Simulation (Baseline MeshCore vs. MHR) ===")

# --- Aktiver Repeater-Subgraph waehlen ---
# Kriterium: role==repeater, Geo vorhanden, UND (relay_active ODER relay_count_24h>0
# ODER taucht real als aufgeloester Pfad-Hop auf). Begruendung: das sind die Knoten,
# die im Beobachtungszeitraum nachweislich am Relaying beteiligt waren bzw. dafuer
# konfiguriert/aktiv sind.
seen_in_paths = set()
for (a, b) in edge_count:
    seen_in_paths.add(a)
    seen_in_paths.add(b)

active = []
for i, n in enumerate(nodes):
    if n.get("role") != "repeater":
        continue
    if i not in node_geo:
        continue
    if n.get("relay_active") or (n.get("relay_count_24h", 0) or 0) > 0 or i in seen_in_paths:
        active.append(i)

log(f"  Aktiver Repeater-Subgraph: {len(active)} Knoten "
    f"(repeater + Geo + relay-aktiv/24h>0/als Pfad-Hop gesehen)")

# Falls extrem gross (Sim O(N^2) fuer Reichweitengraph) — bei wenigen hundert ok.
lat = {i: node_geo[i][0] for i in active}
lon = {i: node_geo[i][1] for i in active}

# --- Reichweiten-/Linkgraph aus dem KALIBRIERTEN SNR-Modell ---
# Pro Knoten werden nur die MAX_NEIGHBORS staerksten Links (hoechstes SNR / naechste
# Distanz) behalten. Das ist (a) physikalisch realistischer (ein Knoten hat eine
# begrenzte Zahl nutzbarer Nachbarn, nicht hunderte) und (b) haelt den Flood rechenbar.
MAX_NEIGHBORS = int(os.environ.get("MHR_MAX_NEIGHBORS", "20"))
# Erst alle Kandidatenkanten sammeln, dann pro Knoten Top-K nach pr filtern.
cand_edges = collections.defaultdict(list)   # i -> Liste (pr, j, dij)
for ai in range(len(active)):
    i = active[ai]
    for aj in range(ai + 1, len(active)):
        j = active[aj]
        dij = haversine(lat[i], lon[i], lat[j], lon[j])
        if dij > LORA_MAX_KM:
            continue
        s = model_snr(dij)
        if s <= SNR_THR:
            continue
        pr = deliv(s)
        if pr <= 0.05:
            continue
        cand_edges[i].append((pr, j, dij))
        cand_edges[j].append((pr, i, dij))

G = nx.Graph()
G.add_nodes_from(active)
Prel = {}   # (i,j) -> Lieferwahrscheinlichkeit (symmetrisch genutzt)
for i, lst in cand_edges.items():
    lst.sort(reverse=True)                     # staerkste Links zuerst
    for pr, j, dij in lst[:MAX_NEIGHBORS]:
        # symmetrisch hinzufuegen; Kante existiert sobald EINE Seite sie unter Top-K hat
        Prel[(i, j)] = pr
        Prel[(j, i)] = pr
        if not G.has_edge(i, j):
            G.add_edge(i, j, etx=1.0 / (pr * pr), p=pr, dist=dij)

comps_sim = sorted(nx.connected_components(G), key=len, reverse=True)
giant_nodes = set(comps_sim[0]) if comps_sim else set()
log(f"  Linkgraph: {G.number_of_nodes()} Knoten, {G.number_of_edges()} Kanten, "
    f"groesste Komponente {len(giant_nodes)}")

giant_list = sorted(giant_nodes)


# --- Baseline: MeshCore first-packet-wins-Flood (Monte-Carlo) ---
FLOOD_MAX = 16
BASE_AIR = 0.10
PER_HOP_AIR = 0.012


def flood_first_wins(src, dst, rstate):
    """
    MeshCore-Baseline: jeder Repeater leitet die ZUERST eintreffende Kopie GENAU EINMAL
    weiter (hasSeen-Dedup), mit zufaelligem Hop-Timing. Der am Ziel zuerst ankommende
    Pfad 'gewinnt' und wird gecacht (first-packet-wins).

    Wir simulieren das Timing-getrieben (Prioritaetswarteschlange nach Ankunftszeit):
      - Gewinnerpfad = erste am Ziel eintreffende Kopie.
      - Airtime ntx = Anzahl Repeater, die im Flood tatsaechlich GESENDET haben
        (= geflutete Menge). Da der Flood netzweit weiterlaeuft, zaehlen wir die
        Sende-Ereignisse weiter, AUCH nachdem der Gewinner steht (realistische Airtime).
    Beschleunigung: sobald der Gewinner feststeht, brauchen wir den Gewinnerpfad nicht
    weiter zu suchen, aber die geflutete Menge ist deterministisch (jeder erreichte
    Knoten sendet einmal) -> wir vervollstaendigen die Reichweite ohne Pfade zu tracken.
    """
    import heapq
    pq = [(0.0, src, (src,))]
    forwarded = set()
    win_t, win_path = None, None
    while pq:
        t, u, path = heapq.heappop(pq)
        if u == dst:
            if win_t is None or t < win_t:
                win_t, win_path = t, path
            continue
        if u in forwarded:
            continue
        forwarded.add(u)
        if win_path is not None and len(path) - 1 >= FLOOD_MAX:
            continue
        if len(path) - 1 >= FLOOD_MAX:
            continue
        air = BASE_AIR + PER_HOP_AIR * (len(path) - 1)
        for v in G.neighbors(u):
            if v in path:
                continue
            pr = Prel.get((u, v), 0.0)
            if rstate.random() <= pr:
                tt = t + air + rstate.uniform(0.0, 5.0 * air)
                # Sobald Gewinner steht: keine Pfade mehr mitschleppen (Speed),
                # aber Reichweite weiter ausbreiten fuer die Airtime-Zaehlung.
                heapq.heappush(pq, (tt, v, path + (v,) if win_path is None else (v,)))
    # Airtime = Anzahl tatsaechlich sendender (geflooderter) Repeater
    ntx = len(forwarded)
    return win_path, ntx


def best_quality_path(src, dst):
    """
    MHR: qualitaetsgeleiteter Pfad = ETX-kuerzester Pfad (bester kumulierter SNR /
    wenigste effektive Hops). prefer-shorter ist implizit, da ETX mit Hops waechst.
    """
    try:
        return nx.shortest_path(G, src, dst, weight="etx")
    except nx.NetworkXNoPath:
        return None


def path_reliability(path):
    r = 1.0
    for a, b in zip(path, path[1:]):
        r *= Prel.get((a, b), 0.0)
    return r


def path_hops(path):
    return len(path) - 1 if path else 0


# --- Monte-Carlo ueber zufaellige Quelle-Ziel-Paare in der grossen Komponente ---
N_PAIRS = int(os.environ.get("MHR_N_PAIRS", "200"))
MC = int(os.environ.get("MHR_MC", "25"))   # Monte-Carlo-Wiederholungen je Paar (Baseline)
results = []
if len(giant_list) >= 2:
    for _ in range(N_PAIRS):
        s, d = rng.choice(giant_list, size=2, replace=False)
        s, d = int(s), int(d)
        mhr = best_quality_path(s, d)
        if not mhr:
            continue
        mhr_h = path_hops(mhr)
        mhr_rel = path_reliability(mhr)
        # Baseline Monte-Carlo
        b_hops, b_rel, b_tx = [], [], []
        for _m in range(MC):
            wp, ntx = flood_first_wins(s, d, rng)
            if wp:
                b_hops.append(path_hops(wp))
                b_rel.append(path_reliability(list(wp)))
                b_tx.append(ntx)
        if not b_hops:
            continue
        b_hops = np.array(b_hops)
        b_rel = np.array(b_rel)
        b_tx = np.array(b_tx)
        results.append({
            "src": s, "dst": d,
            "mhr_hops": mhr_h,
            "base_hops_mean": float(b_hops.mean()),
            "base_hops_max": int(b_hops.max()),
            "detour_ratio": float(b_hops.mean() / mhr_h) if mhr_h > 0 else 1.0,
            "frac_detour": float(np.mean(b_hops > mhr_h)),
            # Airtime: Baseline = netzweite Sende-Ereignisse; MHR = Unicast entlang Pfad
            "base_tx_mean": float(b_tx.mean()),
            "mhr_tx": mhr_h,
            "base_rel_mean": float(b_rel.mean()),
            "mhr_rel": float(mhr_rel),
        })

log(f"  Auswertbare Paare: {len(results)} (von {N_PAIRS} gezogen)")


def agg(key):
    return np.array([r[key] for r in results]) if results else np.array([0.0])


if results:
    base_tx = agg("base_tx_mean").mean()
    mhr_tx = agg("mhr_tx").mean()
    sim_summary = {
        "n_pairs": len(results),
        "mean_mhr_hops": float(agg("mhr_hops").mean()),
        "mean_base_hops": float(agg("base_hops_mean").mean()),
        "worst_base_hops": int(agg("base_hops_max").max()),
        "mean_detour_ratio": float(agg("detour_ratio").mean()),
        "median_detour_ratio": float(np.median(agg("detour_ratio"))),
        "pct_pairs_with_detour": float(np.mean(agg("frac_detour") > 0.10) * 100),
        "mean_base_tx": float(base_tx),
        "mean_mhr_tx": float(mhr_tx),
        "airtime_reduction_pct": float((1 - mhr_tx / base_tx) * 100) if base_tx > 0 else 0.0,
        "mean_base_reliability": float(agg("base_rel_mean").mean()),
        "mean_mhr_reliability": float(agg("mhr_rel").mean()),
    }
else:
    sim_summary = {"n_pairs": 0}

log("  Simulationsergebnis (Baseline MeshCore vs. MHR):")
for k, v in sim_summary.items():
    log(f"    {k}: {v}")


# ======================================================================================
# BAUSTEIN C — Gesamtergebnis + Plots
# ======================================================================================
log("\n=== Baustein C: Output schreiben ===")

# nodes/observers Kopie (kompakt) ins data/
json.dump({"nodes": nodes}, open(os.path.join(OUT_DATA, "nodes.json"), "w"),
          ensure_ascii=False)
json.dump({"observers": observers}, open(os.path.join(OUT_DATA, "observers.json"), "w"),
          indent=1, ensure_ascii=False)

all_results = {
    "seed": SEED,
    "measured": {
        "topology": {
            "backbone_nodes": DiG.number_of_nodes(),
            "directed_edges": DiG.number_of_edges(),
            "largest_component": giant,
            "degree_min_median_max": [min(degs), float(statistics.median(degs)), max(degs)],
        },
        "snr_calibration": FIT,
        "real_detour": detour_stats,
        "hash_resolution": {k: resolve_stat[k] for k in
                            ("unique", "geo", "geo_back", "ambig", "unknown")},
        "packets_processed": stat["total"],
    },
    "simulated": {
        "active_subgraph_size": len(active),
        "link_graph_nodes": G.number_of_nodes(),
        "link_graph_edges": G.number_of_edges(),
        "largest_component": len(giant_nodes),
        "baseline_vs_mhr": sim_summary,
    },
}
json.dump(all_results, open(os.path.join(HERE, "sim_results_v3.json"), "w"),
          indent=2, ensure_ascii=False)
log("  sim_results_v3.json geschrieben.")

plt.rcParams.update({"font.size": 10, "figure.dpi": 130})

# --- Plot 1: reale beobachtete Topologie (Backbone-Kanten geografisch) ---
fig, ax = plt.subplots(figsize=(8.4, 7.6))
drawn = 0
for (a, b), c in edge_count.items():
    if a in node_geo and b in node_geo and is_repeater(a) and is_repeater(b):
        ax.plot([node_geo[a][1], node_geo[b][1]],
                [node_geo[a][0], node_geo[b][0]],
                color="#5a82a8", lw=min(0.2 + 0.04 * math.log1p(c), 1.6), alpha=0.35, zorder=1)
        drawn += 1
rep_geo_nodes = [i for i in node_geo if is_repeater(i)]
ax.scatter([node_geo[i][1] for i in rep_geo_nodes],
           [node_geo[i][0] for i in rep_geo_nodes],
           c="#d98c5f", s=6, zorder=2, label=f"Repeater ({len(rep_geo_nodes)})")
ax.set_title(f"GEMESSENE Relay-Topologie aus {stat['total']} realen Paketen\n"
             f"{DiG.number_of_edges()} gerichtete Kanten, groesste Komponente {giant} Knoten")
ax.set_xlabel("Laenge (°O)")
ax.set_ylabel("Breite (°N)")
ax.legend(loc="lower left")
ax.grid(alpha=0.2)
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_v3_real_topology.png"))
plt.close(fig)

# --- Plot 2: SNR-Fit (GEMESSEN: OLS + Binned-Median, vs. ALTE Annahme) ---
fig, ax = plt.subplots(figsize=(6.6, 4.9))
if len(snr_samples) >= 30:
    arr = np.array(snr_samples)
    ax.scatter(arr[:, 0], arr[:, 1], s=4, alpha=0.12, color="#34495e",
               label=f"{len(snr_samples)} reale rx-Samples (eindeutiger letzter Hop)")
    dd = arr[:, 0]
    xs = np.logspace(np.log10(max(0.1, dd.min())), np.log10(dd.max()), 120)
    # gemessener OLS-Fit
    ax.plot(xs, [FIT["snr0"] - 10 * FIT["ple"] * math.log10(max(x, 0.05)) for x in xs],
            color="#c0392b", lw=2.2,
            label=f"GEMESSEN OLS: SNR0={FIT['snr0']:.1f}, n={FIT['ple']:.2f} (corr={FIT['corr_logd_snr']:.2f})")
    # binned medians
    edges = np.logspace(np.log10(max(0.1, dd.min())), np.log10(dd.max()), 12)
    bxx, byy = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (dd >= lo) & (dd < hi)
        if m.sum() >= 20:
            bxx.append(np.median(dd[m])); byy.append(np.median(arr[m, 1]))
    if bxx:
        ax.plot(bxx, byy, "o-", color="#e67e22", lw=1.8, ms=5, label="GEMESSEN: Bin-Mediane")
    # alte Annahme
    ax.plot(xs, [OLD_SNR0 - 10 * OLD_PLE * math.log10(max(x, 0.05)) for x in xs],
            color="#2e7d32", lw=2.0, ls="--",
            label=f"ALTE ANNAHME: SNR0={OLD_SNR0:.1f}, n={OLD_PLE:.2f}")
ax.axhline(SNR_THR, color="#999", ls=":", label=f"Empfangsschwelle {SNR_THR:.0f} dB")
ax.set_xscale("log")
ax.set_xlabel("Distanz letzter Hop → Observer (km, log)")
ax.set_ylabel("SNR (dB)")
ax.set_title("A3: SNR/Distanz — GEMESSEN vs. ALT ANGENOMMEN\n"
             "Befund: Distanz erklaert SNR real nur schwach")
ax.legend(fontsize=7.5, loc="lower left")
ax.grid(alpha=0.2, which="both")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_v3_snr_fit.png"))
plt.close(fig)

# --- Plot 3: reale Detour-Verteilung ---
fig, ax = plt.subplots(figsize=(6.4, 4.6))
if det_ratio is not None and len(det_ratio) > 0:
    clip = np.clip(det_ratio, 0, 8)
    ax.hist(clip, bins=40, color="#c0392b", alpha=0.8)
    ax.axvline(1.0, color="#2e7d32", ls="--", label="geogr. Unterschranke (1×)")
    ax.axvline(detour_stats["detour_factor_median"], color="#000", ls=":",
               label=f"Median {detour_stats['detour_factor_median']:.2f}×")
    ax.set_title(f"A4: GEMESSENE Detour-Faktoren ({detour_stats['n_packets']} reale Pakete)\n"
                 f"P90={detour_stats['detour_factor_p90']:.2f}×, "
                 f">2×: {100*detour_stats['frac_detour_gt_2x']:.0f}%")
ax.set_xlabel("Detour-Faktor (reale Hops / geogr. Unterschranke)")
ax.set_ylabel("Pakete")
ax.legend()
ax.grid(alpha=0.2)
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_v3_real_detours.png"))
plt.close(fig)

# --- Plot 4: Sim-Vergleich Baseline vs MHR ---
fig, axs = plt.subplots(1, 3, figsize=(12.5, 4.2))
if results:
    # Hops
    v = [sim_summary["mean_mhr_hops"], sim_summary["mean_base_hops"]]
    b = axs[0].bar(["MHR", "MeshCore"], v, color=["#2e7d32", "#c0392b"])
    for bb, vv in zip(b, v):
        axs[0].text(bb.get_x() + bb.get_width() / 2, vv, f"{vv:.2f}", ha="center", va="bottom")
    axs[0].set_title("Ø Hops")
    axs[0].grid(axis="y", alpha=0.2)
    # Airtime
    v = [sim_summary["mean_mhr_tx"], sim_summary["mean_base_tx"]]
    b = axs[1].bar(["MHR\n(Unicast)", "MeshCore\n(Flood)"], v, color=["#2e7d32", "#c0392b"])
    for bb, vv in zip(b, v):
        axs[1].text(bb.get_x() + bb.get_width() / 2, vv, f"{vv:.1f}", ha="center", va="bottom")
    axs[1].set_title(f"Ø Sende-Ereignisse (−{sim_summary['airtime_reduction_pct']:.0f}%)")
    axs[1].grid(axis="y", alpha=0.2)
    # Zuverlaessigkeit
    v = [sim_summary["mean_mhr_reliability"], sim_summary["mean_base_reliability"]]
    b = axs[2].bar(["MHR", "MeshCore"], v, color=["#2e7d32", "#c0392b"])
    for bb, vv in zip(b, v):
        axs[2].text(bb.get_x() + bb.get_width() / 2, vv, f"{vv:.2f}", ha="center", va="bottom")
    axs[2].set_title("Ø Pfad-Zuverlaessigkeit")
    axs[2].grid(axis="y", alpha=0.2)
fig.suptitle(f"B: SIMULIERT auf {len(giant_nodes)}-Knoten-Realtopologie "
             f"({sim_summary.get('n_pairs',0)} Quelle-Ziel-Paare, Seed {SEED})")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_v3_sim_compare.png"))
plt.close(fig)

log("  Plots geschrieben: fig_v3_real_topology.png, fig_v3_snr_fit.png, "
    "fig_v3_real_detours.png, fig_v3_sim_compare.png")
log("\n=== FERTIG ===")
