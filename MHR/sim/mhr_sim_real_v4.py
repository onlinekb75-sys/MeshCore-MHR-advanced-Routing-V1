#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — Simulation v4 auf der ECHTEN, server-gemessenen Link-Topologie
=============================================================================

Warum v4 (vs. v3 / Studie):
  v3 (mhr_sim_real_v3.py) und die Studie (study_sim.py) bauten den Routing-Graphen aus
  einem GEOMETRISCHEN Log-Distance-Linkmodell (Reichweitenscheiben). Dieses Modell traf
  nur ~41,7 % der real beobachteten Kanten und seine SNR-Distanz-Annahme wird von den
  Realdaten widerlegt (|corr(log d, SNR)| ~ 0,42, PLE ~ 0,4 statt 2,55).

  v4 nutzt stattdessen den SERVER-AUFGELOESTEN neighbor-graph: ECHTE Kanten + ECHTES
  Per-Link-SNR (avg_snr). Der Routing-Graph IST damit die gemessene Topologie, nicht eine
  geometrische Annahme. Link-Reliability/ETX werden aus dem echten avg_snr abgeleitet,
  NICHT aus Distanz. Das ist die genaueste verfuegbare Grundlage.

Datenquellen (alle im Repo unter data/, KEINE Rohpakete noetig):
  - neighbor_graph.json : {nodes, edges, stats}. Kanten mit source/target (Pubkeys, teils
                          gekuerzt), weight (Beobachtungszahl), score, avg_snr (ECHT, dB),
                          bidirectional, ambiguous. 1034 Knoten, 1956 Kanten.
  - nodes.json          : 1962 Knoten mit public_key, lat/lon, role, relay_count_*, scores.
  - snr_calibration.json: Kontext (Empfangsschwelle ~ -12 dB).

Methodik-Erbe (bewusst identisch zu v3/Studie, damit Vergleichbarkeit gegeben ist):
  - Flood-Modell: timing-getriebene Prioritaetswarteschlange, first-packet-wins-Dedup,
    Airtime = Anzahl tatsaechlich sendender Knoten (Sende-Ereignisse / Zustellung).
  - Mechanismen-Sweep (1 Knoten -> alle) mit Safety-Invariante:
    Lieferquote >= Baseline UND Airtime <= Baseline (Rausch-Band ueber Seeds).
  - >=5 Seeds fuer alle Zufallsanteile; Seed 42 als Master, reproduzierbar.

Aufruf:  python3 mhr_sim_real_v4.py
Optionale Env-Vars (nur zum Debuggen): V4_FAST=1, V4_PAIRS, V4_SEEDS.
"""

import json
import math
import os
import time
import heapq
import collections
import statistics
import numpy as np
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# --------------------------------------------------------------------------------------
# Konfiguration / Reproduzierbarkeit
# --------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NG_F = os.path.join(DATA, "neighbor_graph.json")
NODES_F = os.path.join(DATA, "nodes.json")
CAL_F = os.path.join(DATA, "snr_calibration.json")

MASTER_SEED = 42

FAST = os.environ.get("V4_FAST", "0") == "1"
N_PAIRS = int(os.environ.get("V4_PAIRS", "150" if not FAST else "25"))
N_SEEDS = int(os.environ.get("V4_SEEDS", "6" if not FAST else "2"))   # >=5 fuer Zufallsanteile

# Physik / LoRa
SNR_THR = -12.0     # Empfangsschwelle (dB), konsistent zu v3/Kalibrierung
SNR_SCALE = 4.0     # Breite der logistischen Reliability-Kurve um die Schwelle (dB)

# Flood-Timing-Modell (identisch zu Studie -> Vergleichbarkeit)
FLOOD_MAX_BASE = 64
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0
TX_HOP_WEIGHT = 0.6   # ausgelieferter Stufe-A-Wert fuer hop-gewichtetes Delay (M1/COMBI)


def log(msg):
    print(msg, flush=True)


def haversine(la1, lo1, la2, lo2):
    R = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


# ======================================================================================
# 1) REALE TOPOLOGIE AUFBAUEN (echte Kanten + echtes Per-Link-SNR)
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json aufbauen ===")
ng = json.load(open(NG_F))
nodes_full = json.load(open(NODES_F))["nodes"]
cal = json.load(open(CAL_F))
SNR_THR = float(cal.get("snr_threshold_db", SNR_THR))

# --- Knoten-Identitaet: die neighbor-graph-Knoten tragen pubkey/name/role selbst.
#     nodes.json dient NUR zur Anreicherung (Geo, relay-Stats, Traffic-Scores) via
#     Pubkey-(Praefix-)Join. Robust gegen fehlende/gekuerzte Keys.
pk_to_full = {}     # voller pubkey (lower) -> index in nodes_full
for i, n in enumerate(nodes_full):
    pk = n.get("public_key")
    if pk:
        pk_to_full[pk.lower()] = i

# Praefix-Index fuer gekuerzte Endpunkt-Keys (9/11/13 hex statt 64)
# -> eindeutig nur, wenn genau EIN voller Key mit dem Praefix beginnt.
_full_keys = list(pk_to_full.keys())


def resolve_node(pk):
    """Loese einen (evtl. gekuerzten) Pubkey zu GENAU EINEM nodes.json-Index auf.
    Rueckgabe: index oder None (kein/uneindeutiger Treffer)."""
    if not pk:
        return None
    pk = pk.lower()
    idx = pk_to_full.get(pk)
    if idx is not None:
        return idx
    if len(pk) < 64:
        hits = [pk_to_full[k] for k in _full_keys if k.startswith(pk)]
        if len(hits) == 1:
            return hits[0]
    return None


def valid_geo(la, lo):
    if la is None or lo is None:
        return False
    if abs(la) < 0.5 and abs(lo) < 0.5:
        return False
    return 35.0 <= la <= 60.0 and -12.0 <= lo <= 25.0


# Knoten-Metadaten (pro neighbor-graph-Knoten). Geo/Stats aus nodes.json angereichert.
NODE = {}     # pubkey(lower) -> dict(name, role, lat, lon, relay24, traffic, usefulness)
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    meta = {"name": x.get("name"), "role": x.get("role"),
            "neighbor_count": x.get("neighbor_count", 0),
            "lat": None, "lon": None, "relay24": 0, "traffic": 0.0, "useful": 0.0}
    idx = resolve_node(pk)
    if idx is not None:
        n = nodes_full[idx]
        if valid_geo(n.get("lat"), n.get("lon")):
            meta["lat"], meta["lon"] = n["lat"], n["lon"]
        meta["relay24"] = n.get("relay_count_24h", 0) or 0
        meta["traffic"] = n.get("traffic_share_score", 0) or 0.0
        meta["useful"] = n.get("usefulness_score", 0) or 0.0
        if not meta["role"]:
            meta["role"] = n.get("role")
    NODE[pk] = meta


def snr_reliability(snr_db):
    """Link-Reliability aus ECHTEM avg_snr (logistisch um die Empfangsschwelle).
    NICHT aus Distanz. Bei SNR an/ueber Schwelle -> hohe, aber gedeckelte Zuverlaessigkeit."""
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / SNR_SCALE)), 0.02, 0.995))


# Kanten aufbauen. ambiguous-Kanten fuer die Kern-Analyse VERWERFEN (separat zaehlen).
# weight = Vertrauens-/Stabilitaetsmass (Beobachtungszahl) -> als Kanten-Attribut behalten.
G = nx.Graph()              # Kern-Graph (ohne ambiguous)
SNR = {}                    # (u,v) -> avg_snr   (symmetrisch)
PREL = {}                   # (u,v) -> Link-Reliability aus SNR (symmetrisch)
WEIGHT = {}                 # (u,v) -> Beobachtungszahl
n_ambig = 0
snr_used = []
for e in ng["edges"]:
    u, v = e["source"].lower(), e["target"].lower()
    if u == v:
        continue
    if e.get("ambiguous"):
        n_ambig += 1
        continue
    s = e.get("avg_snr")
    if s is None:
        continue
    pr = snr_reliability(float(s))
    w = e.get("weight", 1)
    # ETX aus Reliability: erwartete Sendeversuche fuer Erfolg in beide Richtungen ~ 1/(p*p).
    etx = 1.0 / max(pr * pr, 1e-4)
    G.add_edge(u, v, snr=float(s), prel=pr, etx=etx, weight=w)
    SNR[(u, v)] = SNR[(v, u)] = float(s)
    PREL[(u, v)] = PREL[(v, u)] = pr
    WEIGHT[(u, v)] = WEIGHT[(v, u)] = w
    snr_used.append(float(s))

# Stelle sicher, dass alle neighbor-graph-Knoten im Graph sind (auch grad-0 -> isoliert)
for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)

# Geo-Abdeckung
n_geo = sum(1 for pk in NODE if NODE[pk]["lat"] is not None)
roles = collections.Counter(NODE[pk]["role"] for pk in G.nodes() if pk in NODE)

log(f"  Knoten (Kern-Graph): {G.number_of_nodes()}  |  Kanten (Kern, ohne ambiguous): "
    f"{G.number_of_edges()}  |  ambiguous verworfen: {n_ambig}")
log(f"  Grad (ungerichtet) min/median/mean/max: "
    f"{min(degs)}/{statistics.median(degs):.0f}/{statistics.mean(degs):.2f}/{max(degs)}")
log(f"  Komponenten: {len(comps)}  |  groesste Zusammenhangskomponente: {len(giant)} "
    f"({100*len(giant)/max(G.number_of_nodes(),1):.0f} %)  |  Top5: "
    f"{[len(c) for c in comps[:5]]}")
log(f"  Knoten mit Geo (joinbar): {n_geo}/{G.number_of_nodes()}  |  Rollen: {dict(roles)}")
if snr_used:
    sa = np.array(snr_used)
    log(f"  Per-Link-SNR (echt): Median {np.median(sa):.2f} dB, "
        f"P10 {np.percentile(sa,10):.1f}, P90 {np.percentile(sa,90):.1f} dB; "
        f"-> Reliability Median {snr_reliability(float(np.median(sa))):.2f}")

# Precompute Adjazenz (Nachbar, Reliability) fuer den Flood -> schneller als nx.neighbors
ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}
deg_gc = dict(GC.degree())
log(f"  Riesenkomponente fuer Simulation: {GC.number_of_nodes()} Knoten, "
    f"{GC.number_of_edges()} Kanten, Grad median {int(np.median(list(deg_gc.values())))}, "
    f"max {max(deg_gc.values())}")

# Unterschied zu v3 explizit beziffern: v3-Sim-Linkgraph war dicht (Top-20-Nachbarn,
# ~9861 Kanten / 831 Knoten -> Ø-Grad ~24). v4 ist sparse-real (Ø-Grad ~3,8).
v3_avg_deg = 9861 * 2 / 831
log(f"  UNTERSCHIED zu v3: v3-Sim-Graph Ø-Grad ~{v3_avg_deg:.1f} (dicht, geometrisch); "
    f"v4 Ø-Grad {statistics.mean(degs):.2f} (sparse, real gemessen).")


# ======================================================================================
# 2) FLOOD-MODELL + MECHANISMEN  (Methodik wie Studie, aber auf REALEN Kanten)
# ======================================================================================
def rebroadcast_delay(hops, mech, is_new, rstate):
    """Sende-Delay nach Empfang. Stock: Basis + voller Jitter.
    M1 (Neu): hop-gewichtet (TX_HOP_WEIGHT) -> weniger akkumulierte Hops => frueher."""
    air = BASE_AIR + PER_HOP_AIR * hops
    if is_new and mech in ("M1", "COMBI"):
        base = air * (1.0 + TX_HOP_WEIGHT * hops)
        return base + rstate.uniform(0.0, 1.5 * air)
    return air + rstate.uniform(0.0, JITTER * air)


def reconstruct_path(accepted, node, src):
    path = [node]
    cur = node
    guard = 0
    while cur != src:
        info = accepted.get(cur)
        if info is None or info[1] is None:
            break
        cur = info[1]
        path.append(cur)
        guard += 1
        if guard > 5000:
            break
    path.reverse()
    return path


def path_reliability(path):
    if not path or len(path) < 2:
        return 1.0
    r = 1.0
    for a, b in zip(path, path[1:]):
        r *= PREL.get((a, b), 0.0)
    return r


def path_hops(path):
    return (len(path) - 1) if path else 0


def run_flood(src, dst, newfw, mech, rstate, flood_max,
              dead_nodes=None, dead_edges=None, mpr_set=None):
    """Timing-getriebener Flood auf der REALEN Topologie.
    Rueckgabe: (delivered, used_path, n_tx). n_tx = Airtime (sendende Knoten).
    Mechanismen lokal (nur newfw-Knoten):
      M0 Baseline first-wins; M1 hop-gewichtetes Delay; M2k(k) counter-suppress;
      M3 shorter-path-cancel; M4 MPR/CDS-Schweigen; M5 Best-of-N-am-Ziel-nach-Hops;
      M7 nur flood_max (Hop-Limit, lokal); COMBI = M1+M3+M5+M7.
    """
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    use_m1 = mech in ("M1",)            # M1 wirkt im Delay (oben), hier kein Sonderzweig noetig
    use_m2 = mech in ("M2k2", "M2k3")
    k_supp = 2 if mech == "M2k2" else 3
    use_m3 = mech in ("M3", "COMBI")
    use_m4 = mech in ("M4",)
    use_m5 = mech in ("M5", "COMBI")

    accepted = {}
    acc_hops = {}
    sent = set()
    heard_count = collections.Counter()
    short_dup = collections.Counter()
    dst_paths = []
    seq = 0
    pq = []

    def schedule_send(u, t_send, hops, prev):
        nonlocal seq
        heapq.heappush(pq, (t_send, seq, u, hops, prev))
        seq += 1

    schedule_send(src, 0.0, 0, None)
    accepted[src] = (0, None)
    acc_hops[src] = 0

    while pq:
        t_send, _, u, hops_u, prev_u = heapq.heappop(pq)
        node_max = flood_max if (u in newfw) else FLOOD_MAX_BASE
        if hops_u >= node_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:
            continue
        if use_m4 and (u in newfw) and (mpr_set is not None) and (u not in mpr_set) and u != src:
            continue
        if use_m2 and (u in newfw) and u != src and u != dst:
            if heard_count[u] >= k_supp:
                continue
        if use_m3 and (u in newfw) and u != src and u != dst:
            if short_dup[u] >= 1:
                continue

        sent.add(u)
        out_hops = hops_u + 1
        rnd = rstate.random
        for v, pr in ADJ[u]:
            if has_dead and not link_ok(u, v):
                continue
            if rnd() > pr:
                continue
            heard_count[v] += 1
            if v == dst:
                p = reconstruct_path(accepted, u, src) + [v]
                dst_paths.append((out_hops, p))
            if v not in accepted:
                accepted[v] = (out_hops, u)
                acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, mech, v in newfw, rstate)
                schedule_send(v, t_send + d, out_hops, u)
            else:
                if v not in sent and out_hops <= acc_hops.get(v, out_hops):
                    short_dup[v] += 1

    delivered = dst in accepted or len(dst_paths) > 0
    if not delivered:
        return False, None, len(sent)
    if use_m5 and dst_paths:
        best = min(dst_paths, key=lambda hp: (hp[0], -path_reliability(hp[1])))
        used = best[1]
    else:
        used = reconstruct_path(accepted, dst, src)
    return True, used, len(sent)


# ======================================================================================
# MHR-Referenzpfad (qualitaets-/hopgeleitet) auf realen Kanten
# ======================================================================================
def mhr_path_etx(s, d, graph):
    """MHR: ETX-kuerzester Pfad (echtes Per-Link-SNR -> Reliability -> ETX). prefer-shorter
    implizit (ETX waechst mit Hops)."""
    try:
        return nx.shortest_path(graph, s, d, weight="etx")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def hop_path(s, d, graph):
    try:
        return nx.shortest_path(graph, s, d)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


# ======================================================================================
# M4: Greedy-CDS / MPR-Relaymenge auf der realen Riesenkomponente
# ======================================================================================
def compute_cds(graph):
    nb = {u: ({u} | {v for v, _ in ADJ[u]}) for u in graph.nodes()}
    nodes_set = set(graph.nodes())
    dominated = set()
    relays = set()
    gain = {u: len(nb[u]) for u in nodes_set}
    while len(dominated) < len(nodes_set):
        best = max(gain, key=gain.get)
        true_gain = len(nb[best] - dominated)
        if true_gain != gain[best]:
            gain[best] = true_gain
            continue
        if true_gain <= 0:
            break
        relays.add(best)
        dominated |= nb[best]
        del gain[best]
    return relays


log("\n=== M4: Greedy-Relaymenge (CDS/MPR) auf realer Topologie ===")
t0 = time.time()
CDS = compute_cds(GC)
log(f"  Relaymenge: {len(CDS)}/{GC.number_of_nodes()} "
    f"({100*len(CDS)/max(GC.number_of_nodes(),1):.1f} %), {time.time()-t0:.1f}s")


# ======================================================================================
# Rollout / Sweep-Infrastruktur (wie Studie)
# ======================================================================================
def traffic_score(pk):
    m = NODE.get(pk, {})
    return (m.get("relay24", 0), m.get("traffic", 0.0))


top_traffic_order = sorted(giant_list, key=lambda pk: traffic_score(pk), reverse=True)


def select_newfw(alpha, rollout, seed):
    rstate = np.random.default_rng(seed * 100003 + 7)
    n = len(giant_list)
    if alpha == 0.0:
        return set()
    k = 1 if alpha == "1node" else max(1, int(round(alpha * n)))
    if rollout == "top_traffic":
        return set(top_traffic_order[:k])
    idx = rstate.choice(n, size=min(k, n), replace=False)
    return set(giant_list[i] for i in idx)


def mech_flood_max(mech):
    if mech == "M7_10":
        return 10
    if mech == "M7_12":
        return 12
    if mech == "M7_15":
        return 15
    if mech == "M7_18":
        return 18
    if mech == "M7_20":
        return 20
    if mech == "M7_64":
        return 64
    if mech == "COMBI":
        return 15   # COMBI nutzt den ausgelieferten Stufe-A-Wert (statt 12 wie v3)
    return FLOOD_MAX_BASE


def mech_mpr(mech):
    return CDS if mech == "M4" else None


def make_pairs(seed, n_pairs):
    rstate = np.random.default_rng(seed * 7919 + 13)
    arr = list(giant_list)
    pairs = []
    for _ in range(n_pairs):
        i, j = rstate.choice(len(arr), size=2, replace=False)
        pairs.append((arr[i], arr[j]))
    return pairs


_sp_cache = {}


def shortest_hops(s, d, dead_nodes, dead_edges):
    if not dead_nodes and not dead_edges:
        key = (s, d)
        if key in _sp_cache:
            return _sp_cache[key]
        try:
            v = nx.shortest_path_length(GC, s, d)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            v = None
        _sp_cache[key] = v
        return v
    H = GC.copy()
    H.remove_nodes_from(dead_nodes)
    H.remove_edges_from(tuple(e) for e in dead_edges)
    try:
        return nx.shortest_path_length(H, s, d)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def evaluate(mech, alpha, rollout, seeds, n_pairs,
             dead_nodes_frac=0.0, dead_edges_frac=0.0, stress_seed_off=0):
    flood_max = mech_flood_max(mech)
    mpr = mech_mpr(mech)
    air_list, deliv_list, hop_list, detour_list, rel_list = [], [], [], [], []
    pair_paths = collections.defaultdict(list)
    per_seed_deliv, per_seed_air = [], []

    for si, seed in enumerate(seeds):
        seed_deliv, seed_air = [], []
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503 + stress_seed_off)
        newfw = select_newfw(alpha, rollout, seed)
        dead_nodes, dead_edges = set(), set()
        if dead_nodes_frac > 0:
            order = sorted(giant_list,
                           key=lambda pk: NODE.get(pk, {}).get("relay24", 0))
            k = int(dead_nodes_frac * len(order))
            cand = order[:max(k * 3, k)]
            if cand:
                sel = rstate.choice(len(cand), size=min(k, len(cand)), replace=False)
                dead_nodes = set(cand[i] for i in sel)
        if dead_edges_frac > 0:
            all_e = list(GC.edges())
            k = int(dead_edges_frac * len(all_e))
            if all_e and k > 0:
                sel = rstate.choice(len(all_e), size=k, replace=False)
                dead_edges = set(frozenset(all_e[i]) for i in sel)

        for (s, d) in make_pairs(seed, n_pairs):
            if s in dead_nodes or d in dead_nodes:
                continue
            ok, used, ntx = run_flood(s, d, newfw, mech, rstate, flood_max,
                                      dead_nodes=dead_nodes, dead_edges=dead_edges,
                                      mpr_set=mpr)
            deliv_list.append(1.0 if ok else 0.0)
            seed_deliv.append(1.0 if ok else 0.0)
            if ok and used:
                h = path_hops(used)
                air_list.append(ntx)
                seed_air.append(ntx)
                hop_list.append(h)
                rel_list.append(path_reliability(used))
                sp = shortest_hops(s, d, dead_nodes, dead_edges)
                if sp and sp > 0:
                    detour_list.append(h / sp)
                pair_paths[(s, d)].append(tuple(used))
        if seed_deliv:
            per_seed_deliv.append(float(np.mean(seed_deliv)))
        if seed_air:
            per_seed_air.append(float(np.mean(seed_air)))

    stable = [1.0 if len(set(pl)) == 1 else 0.0
              for pl in pair_paths.values() if len(pl) >= 2]
    route_stability = float(np.mean(stable)) if stable else 1.0

    def safem(x):
        return float(np.mean(x)) if len(x) else 0.0

    return {
        "mech": mech, "alpha": alpha, "rollout": rollout,
        "n_obs": len(deliv_list),
        "delivery": safem(deliv_list),
        "airtime_mean": safem(air_list),
        "airtime_median": float(np.median(air_list)) if air_list else 0.0,
        "hops_mean": safem(hop_list),
        "hops_median": float(np.median(hop_list)) if hop_list else 0.0,
        "detour_ratio_mean": safem(detour_list),
        "detour_ratio_median": float(np.median(detour_list)) if detour_list else 0.0,
        "reliability_mean": safem(rel_list),
        "route_stability": route_stability,
        "deliv_sem": (float(np.std(per_seed_deliv, ddof=1) / math.sqrt(len(per_seed_deliv)))
                      if len(per_seed_deliv) >= 2 else 0.0),
        "air_sem": (float(np.std(per_seed_air, ddof=1) / math.sqrt(len(per_seed_air)))
                    if len(per_seed_air) >= 2 else 0.0),
    }


# ======================================================================================
# 2a) Baseline (first-wins-Flood) vs. MHR (ETX/hop-geleitet) — Direktvergleich
# ======================================================================================
log("\n=== 2a) Baseline (first-wins-Flood) vs. MHR auf realen Kanten ===")
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))

# Baseline-Flood-Kennzahlen
base = evaluate("M0", 0.0, "random", seeds, N_PAIRS)
BASE_DELIV = base["delivery"]
BASE_AIR_M = base["airtime_mean"]
log(f"  Baseline-Flood: delivery={BASE_DELIV:.3f}  airtime={BASE_AIR_M:.1f}  "
    f"hops_mean={base['hops_mean']:.2f}  detour_median={base['detour_ratio_median']:.2f}  "
    f"rel={base['reliability_mean']:.3f}")

# MHR-Referenzpfade auf denselben Paaren (ETX-kuerzest). Airtime MHR = Unicast = Hops.
mhr_hops, mhr_rel, mhr_tx = [], [], []
base_hops_for_pairs, detour_b_over_mhr = [], []
n_eval_pairs = 0
for seed in seeds:
    for (s, d) in make_pairs(seed, N_PAIRS):
        mp = mhr_path_etx(s, d, GC)
        if not mp:
            continue
        h = path_hops(mp)
        mhr_hops.append(h)
        mhr_rel.append(path_reliability(mp))
        mhr_tx.append(h)            # Unicast entlang des Pfads
        # Baseline-Hops desselben Paares (ein Flood-Lauf je Paar, gemittelt ueber Seeds reicht)
        ok, used, ntx = run_flood(s, d, set(), "M0",
                                  np.random.default_rng(seed * 13 + hash((s, d)) % 9973),
                                  FLOOD_MAX_BASE)
        if ok and used:
            bh = path_hops(used)
            base_hops_for_pairs.append(bh)
            if h > 0:
                detour_b_over_mhr.append(bh / h)
        n_eval_pairs += 1

mhr_summary = {
    "n_pairs": n_eval_pairs,
    "mean_mhr_hops": float(np.mean(mhr_hops)) if mhr_hops else 0.0,
    "mean_base_hops": float(np.mean(base_hops_for_pairs)) if base_hops_for_pairs else 0.0,
    "mean_detour_base_over_mhr": float(np.mean(detour_b_over_mhr)) if detour_b_over_mhr else 0.0,
    "median_detour_base_over_mhr": float(np.median(detour_b_over_mhr)) if detour_b_over_mhr else 0.0,
    "pct_pairs_base_longer": float(100 * np.mean(np.array(detour_b_over_mhr) > 1.0))
                              if detour_b_over_mhr else 0.0,
    "mean_base_airtime": BASE_AIR_M,
    "mean_mhr_airtime": float(np.mean(mhr_tx)) if mhr_tx else 0.0,
    "airtime_reduction_pct": float(100 * (1 - np.mean(mhr_tx) / BASE_AIR_M))
                              if (mhr_tx and BASE_AIR_M > 0) else 0.0,
    "mean_base_reliability": base["reliability_mean"],
    "mean_mhr_reliability": float(np.mean(mhr_rel)) if mhr_rel else 0.0,
    "base_delivery": BASE_DELIV,
}
log("  Baseline vs. MHR:")
for k, v in mhr_summary.items():
    log(f"    {k}: {v}")


# ======================================================================================
# 2b) flood.max-Sweep auf der REALEN (sparseren) Topologie
# ======================================================================================
log("\n=== 2b) flood.max-Sweep (10/12/15/18/20/64) bei voller Adoption ===")
# Frage: Bleibt die Lieferquote >= Baseline auf der sparseren realen Topologie? Welcher
# Schwellwert ist optimal (max. Airtime-Ersparnis bei deliv >= Baseline)?
flood_sweep = []
# Netzdurchmesser-Kontext der realen Topologie
ecc_sample = []
_diam_nodes = giant_list if len(giant_list) <= 400 else \
    list(np.random.default_rng(1).choice(giant_list, size=400, replace=False))
for u in _diam_nodes[:200]:
    try:
        lengths = nx.single_source_shortest_path_length(GC, u)
        ecc_sample.append(max(lengths.values()))
    except Exception:
        pass
diam_p90 = float(np.percentile(ecc_sample, 90)) if ecc_sample else 0.0
diam_max = int(max(ecc_sample)) if ecc_sample else 0
log(f"  Realer Netzdurchmesser (Hops, Stichprobe): P90={diam_p90:.0f}, max={diam_max}")

for fm_mech in ["M7_10", "M7_12", "M7_15", "M7_18", "M7_20", "M7_64"]:
    r = evaluate(fm_mech, 1.0, "random", seeds, N_PAIRS)
    fmv = mech_flood_max(fm_mech)
    r["flood_max"] = fmv
    r["delivery_vs_base"] = r["delivery"] - BASE_DELIV
    r["airtime_vs_base_pct"] = (100 * (r["airtime_mean"] - BASE_AIR_M) / BASE_AIR_M
                                if BASE_AIR_M > 0 else 0.0)
    flood_sweep.append(r)
    log(f"  flood.max={fmv:2d}: deliv={r['delivery']:.3f} ({r['delivery_vs_base']:+.3f}) "
        f"air={r['airtime_mean']:.1f} ({r['airtime_vs_base_pct']:+.1f}%) "
        f"hops_med={r['hops_median']:.0f}")


# ======================================================================================
# 2c) Adoptions-Sweep + Safety-Invariante (Dreiteilung pruefen)
# ======================================================================================
log("\n=== 2c) Adoptions-Sweep (1 Knoten -> alle) mit Safety-Invariante ===")
ALPHAS = [0.0, "1node", 0.01, 0.05, 0.10, 0.25, 0.50, 1.0]
MECHS = ["M0", "M1", "M2k2", "M2k3", "M3", "M4", "M5", "M7_15", "COMBI"]
ROLLOUTS = ["random", "top_traffic"]

DELIV_TOL = max(2.0 * base.get("deliv_sem", 0.0), 0.01)
AIR_TOL = max(2.0 * base.get("air_sem", 0.0), 0.005 * BASE_AIR_M)
log(f"  Rausch-Band (2*SEM, min): Lieferquote +-{DELIV_TOL:.4f}, "
    f"Airtime +-{AIR_TOL:.2f}")

sweep = []
for mech in MECHS:
    for rollout in ROLLOUTS:
        for alpha in ALPHAS:
            row = evaluate(mech, alpha, rollout, seeds, N_PAIRS)
            safe = (row["delivery"] >= BASE_DELIV - DELIV_TOL) and \
                   (row["airtime_mean"] <= BASE_AIR_M + AIR_TOL)
            row["safe"] = bool(safe)
            row["safe_strict"] = bool((row["delivery"] >= BASE_DELIV - 1e-9) and
                                      (row["airtime_mean"] <= BASE_AIR_M + 1e-9))
            row["delivery_vs_base"] = row["delivery"] - BASE_DELIV
            row["airtime_vs_base_pct"] = (100 * (row["airtime_mean"] - BASE_AIR_M) / BASE_AIR_M
                                          if BASE_AIR_M > 0 else 0.0)
            sweep.append(row)
        # nur kompakte Logzeile je (mech,rollout) bei alpha=1.0
        r1 = sweep[-1]
        log(f"  [{mech:6s} {rollout:11s} a=1.0] deliv={r1['delivery']:.3f} "
            f"({r1['delivery_vs_base']:+.3f}) air={r1['airtime_mean']:6.1f} "
            f"({r1['airtime_vs_base_pct']:+6.1f}%) detour={r1['detour_ratio_median']:.2f} "
            f"SAFE={'OK' if r1['safe'] else 'X'}")


# ======================================================================================
# 2d) SNR-Frage neu: korreliert echtes avg_snr mit Pfad-Kuerze / Reliability?
# ======================================================================================
log("\n=== 2d) SNR-Frage neu pruefen (echtes Per-Link-SNR) ===")
# (i) Korreliert das mittlere Pfad-SNR mit der Hop-Zahl des MHR-Pfads?
# (ii) Liefert ein SNR-/ETX-geleiteter Pfad kuerzere/zuverlaessigere Pfade als der reine
#      Hop-kuerzeste Pfad? (Wenn SNR irrelevant fuer Topologie waere, waeren beide gleich.)
snr_vs_hops_x, snr_vs_hops_y = [], []     # (mean_path_snr, hops)
etx_vs_hop_better_rel = []                # rel(ETX-Pfad) - rel(Hop-Pfad)
etx_vs_hop_extra_hops = []                # hops(ETX) - hops(Hop)  (>=0 per Definition)
# (iii) Korreliert die Einzel-Link-SNR mit der Link-Reliability? (trivial ja) und mit
#       Knotengrad? (Hub-Effekt)
link_snr_arr = np.array(snr_used) if snr_used else np.array([0.0])

cmp_seed_pairs = []
for seed in seeds[:3]:
    cmp_seed_pairs += make_pairs(seed, N_PAIRS)
seen_pairs = set()
for (s, d) in cmp_seed_pairs:
    if (s, d) in seen_pairs:
        continue
    seen_pairs.add((s, d))
    etxp = mhr_path_etx(s, d, GC)
    hopp = hop_path(s, d, GC)
    if not etxp or not hopp:
        continue
    h = path_hops(etxp)
    # mittleres Per-Link-SNR entlang des ETX-Pfads
    msnr = np.mean([SNR[(a, b)] for a, b in zip(etxp, etxp[1:])]) if h > 0 else 0.0
    snr_vs_hops_x.append(msnr)
    snr_vs_hops_y.append(h)
    etx_vs_hop_better_rel.append(path_reliability(etxp) - path_reliability(hopp))
    etx_vs_hop_extra_hops.append(path_hops(etxp) - path_hops(hopp))

snr_hop_corr = float(np.corrcoef(snr_vs_hops_x, snr_vs_hops_y)[0, 1]) \
    if len(snr_vs_hops_x) > 5 else 0.0
# Link-SNR vs. Knotengrad (Hub-Effekt: haben hochgradige Knoten bessere oder schlechtere Links?)
deg_snr_x, deg_snr_y = [], []
for (u, v) in G.edges():
    deg_snr_x.append((G.degree(u) + G.degree(v)) / 2.0)
    deg_snr_y.append(SNR[(u, v)])
deg_snr_corr = float(np.corrcoef(deg_snr_x, deg_snr_y)[0, 1]) if len(deg_snr_x) > 5 else 0.0

snr_findings = {
    "n_links_with_snr": int(len(snr_used)),
    "link_snr_median": float(np.median(link_snr_arr)),
    "corr_pathSNR_vs_hops": snr_hop_corr,
    "etx_vs_hop_extra_hops_mean": float(np.mean(etx_vs_hop_extra_hops)) if etx_vs_hop_extra_hops else 0.0,
    "etx_vs_hop_rel_gain_mean": float(np.mean(etx_vs_hop_better_rel)) if etx_vs_hop_better_rel else 0.0,
    "etx_vs_hop_rel_gain_median": float(np.median(etx_vs_hop_better_rel)) if etx_vs_hop_better_rel else 0.0,
    "pct_pairs_etx_differs_from_hop": float(100 * np.mean(np.array(etx_vs_hop_extra_hops) != 0))
                                       if etx_vs_hop_extra_hops else 0.0,
    "corr_linkSNR_vs_nodedegree": deg_snr_corr,
    "n_pairs_compared": len(snr_vs_hops_x),
}
log(f"  Korr(Pfad-SNR, Hops)           = {snr_hop_corr:+.2f}")
log(f"  ETX-Pfad vs. Hop-Pfad: Ø extra Hops = {snr_findings['etx_vs_hop_extra_hops_mean']:+.2f}, "
    f"Ø Reliability-Gewinn = {snr_findings['etx_vs_hop_rel_gain_mean']:+.3f} "
    f"(Median {snr_findings['etx_vs_hop_rel_gain_median']:+.3f})")
log(f"  Anteil Paare, bei denen ETX-Pfad != Hop-Pfad: "
    f"{snr_findings['pct_pairs_etx_differs_from_hop']:.0f} %")
log(f"  Korr(Link-SNR, Knotengrad)     = {deg_snr_corr:+.2f}")


# ======================================================================================
# 3) ERGEBNISSE SCHREIBEN
# ======================================================================================
log("\n=== 3) Ergebnisse / Plots schreiben ===")
results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS, "fast_mode": FAST,
    "topology": {
        "core_nodes": G.number_of_nodes(),
        "core_edges": G.number_of_edges(),
        "ambiguous_edges_dropped": n_ambig,
        "degree_min_median_mean_max": [min(degs), float(statistics.median(degs)),
                                       float(statistics.mean(degs)), max(degs)],
        "components": len(comps),
        "giant_nodes": len(giant),
        "giant_edges": GC.number_of_edges(),
        "nodes_with_geo": n_geo,
        "roles": dict(roles),
        "cds_relays": len(CDS),
        "diam_p90_hops": diam_p90, "diam_max_hops": diam_max,
        "v3_sim_avg_degree_ref": v3_avg_deg,
        "v4_avg_degree": float(statistics.mean(degs)),
        "link_snr_median_db": float(np.median(link_snr_arr)),
    },
    "baseline_vs_mhr": mhr_summary,
    "baseline": {"delivery": BASE_DELIV, "airtime_mean": BASE_AIR_M,
                 "hops_mean": base["hops_mean"], "detour_median": base["detour_ratio_median"],
                 "reliability_mean": base["reliability_mean"]},
    "flood_max_sweep": flood_sweep,
    "adoption_sweep": sweep,
    "safety_tolerance": {"delivery_tol": DELIV_TOL, "airtime_tol": AIR_TOL},
    "snr_findings": snr_findings,
}
json.dump(results, open(os.path.join(HERE, "sim_results_v4.json"), "w"),
          indent=2, ensure_ascii=False)
log("  sim_results_v4.json geschrieben.")


# --------------------------------------------------------------------------------------
# PLOTS
# --------------------------------------------------------------------------------------
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})

# fig_v4_topology — reale Kanten geografisch (sofern Geo), Kanten nach SNR gefaerbt
fig, ax = plt.subplots(figsize=(8.4, 7.8))
drawn = 0
import matplotlib.cm as cm
norm = matplotlib.colors.Normalize(vmin=-12, vmax=13)
for (u, v) in G.edges():
    mu, mv = NODE.get(u, {}), NODE.get(v, {})
    if mu.get("lat") is not None and mv.get("lat") is not None:
        c = cm.viridis(norm(SNR[(u, v)]))
        ax.plot([mu["lon"], mv["lon"]], [mu["lat"], mv["lat"]],
                color=c, lw=0.5, alpha=0.5, zorder=1)
        drawn += 1
geo_nodes = [(m["lon"], m["lat"]) for m in NODE.values() if m.get("lat") is not None]
if geo_nodes:
    ax.scatter([p[0] for p in geo_nodes], [p[1] for p in geo_nodes],
               c="#c0392b", s=5, zorder=2, label=f"Knoten mit Geo ({len(geo_nodes)})")
sm = cm.ScalarMappable(norm=norm, cmap="viridis"); sm.set_array([])
fig.colorbar(sm, ax=ax, label="Echtes avg_snr je Kante (dB)", shrink=0.7)
ax.set_title(f"v4: ECHTE neighbor-graph-Topologie ({G.number_of_edges()} Kanten, "
             f"{drawn} mit Geo gezeichnet)\nKern ohne ambiguous, groesste Komponente "
             f"{len(giant)} Knoten, Ø-Grad {statistics.mean(degs):.2f} (SPARSE & REAL)")
ax.set_xlabel("Laenge (°O)"); ax.set_ylabel("Breite (°N)")
ax.legend(loc="lower left"); ax.grid(alpha=0.2)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_v4_topology.png")); plt.close(fig)

# fig_v4_baseline_vs_mhr
fig, axs = plt.subplots(1, 3, figsize=(12.5, 4.2))
v = [mhr_summary["mean_mhr_hops"], mhr_summary["mean_base_hops"]]
b = axs[0].bar(["MHR", "Baseline"], v, color=["#2e7d32", "#c0392b"])
for bb, vv in zip(b, v):
    axs[0].text(bb.get_x()+bb.get_width()/2, vv, f"{vv:.2f}", ha="center", va="bottom")
axs[0].set_title("Ø Hops"); axs[0].grid(axis="y", alpha=0.2)
v = [mhr_summary["mean_mhr_airtime"], mhr_summary["mean_base_airtime"]]
b = axs[1].bar(["MHR\n(Unicast)", "Baseline\n(Flood)"], v, color=["#2e7d32", "#c0392b"])
for bb, vv in zip(b, v):
    axs[1].text(bb.get_x()+bb.get_width()/2, vv, f"{vv:.1f}", ha="center", va="bottom")
axs[1].set_title(f"Ø Sende-Ereignisse (−{mhr_summary['airtime_reduction_pct']:.0f} %)")
axs[1].grid(axis="y", alpha=0.2)
v = [mhr_summary["mean_mhr_reliability"], mhr_summary["mean_base_reliability"]]
b = axs[2].bar(["MHR", "Baseline"], v, color=["#2e7d32", "#c0392b"])
for bb, vv in zip(b, v):
    axs[2].text(bb.get_x()+bb.get_width()/2, vv, f"{vv:.2f}", ha="center", va="bottom")
axs[2].set_title("Ø Pfad-Zuverlaessigkeit"); axs[2].grid(axis="y", alpha=0.2)
fig.suptitle(f"v4: Baseline (first-wins-Flood) vs. MHR auf REALEN Kanten "
             f"({mhr_summary['n_pairs']} Paare, {N_SEEDS} Seeds)")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_v4_baseline_vs_mhr.png")); plt.close(fig)

# fig_v4_floodmax_sweep
fig, ax = plt.subplots(figsize=(7.6, 5.0))
fms = [r["flood_max"] for r in flood_sweep]
delv = [r["delivery"] for r in flood_sweep]
airp = [r["airtime_vs_base_pct"] for r in flood_sweep]
ax.plot(fms, delv, "o-", color="#2e7d32", label="Lieferquote")
ax.axhline(BASE_DELIV, color="#2e7d32", ls=":", lw=1, label=f"Baseline-Lieferquote {BASE_DELIV:.2f}")
ax.set_xlabel("flood.max (Hop-Limit)"); ax.set_ylabel("Lieferquote", color="#2e7d32")
ax.tick_params(axis="y", labelcolor="#2e7d32")
ax2 = ax.twinx()
ax2.plot(fms, airp, "s--", color="#c0392b", label="Airtime vs. Baseline (%)")
ax2.set_ylabel("Airtime-Aenderung vs. Baseline (%)", color="#c0392b")
ax2.tick_params(axis="y", labelcolor="#c0392b")
ax.axvline(15, color="#888", ls="-.", lw=1, label="ausgeliefert: 15")
ax.set_title(f"v4: flood.max-Sweep auf realer Topologie (P90-Durchmesser {diam_p90:.0f} Hops)")
ax.grid(alpha=0.25)
lines = ax.get_lines() + ax2.get_lines()
ax.legend(lines, [l.get_label() for l in lines], fontsize=8, loc="center right")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_v4_floodmax_sweep.png")); plt.close(fig)

# fig_v4_adoption_safety — Safety-Matrix (Mechanismus x alpha), Top-Traffic
ro = "top_traffic"
alphas_str = [str(a) for a in ALPHAS]
M = np.zeros((len(MECHS), len(alphas_str)))
for mi, mech in enumerate(MECHS):
    for ai, a in enumerate(alphas_str):
        rows = [r for r in sweep if r["mech"] == mech and r["rollout"] == ro
                and str(r["alpha"]) == a]
        if rows:
            r = rows[0]
            if not r["safe"]:
                M[mi, ai] = 0.0
            elif r["airtime_vs_base_pct"] < -1.0:
                M[mi, ai] = 1.0
            else:
                M[mi, ai] = 0.5
fig, ax = plt.subplots(figsize=(8.8, 5.6))
cmap = matplotlib.colors.ListedColormap(["#c0392b", "#f1c40f", "#2e7d32"])
ax.imshow(M, cmap=cmap, vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(len(alphas_str))); ax.set_xticklabels(alphas_str, rotation=45, ha="right")
ax.set_yticks(range(len(MECHS))); ax.set_yticklabels(MECHS)
ax.set_xlabel("Adoptionsanteil α")
ax.set_title("v4: Safety-Matrix (grün=Gewinn&safe, gelb=safe/neutral, rot=Verletzung)\n"
             "Top-Traffic-Rollout, reale Topologie")
for mi in range(len(MECHS)):
    for ai in range(len(alphas_str)):
        rows = [r for r in sweep if r["mech"] == MECHS[mi] and r["rollout"] == ro
                and str(r["alpha"]) == alphas_str[ai]]
        if rows:
            ax.text(ai, mi, f"{rows[0]['airtime_vs_base_pct']:+.0f}", ha="center",
                    va="center", fontsize=6.5,
                    color="white" if M[mi, ai] != 0.5 else "black")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_v4_adoption_safety.png")); plt.close(fig)

# fig_v4_snr_vs_reliability
fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.6))
axs[0].scatter(snr_vs_hops_x, snr_vs_hops_y, s=10, alpha=0.4, color="#34495e")
axs[0].set_xlabel("Mittleres echtes Per-Link-SNR entlang MHR-Pfad (dB)")
axs[0].set_ylabel("Hop-Zahl des MHR-Pfads")
axs[0].set_title(f"SNR vs. Pfad-Kuerze\ncorr={snr_hop_corr:+.2f} "
                 f"(SNR erklaert Hopzahl {'kaum' if abs(snr_hop_corr)<0.3 else 'teilweise'})")
axs[0].grid(alpha=0.25)
axs[1].hist(etx_vs_hop_better_rel, bins=30, color="#2e7d32", alpha=0.8)
axs[1].axvline(0, color="k", ls="--", lw=1)
axs[1].set_xlabel("Reliability(ETX-Pfad) − Reliability(Hop-Pfad)")
axs[1].set_ylabel("Anzahl Paare")
axs[1].set_title(f"ETX-Pfad-Vorteil ggü. reinem Hop-Pfad\n"
                 f"Ø +{snr_findings['etx_vs_hop_rel_gain_mean']:.3f}, "
                 f"+{snr_findings['etx_vs_hop_extra_hops_mean']:.2f} Hops")
axs[1].grid(alpha=0.25)
fig.suptitle("v4: SNR-Frage neu — echtes Per-Link-SNR vs. Pfad-Kuerze / Reliability")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_v4_snr_vs_reliability.png")); plt.close(fig)

log("  Plots: fig_v4_topology.png, fig_v4_baseline_vs_mhr.png, fig_v4_floodmax_sweep.png, "
    "fig_v4_adoption_safety.png, fig_v4_snr_vs_reliability.png")

# Kennzahlen fuer den Markdown-Bericht zurueckgeben (per stdout-Markierung)
log("\n=== KENNZAHLEN-DUMP (fuer Bericht) ===")
log(json.dumps({
    "core_nodes": G.number_of_nodes(), "core_edges": G.number_of_edges(),
    "giant": len(giant), "avg_deg": round(statistics.mean(degs), 2),
    "base_deliv": round(BASE_DELIV, 3), "base_air": round(BASE_AIR_M, 1),
    "mhr_air": round(mhr_summary["mean_mhr_airtime"], 2),
    "airtime_red": round(mhr_summary["airtime_reduction_pct"], 1),
    "detour_b_over_mhr_med": round(mhr_summary["median_detour_base_over_mhr"], 2),
    "rel_base": round(mhr_summary["mean_base_reliability"], 3),
    "rel_mhr": round(mhr_summary["mean_mhr_reliability"], 3),
    "snr_hop_corr": round(snr_hop_corr, 2),
    "etx_rel_gain": round(snr_findings["etx_vs_hop_rel_gain_mean"], 3),
    "diam_p90": diam_p90,
}, ensure_ascii=False))
log("\n=== FERTIG ===")
