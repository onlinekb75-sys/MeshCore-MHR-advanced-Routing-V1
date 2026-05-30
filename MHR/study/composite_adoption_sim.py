#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — INTEGRATIVE Abschluss-Simulation: KOMPOSIT-Adoption der gesamten
node-lokalen MHR-Optimierungs-Schicht auf der ECHTEN CoreScope-Topologie
================================================================================

Frage:
  Wie verbessert sich das Netz, wenn 1 / 10 / 25 / 50 / 75 / 100 % der Knoten die
  GESAMTE node-lokale MHR-Schicht (alle vier Mechanismen GEMEINSAM) nutzen — vs.
  0 % (reine Upstream-Baseline)? Ehrlich, auf echten Daten, >=5 Seeds.

Die node-lokale MHR-Schicht (KOMBINIERT auf Neu-Firmware-Knoten):
  M1  Hop-gewichtetes Flood-Rebroadcast-Delay (kuerzere Pfade fuehren / senden frueher)
  M2  flood.max = 15 (Hop-Limit, lokal; Upstream = 64)
  M3  guarded Suppression (G1-G5, sicherer Satz: k_cover=2, min_degree=3, snr_floor=-6, prob=0.8)
  M4  Pfad-Erfolgs-Reinforcement (EWMA-Erfolgsmass + passiv gelernter Backup + proaktiver
      Backup-Switch statt teurer Re-Discovery-Flood)
  Stock-Knoten verhalten sich EXAKT wie Upstream (first-wins-Flood, voller Jitter,
  flood.max=64, keine Suppression, kein Reinforcement).

WIEDERVERWENDUNG (kein Neubau): Kernfunktionen aus
  - docs/MHR/sim/mhr_sim_real_v4.py    (Topologie, hop-gewichtetes Timing, flood.max, Metriken)
  - docs/MHR/study/suppression_sim.py  (guarded Suppression G1-G5)
  - docs/MHR/study/reinforce_sim.py    (Pfad-Reinforcement, EWMA + Backup-Switch)
sind hier adaptiert/zusammengefuehrt. Identische Physik (SNR-Reliability), identisches
Flood-Modell (Airtime = sendende Knoten), identische Rollout-Logik -> Vergleichbarkeit.

Zwei messbare Ebenen (die MHR-Schicht wirkt auf beiden):
  (A) SINGLE-DELIVERY-FLOOD  : ein Flood je Paar. Hier wirken M1+M2+M3 (Timing/Limit/
      Suppression). Metriken: Airtime, Lieferquote, Hops, Detour, Routen-Stabilitaet.
  (B) MULTI-TICK-UNICAST     : wiederholte Zustellung ueber gecachte Pfade unter
      Linkausfall/Churn. Hier wirkt zusaetzlich M4 (Reinforcement) gegen Re-Discovery-
      Floods. Metrik: Netto-Airtime (Unicast + Re-Floods), Lieferquote, Re-Floods.
  Der EHRLICHE Komposit-Effekt = gemessen mit ALLEN Mechanismen gemeinsam (nicht Summe
  der Einzelgewinne). Die Beitrags-Zerlegung (welcher Mechanismus traegt wieviel)
  erfolgt durch kumulatives Zuschalten: M2 -> +M1 -> +M3 -> +M4 (=Voll-Komposit).
  Interaktion = Komposit minus Summe der inkrementellen Einzelbeitraege.

Aufruf:  python3 composite_adoption_sim.py
Env (Debug): COMP_FAST=1, COMP_PAIRS, COMP_SEEDS, COMP_TICKS
"""

import json
import math
import os
import heapq
import zlib
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
SIM = os.path.join(os.path.dirname(HERE), "sim")
DATA = os.path.join(SIM, "data")
NG_F = os.path.join(DATA, "neighbor_graph.json")
NODES_F = os.path.join(DATA, "nodes.json")
CAL_F = os.path.join(DATA, "snr_calibration.json")

MASTER_SEED = 42
FAST = os.environ.get("COMP_FAST", "0") == "1"
# begrenzte, transparent geloggte Paaranzahl/Seeds (Effizienz)
N_PAIRS = int(os.environ.get("COMP_PAIRS", "120" if not FAST else "20"))
N_SEEDS = int(os.environ.get("COMP_SEEDS", "6" if not FAST else "2"))     # >=5 fuer Zufall
# Multi-Tick (Reinforcement-Ebene)
N_SRC = int(os.environ.get("COMP_SRC", "32" if not FAST else "6"))
N_DEST = int(os.environ.get("COMP_DESTS", "4" if not FAST else "2"))
N_TICKS = int(os.environ.get("COMP_TICKS", "50" if not FAST else "12"))

# Physik / LoRa (identisch v4/supp/reinforce -> Vergleichbarkeit)
SNR_THR = -12.0
SNR_SCALE = 4.0

# Flood-Timing-Modell (identisch v4)
FLOOD_MAX_BASE = 64
FLOOD_MAX_MHR = 15            # M2 (Stufe-A-Wert)
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0
TX_HOP_WEIGHT = 0.6           # M1 hop-gewichtetes Delay

# M3 guarded Suppression — sicherer Satz (aus suppression_sim Sweet-Spot-Logik)
SUPP_PARAMS = dict(min_degree=3, k_cover=2, snr_floor=-6.0, prob=0.8)
GUARDS_ALL = {"G1", "G2", "G3", "G4", "G5"}

# M4 Reinforcement-Parameter (aus reinforce_sim)
EWMA_ALPHA = 0.30
SWITCH_THR = 0.55
HARD_FAIL_LIMIT = 3
S_INIT = 1.0
LEARN_LOSS = 0.30            # passives Lernen unvollstaendig (Realismus)


def log(msg):
    print(msg, flush=True)


def shash(x):
    return zlib.crc32(repr(x).encode("utf-8"))


# ======================================================================================
# 1) REALE TOPOLOGIE (echte Kanten + echtes Per-Link-SNR) — wie v4/supp/reinforce
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json (wie v4/supp/reinforce) ===")
ng = json.load(open(NG_F))
cal = json.load(open(CAL_F))
SNR_THR = float(cal.get("snr_threshold_db", SNR_THR))


def snr_reliability(snr_db):
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / SNR_SCALE)), 0.02, 0.995))


G = nx.Graph()
SNR = {}
PREL = {}
ETXW = {}
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
    etx = 1.0 / max(pr * pr, 1e-4)
    G.add_edge(u, v, snr=float(s), prel=pr, etx=etx)
    SNR[(u, v)] = SNR[(v, u)] = float(s)
    PREL[(u, v)] = PREL[(v, u)] = pr
    ETXW[(u, v)] = ETXW[(v, u)] = etx
    snr_used.append(float(s))

for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

# Anreicherung: advert_count (Churn-Profil) + relay_count (Top-Traffic-Rollout)
NODE = {}
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    NODE[pk] = {"advert": x.get("advert_count", 0) or 0,
                "neighbor_count": x.get("neighbor_count", 0) or 0,
                "relay24": 0, "traffic": 0.0}
adv_raw = {}
try:
    nd = json.load(open(NODES_F))["nodes"]
    for x in nd:
        pk = (x.get("public_key") or "").lower()
        if pk:
            adv_raw[pk] = int(x.get("advert_count", 0) or 0)
            if pk in NODE:
                NODE[pk]["relay24"] = x.get("relay_count_24h", 0) or 0
                NODE[pk]["traffic"] = x.get("traffic_share_score", 0) or 0.0
except Exception as ex:
    log(f"  WARN nodes.json nicht ladbar ({ex})")

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)
giant_set = set(giant_list)
DEG = dict(GC.degree())
ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}
NBR = {u: set(GC.neighbors(u)) for u in GC.nodes()}

log(f"  Kern-Graph: {G.number_of_nodes()} Knoten, {G.number_of_edges()} Kanten, "
    f"ambiguous verworfen: {n_ambig}")
log(f"  Grad min/median/mean/max: {min(degs)}/{statistics.median(degs):.0f}/"
    f"{statistics.mean(degs):.2f}/{max(degs)}")
log(f"  Riesenkomponente (Simulation): {GC.number_of_nodes()} Knoten, "
    f"{GC.number_of_edges()} Kanten")
if snr_used:
    sa = np.array(snr_used)
    log(f"  Per-Link-SNR (echt): Median {np.median(sa):.2f} dB, "
        f"Reliability-Median {snr_reliability(float(np.median(sa))):.2f}")

# Churn-Profil aus advert_count
adv_vals = np.array([adv_raw.get(pk, 0) for pk in giant_list], dtype=float)
adv_p90 = max(float(np.percentile(adv_vals, 90)) if len(adv_vals) else 1.0, 1.0)


def churn_off_prob(pk, churn_scale):
    a = adv_raw.get(pk, 0)
    rel_stab = min(1.0, a / adv_p90)
    return churn_scale * (1.0 - rel_stab)


# ======================================================================================
# 2) GEMEINSAMES FLOOD-MODELL (Ebene A): M1 + M2 + M3 zusammen je nach Mechanismus-Maske
# ======================================================================================
# mech_mask: Menge aus {"M1","M2","M3"} -> welche Flood-Mechanismen ein MHR-Knoten nutzt.
# Voll-Komposit (Ebene A) = {"M1","M2","M3"}.
def rebroadcast_delay(hops, is_mhr_m1, rstate):
    """Stock / ohne M1: Basis + voller Jitter. M1: hop-gewichtet -> weniger Hops = frueher."""
    air = BASE_AIR + PER_HOP_AIR * hops
    if is_mhr_m1:
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


def guards_pass(R, cover_senders, params, rng):
    """G1-G5 guarded Suppression (aus suppression_sim, voller Guard-Satz, perfektes
    2-Hop-Wissen NBR). Schweigen NUR wenn alle Guards erfuellt; sonst senden (sicher)."""
    # G1 Low-Degree / Leaf-Schutz
    if DEG.get(R, 0) < params["min_degree"]:
        return False
    # G2 Cover-Count
    if len(cover_senders) < params["k_cover"]:
        return False
    # G4 Reliability-Floor
    good = [x for x in cover_senders if SNR.get((R, x), -99.0) >= params["snr_floor"]]
    if len(good) < params["k_cover"]:
        return False
    cover_eff = set(good)
    # G3 Neighbour-Coverage: jeder Nachbar von R ist Nachbar >=1 Cover-Senders
    covered = set()
    for x in cover_eff:
        covered |= NBR.get(x, set())
        covered.add(x)
    for nb in NBR.get(R, set()):
        if nb == R:
            continue
        if nb not in covered:
            return False
    # G5 Prob-Margin
    if rng.random() >= params["prob"]:
        return False
    return True


def run_flood(src, dst, mhr_set, mech_mask, rstate, dead_nodes=None, dead_edges=None):
    """Timing-getriebener Flood auf realer Topologie mit der KOMBINIERTEN MHR-Schicht
    (M1/M2/M3 je nach mech_mask) auf den MHR-Knoten. Rueckgabe: (delivered, used_path, n_tx).
    n_tx = Airtime (Anzahl tatsaechlich sendender Knoten)."""
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)
    use_m1 = "M1" in mech_mask
    use_m2 = "M2" in mech_mask
    use_m3 = "M3" in mech_mask

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    accepted = {src: (0, None)}
    acc_hops = {src: 0}
    sent = set()
    cover_of = collections.defaultdict(set)
    dst_paths = []
    seq = 0
    pq = [(0.0, 0, src, 0, None)]

    while pq:
        t_send, _, u, hops_u, prev_u = heapq.heappop(pq)
        is_mhr = u in mhr_set
        node_max = FLOOD_MAX_MHR if (is_mhr and use_m2) else FLOOD_MAX_BASE
        if hops_u >= node_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:
            continue
        # M3 Suppression-Entscheidung (nur MHR-Knoten, nicht src/dst)
        if use_m3 and is_mhr and u != src and u != dst:
            if guards_pass(u, cover_of.get(u, set()), SUPP_PARAMS, rstate):
                continue  # R schweigt
        sent.add(u)
        out_hops = hops_u + 1
        rnd = rstate.random
        for v, pr in ADJ[u]:
            if has_dead and not link_ok(u, v):
                continue
            if rnd() > pr:
                continue
            cover_of[v].add(u)
            if v == dst:
                p = reconstruct_path(accepted, u, src) + [v]
                dst_paths.append((out_hops, p))
            if v not in accepted:
                accepted[v] = (out_hops, u)
                acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, (v in mhr_set) and use_m1, rstate)
                seq += 1
                heapq.heappush(pq, (t_send + d, seq, v, out_hops, u))

    delivered = dst in accepted or len(dst_paths) > 0
    if not delivered:
        return False, None, len(sent)
    used = reconstruct_path(accepted, dst, src)
    return True, used, len(sent)


# ======================================================================================
# 3) ROLLOUT / PAARE
# ======================================================================================
# Top-Traffic-Repeater zuerst: relay_count_24h, dann advert, dann Grad
top_traffic_order = sorted(
    giant_list,
    key=lambda pk: (NODE.get(pk, {}).get("relay24", 0),
                    NODE.get(pk, {}).get("advert", 0),
                    DEG.get(pk, 0)),
    reverse=True)


def select_mhr(alpha, rollout, seed):
    rstate = np.random.default_rng(seed * 100003 + 7)
    n = len(giant_list)
    if alpha == 0.0:
        return set()
    k = 1 if alpha == "1node" else max(1, int(round(alpha * n)))
    if rollout == "top_traffic":
        return set(top_traffic_order[:k])
    idx = rstate.choice(n, size=min(k, n), replace=False)
    return set(giant_list[i] for i in idx)


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


# ======================================================================================
# 4) EBENE-A-EVALUATION (Single-Delivery-Flood): Airtime/Delivery/Hops/Detour/Stabilitaet
# ======================================================================================
def evaluate_floodlevel(alpha, rollout, mech_mask, seeds, n_pairs,
                        dead_edges_frac=0.0, churn=False, stress_seed_off=0):
    """Ein Flood je Paar. mech_mask leer -> Baseline-Verhalten (alle Stock).
    Misst Airtime, Lieferquote, Hops, Detour, Routen-Stabilitaet (gleicher Pfad ueber Seeds)."""
    air_list, deliv_list, hop_list, detour_list, rel_list = [], [], [], [], []
    pair_paths = collections.defaultdict(list)
    per_seed_deliv, per_seed_air = [], []

    for si, seed in enumerate(seeds):
        seed_deliv, seed_air = [], []
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503 + stress_seed_off)
        mhr_set = select_mhr(alpha, rollout, seed) & giant_set
        dead_nodes, dead_edges = set(), set()
        if churn:
            order = sorted(giant_list, key=lambda pk: NODE.get(pk, {}).get("advert", 0))
            k = int(0.05 * len(order))
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
            ok, used, ntx = run_flood(s, d, mhr_set, mech_mask, rstate,
                                      dead_nodes=dead_nodes, dead_edges=dead_edges)
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
        "alpha": alpha, "rollout": rollout, "n_obs": len(deliv_list),
        "delivery": safem(deliv_list),
        "airtime_mean": safem(air_list),
        "airtime_median": float(np.median(air_list)) if air_list else 0.0,
        "hops_mean": safem(hop_list),
        "hops_median": float(np.median(hop_list)) if hop_list else 0.0,
        "detour_mean": safem(detour_list),
        "detour_median": float(np.median(detour_list)) if detour_list else 0.0,
        "reliability_mean": safem(rel_list),
        "route_stability": route_stability,
        "deliv_sem": (float(np.std(per_seed_deliv, ddof=1) / math.sqrt(len(per_seed_deliv)))
                      if len(per_seed_deliv) >= 2 else 0.0),
        "air_sem": (float(np.std(per_seed_air, ddof=1) / math.sqrt(len(per_seed_air)))
                    if len(per_seed_air) >= 2 else 0.0),
    }


# ======================================================================================
# 5) EBENE B (Multi-Tick-Unicast + M4 Reinforcement) — aus reinforce_sim adaptiert
# ======================================================================================
def compute_paths(src, dst):
    try:
        primary = nx.shortest_path(GC, src, dst, weight="etx")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, None
    H = GC.copy()
    H.remove_edges_from(list(zip(primary, primary[1:])))
    try:
        backup = nx.shortest_path(H, src, dst, weight="etx")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        backup = None
    return primary, backup


def unicast_attempt(path, dead_nodes, dead_edges, rstate):
    if not path or len(path) < 2:
        return (path is not None and len(path) == 1), 0
    tx = 0
    for a, b in zip(path, path[1:]):
        tx += 1
        if b in dead_nodes or frozenset((a, b)) in dead_edges:
            return False, tx
        if rstate.random() > PREL.get((a, b), 0.0):
            return False, tx
    return True, tx


def run_pair_multitick(src, dst, use_M4, rstate, linkfail, churn_scale, edge_index, learn_loss):
    """N_TICKS Sendeversuche src->dst. use_M4=False -> Baseline (first-wins-Cache, Re-Flood
    nach 3 Fehlern). use_M4=True -> EWMA + Backup-Switch (proaktiv vor Re-Flood).
    Re-Flood-Airtime mit der MHR-Flood-Schicht der QUELLE: wenn use_M4, nutzt die Quelle
    auch M1/M2 fuer ihren Re-Discovery-Flood (Komposit konsistent). Wir modellieren den
    Re-Flood hier vereinfacht mit flood_max der Quelle."""
    primary, backup_full = compute_paths(src, dst)
    if primary is None:
        return None
    backup = backup_full
    if use_M4:
        if backup_full is None or rstate.random() < learn_loss:
            backup = None

    cur_path = list(primary)
    cur_is_backup = False
    s_ewma = S_INIT
    consec_fail = 0
    unicast_air = 0.0
    reflood_air = 0.0
    delivered = 0
    switches = 0
    refloods = 0
    opt_hops = path_hops(primary)
    all_edges = edge_index
    flood_max = FLOOD_MAX_MHR if use_M4 else FLOOD_MAX_BASE

    def do_reflood(dead_nodes, dead_edges):
        # einfacher first-wins-Flood (Re-Discovery). Airtime = sendende Knoten.
        accepted = {src: (0, None)}
        sent = set()
        seq = 0
        pq = [(0.0, 0, src, 0)]
        dst_ok = False
        used = None
        while pq:
            t_send, _, u, hops_u = heapq.heappop(pq)
            if hops_u >= flood_max or u in sent:
                continue
            if u in dead_nodes:
                continue
            sent.add(u)
            oh = hops_u + 1
            for v, pr in ADJ[u]:
                if v in dead_nodes or frozenset((u, v)) in dead_edges:
                    continue
                if rstate.random() > pr:
                    continue
                if v == dst:
                    dst_ok = True
                    used = reconstruct_path(accepted, u, src) + [v]
                if v not in accepted:
                    accepted[v] = (oh, u)
                    d = BASE_AIR + PER_HOP_AIR * oh + rstate.uniform(0.0, JITTER * (BASE_AIR + PER_HOP_AIR * oh))
                    seq += 1
                    heapq.heappush(pq, (t_send + d, seq, v, oh))
        if dst in accepted and used is None:
            used = reconstruct_path(accepted, dst, src)
        return (dst_ok or dst in accepted), used, len(sent)

    for tick in range(N_TICKS):
        dead_edges = set()
        if linkfail > 0 and all_edges:
            k = int(linkfail * len(all_edges))
            if k > 0:
                idx = rstate.choice(len(all_edges), size=k, replace=False)
                dead_edges = set(all_edges[i] for i in idx)
        dead_nodes = set()
        if churn_scale > 0:
            cand = set(cur_path) | (set(backup) if backup else set()) | set(primary)
            for n in cand:
                if n == src:
                    continue
                if rstate.random() < churn_off_prob(n, churn_scale):
                    dead_nodes.add(n)

        if not use_M4:
            ok, tx = unicast_attempt(cur_path, dead_nodes, dead_edges, rstate)
            unicast_air += tx
            if ok:
                delivered += 1
                consec_fail = 0
            else:
                consec_fail += 1
                if consec_fail >= HARD_FAIL_LIMIT:
                    fok, fpath, ftx = do_reflood(dead_nodes, dead_edges)
                    reflood_air += ftx
                    refloods += 1
                    consec_fail = 0
                    if fok and fpath:
                        cur_path = fpath
                        delivered += 1
            continue

        # use_M4
        if (not cur_is_backup) and backup is not None and s_ewma < SWITCH_THR:
            cur_path = list(backup)
            cur_is_backup = True
            switches += 1
            s_ewma = S_INIT * 0.8
        ok, tx = unicast_attempt(cur_path, dead_nodes, dead_edges, rstate)
        unicast_air += tx
        s_ewma = (1 - EWMA_ALPHA) * s_ewma + EWMA_ALPHA * (1.0 if ok else 0.0)
        if ok:
            delivered += 1
            consec_fail = 0
        else:
            consec_fail += 1
            alt = backup if (not cur_is_backup) else primary
            switched_here = False
            if alt is not None and alt != cur_path:
                aok, atx = unicast_attempt(alt, dead_nodes, dead_edges, rstate)
                unicast_air += atx
                if aok:
                    cur_path = list(alt)
                    cur_is_backup = (not cur_is_backup)
                    switches += 1
                    switched_here = True
                    s_ewma = S_INIT * 0.8
                    delivered += 1
                    consec_fail = 0
            if not switched_here and consec_fail >= HARD_FAIL_LIMIT:
                fok, fpath, ftx = do_reflood(dead_nodes, dead_edges)
                reflood_air += ftx
                refloods += 1
                consec_fail = 0
                if fok and fpath:
                    cur_path = fpath
                    cur_is_backup = False
                    s_ewma = S_INIT
                    delivered += 1

    return {
        "unicast_air": unicast_air, "reflood_air": reflood_air,
        "total_air": unicast_air + reflood_air,
        "deliv_rate": delivered / N_TICKS,
        "switches": switches, "refloods": refloods,
        "has_backup": int(backup is not None),
    }


ALL_EDGES = [frozenset(e) for e in GC.edges()]


def make_sources(seed):
    rstate = np.random.default_rng(seed * 7919 + 13)
    idx = rstate.choice(len(giant_list), size=min(N_SRC, len(giant_list)), replace=False)
    return [giant_list[i] for i in idx]


def make_dests(src, seed):
    rstate = np.random.default_rng((seed * 104729 + shash(src)) % (2**63))
    pool = [p for p in giant_list if p != src]
    idx = rstate.choice(len(pool), size=min(N_DEST, len(pool)), replace=False)
    return [pool[i] for i in idx]


def evaluate_multitick(alpha, rollout, use_M4, linkfail, churn_scale, learn_loss, seeds):
    """Multi-Tick-Ebene. Ein Knoten nutzt M4 nur, wenn er Adopter ist (alpha-Auswahl) UND
    use_M4 True. Common Random Numbers gegen die Baseline (gleiche Quelle = Baseline-Modus)."""
    per_seed = []
    for si, seed in enumerate(seeds):
        adopters = select_mhr(alpha, rollout, seed) & giant_set
        sources = make_sources(seed)
        base_air = mode_air = 0.0
        base_deliv = mode_deliv = 0.0
        base_reflood = mode_reflood = 0.0
        base_refl_n = mode_refl_n = 0
        npair = 0
        for src in sources:
            for dst in make_dests(src, seed):
                is_adopter = (src in adopters) and use_M4
                crn = (seed * 2654435761 + si * 40503 + shash((src, dst))) % (2**63)
                rb = np.random.default_rng(crn)
                base = run_pair_multitick(src, dst, False, rb, linkfail, churn_scale,
                                          ALL_EDGES, learn_loss)
                rm = np.random.default_rng(crn)
                mres = run_pair_multitick(src, dst, is_adopter, rm, linkfail, churn_scale,
                                          ALL_EDGES, learn_loss)
                if base is None or mres is None:
                    continue
                npair += 1
                base_air += base["total_air"]; mode_air += mres["total_air"]
                base_deliv += base["deliv_rate"]; mode_deliv += mres["deliv_rate"]
                base_reflood += base["reflood_air"]; mode_reflood += mres["reflood_air"]
                base_refl_n += base["refloods"]; mode_refl_n += mres["refloods"]
        if npair == 0:
            continue
        per_seed.append({
            "base_air": base_air / npair, "mode_air": mode_air / npair,
            "base_deliv": base_deliv / npair, "mode_deliv": mode_deliv / npair,
            "base_reflood": base_reflood / npair, "mode_reflood": mode_reflood / npair,
            "base_refl_n": base_refl_n / npair, "mode_refl_n": mode_refl_n / npair,
        })

    def agm(k):
        v = [d[k] for d in per_seed]
        return float(np.mean(v)) if v else 0.0

    def asem(k):
        v = [d[k] for d in per_seed]
        return (float(np.std(v, ddof=1) / math.sqrt(len(v))) if len(v) >= 2 else 0.0)

    out = {"alpha": alpha, "linkfail": linkfail, "churn_scale": churn_scale,
           "n_seeds_used": len(per_seed)}
    for k in ("base_air", "mode_air", "base_deliv", "mode_deliv",
              "base_reflood", "mode_reflood", "base_refl_n", "mode_refl_n"):
        out[k] = agm(k)
    out["deliv_sem"] = asem("mode_deliv")
    out["base_deliv_sem"] = asem("base_deliv")
    out["air_net_pct"] = (100 * (out["mode_air"] - out["base_air"]) / out["base_air"]
                          if out["base_air"] > 0 else 0.0)
    out["reflood_saved_pct"] = (100 * (out["base_reflood"] - out["mode_reflood"])
                                / out["base_reflood"] if out["base_reflood"] > 0 else 0.0)
    out["deliv_delta"] = out["mode_deliv"] - out["base_deliv"]
    return out


# ======================================================================================
# 6) HAUPT-ABLAUF
# ======================================================================================
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
ADOPTION = [0.0, 0.01, 0.10, 0.25, 0.50, 0.75, 1.0]
ROLLOUTS = ["top_traffic", "random"]

# Kumulative Mechanismus-Masken fuer Ebene A (Beitrags-Zerlegung):
#   "base"   -> {}                  (Upstream)
#   "M2"     -> {M2}                (flood.max=15)
#   "M2_M1"  -> {M2,M1}             (+ hop-gewichtetes Timing)
#   "COMBI_A"-> {M2,M1,M3}          (+ guarded Suppression) = Voll-Komposit Ebene A
FLOOD_MASKS = collections.OrderedDict([
    ("M2", {"M2"}),
    ("M2_M1", {"M2", "M1"}),
    ("COMBI_A", {"M2", "M1", "M3"}),
])

results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS,
    "n_src": N_SRC, "n_dest": N_DEST, "n_ticks": N_TICKS, "fast": FAST,
    "topology": {
        "core_nodes": G.number_of_nodes(), "core_edges": G.number_of_edges(),
        "ambiguous_dropped": n_ambig,
        "giant_nodes": GC.number_of_nodes(), "giant_edges": GC.number_of_edges(),
        "avg_degree": float(statistics.mean(degs)),
        "degree_median": float(statistics.median(degs)),
        "link_snr_median_db": float(np.median(np.array(snr_used))) if snr_used else 0.0,
    },
    "mechanisms": {
        "M1": "hop-gewichtetes Flood-Rebroadcast-Delay (TX_HOP_WEIGHT=%.1f)" % TX_HOP_WEIGHT,
        "M2": "flood.max=%d (Upstream %d)" % (FLOOD_MAX_MHR, FLOOD_MAX_BASE),
        "M3": "guarded Suppression G1-G5 %s" % SUPP_PARAMS,
        "M4": "Pfad-Reinforcement (EWMA a=%.2f, switch_thr=%.2f, learn_loss=%.2f)"
              % (EWMA_ALPHA, SWITCH_THR, LEARN_LOSS),
    },
    "adoption_levels": [str(a) for a in ADOPTION],
}

# --- Baseline (0 %) auf Ebene A ---
log("\n=== Baseline (0 %, Upstream-Flood) — Ebene A ===")
base_A = evaluate_floodlevel(0.0, "random", set(), seeds, N_PAIRS)
BASE_DELIV = base_A["delivery"]
BASE_AIR = base_A["airtime_mean"]
BASE_HOPS = base_A["hops_mean"]
BASE_DETOUR = base_A["detour_median"]
BASE_STAB = base_A["route_stability"]
DELIV_TOL = max(2.0 * base_A["deliv_sem"], 0.005)
AIR_TOL = max(2.0 * base_A["air_sem"], 0.005 * BASE_AIR)
log(f"  Baseline: deliv={BASE_DELIV:.4f} air={BASE_AIR:.2f} hops={BASE_HOPS:.2f} "
    f"detour_med={BASE_DETOUR:.2f} stab={BASE_STAB:.3f}")
log(f"  Rausch-Band (2*SEM,min): deliv +-{DELIV_TOL:.4f}, air +-{AIR_TOL:.3f}")
results["baseline_floodlevel"] = base_A
results["safety_tolerance"] = {"delivery_tol": DELIV_TOL, "airtime_tol": AIR_TOL}

# --- 6a) KOMPOSIT-Adoptions-Sweep (Ebene A: Voll-Komposit M1+M2+M3) ueber beide Rollouts ---
log("\n=== 6a) KOMPOSIT-Adoptions-Sweep (Ebene A, M1+M2+M3) ===")
composite_sweep = {ro: [] for ro in ROLLOUTS}
for ro in ROLLOUTS:
    for a in ADOPTION:
        if a == 0.0:
            r = dict(base_A); r["rollout"] = ro
        else:
            r = evaluate_floodlevel(a, ro, FLOOD_MASKS["COMBI_A"], seeds, N_PAIRS)
        r["delivery_vs_base"] = r["delivery"] - BASE_DELIV
        r["delivery_vs_base_pct"] = (100 * r["delivery_vs_base"] / BASE_DELIV) if BASE_DELIV else 0.0
        r["airtime_vs_base_pct"] = (100 * (r["airtime_mean"] - BASE_AIR) / BASE_AIR) if BASE_AIR else 0.0
        r["hops_vs_base_pct"] = (100 * (r["hops_mean"] - BASE_HOPS) / BASE_HOPS) if BASE_HOPS else 0.0
        r["detour_vs_base_pct"] = (100 * (r["detour_median"] - BASE_DETOUR) / BASE_DETOUR) if BASE_DETOUR else 0.0
        r["stab_vs_base_pct"] = (100 * (r["route_stability"] - BASE_STAB) / BASE_STAB) if BASE_STAB else 0.0
        r["safe"] = bool((r["delivery"] >= BASE_DELIV - DELIV_TOL) and
                         (r["airtime_mean"] <= BASE_AIR + AIR_TOL))
        composite_sweep[ro].append(r)
        log(f"  [{ro:11s} a={str(a):5s}] deliv={r['delivery']:.4f} ({r['delivery_vs_base']:+.4f}) "
            f"air={r['airtime_mean']:6.2f} ({r['airtime_vs_base_pct']:+6.1f}%) "
            f"hops={r['hops_mean']:.2f} detour_med={r['detour_median']:.2f} "
            f"stab={r['route_stability']:.3f} SAFE={'OK' if r['safe'] else 'X'}")
results["composite_sweep"] = composite_sweep

# --- 6b) BEITRAGS-ZERLEGUNG (kumulatives Zuschalten) je Adoption, Ebene A, top_traffic ---
log("\n=== 6b) Beitrags-Zerlegung Ebene A (M2 -> +M1 -> +M3) ===")
contribution = []
for a in ADOPTION:
    entry = {"alpha": str(a)}
    prev_air = BASE_AIR
    prev_deliv = BASE_DELIV
    cum = {}
    for label, mask in FLOOD_MASKS.items():
        if a == 0.0:
            r = base_A
        else:
            r = evaluate_floodlevel(a, "top_traffic", mask, seeds, N_PAIRS)
        cum[label] = {
            "airtime_mean": r["airtime_mean"], "delivery": r["delivery"],
            "airtime_vs_base_pct": (100 * (r["airtime_mean"] - BASE_AIR) / BASE_AIR) if BASE_AIR else 0.0,
            # inkrementeller Airtime-Beitrag dieses Mechanismus (vs. vorherige Stufe)
            "incr_air_gain_pct": (100 * (prev_air - r["airtime_mean"]) / BASE_AIR) if BASE_AIR else 0.0,
            "incr_deliv_delta": r["delivery"] - prev_deliv,
        }
        prev_air = r["airtime_mean"]
        prev_deliv = r["delivery"]
    entry["cumulative"] = cum
    contribution.append(entry)
    log(f"  a={str(a):5s} | M2 air%={cum['M2']['airtime_vs_base_pct']:+5.1f} "
        f"(+M1 incr {cum['M2_M1']['incr_air_gain_pct']:+5.1f}) "
        f"(+M3 incr {cum['COMBI_A']['incr_air_gain_pct']:+5.1f}) "
        f"=> Komposit air%={cum['COMBI_A']['airtime_vs_base_pct']:+5.1f}")
results["contribution_decomp"] = contribution

# --- 6c) INTERAKTION: Komposit vs. Summe der inkrementellen Einzelbeitraege (top_traffic, a=1.0) ---
log("\n=== 6c) Interaktion (Komposit vs. Summe Einzel-Mechanismen, a=1.0) ===")
# Einzel-Mechanismen ISOLIERT (jeweils nur 1 Mechanismus an) bei a=1.0
iso = {}
for label, mask in [("M2_only", {"M2"}), ("M1_only", {"M1"}), ("M3_only", {"M3"})]:
    r = evaluate_floodlevel(1.0, "top_traffic", mask, seeds, N_PAIRS)
    iso[label] = {"airtime_vs_base_pct": (100 * (r["airtime_mean"] - BASE_AIR) / BASE_AIR) if BASE_AIR else 0.0,
                  "delivery": r["delivery"], "airtime_mean": r["airtime_mean"]}
combi_r = next(x for x in composite_sweep["top_traffic"] if str(x["alpha"]) == "1.0")
sum_isolated = sum(iso[k]["airtime_vs_base_pct"] for k in iso)
composite_actual = combi_r["airtime_vs_base_pct"]
interaction = composite_actual - sum_isolated
results["interaction_a1"] = {
    "isolated": iso, "sum_isolated_air_pct": sum_isolated,
    "composite_actual_air_pct": composite_actual,
    "interaction_air_pct": interaction,
    "note": "interaction<0 => Mechanismen verstaerken sich (Komposit besser als Summe); "
            ">0 => sie daempfen sich (Ueberlappung).",
}
log(f"  Isoliert (a=1.0): M2={iso['M2_only']['airtime_vs_base_pct']:+.1f}% "
    f"M1={iso['M1_only']['airtime_vs_base_pct']:+.1f}% M3={iso['M3_only']['airtime_vs_base_pct']:+.1f}%")
log(f"  Summe isoliert = {sum_isolated:+.1f}% | Komposit tatsaechlich = {composite_actual:+.1f}% "
    f"| INTERAKTION = {interaction:+.1f}% "
    f"({'verstaerkend' if interaction < 0 else 'daempfend/ueberlappend'})")

# --- 6d) UNTER STOERUNG (Churn + Linkausfall 10/20 %), Ebene A, top_traffic ---
log("\n=== 6d) Komposit unter Stoerung (Churn + Linkausfall 10/20 %) — Ebene A ===")
stress_A = {}
stress_cases = [
    ("churn", dict(churn=True)),
    ("link_fail_10", dict(dead_edges_frac=0.10)),
    ("link_fail_20", dict(dead_edges_frac=0.20)),
    ("churn+link20", dict(churn=True, dead_edges_frac=0.20)),
]
for label, kw in stress_cases:
    bsl = evaluate_floodlevel(0.0, "random", set(), seeds, N_PAIRS, **kw)
    rows = []
    for a in [0.50, 1.0]:
        r = evaluate_floodlevel(a, "top_traffic", FLOOD_MASKS["COMBI_A"], seeds, N_PAIRS, **kw)
        r["delivery_vs_base"] = r["delivery"] - bsl["delivery"]
        r["airtime_vs_base_pct"] = (100 * (r["airtime_mean"] - bsl["airtime_mean"]) /
                                    bsl["airtime_mean"]) if bsl["airtime_mean"] else 0.0
        r["safe"] = bool((r["delivery"] >= bsl["delivery"] - DELIV_TOL) and
                         (r["airtime_mean"] <= bsl["airtime_mean"] + AIR_TOL))
        rows.append(r)
    stress_A[label] = {"baseline": {"delivery": bsl["delivery"], "airtime_mean": bsl["airtime_mean"]},
                       "rows": rows}
    for r in rows:
        log(f"  {label:13s} a={r['alpha']:<4} base_deliv={bsl['delivery']:.3f} "
            f"c_deliv={r['delivery']:.3f} ({r['delivery_vs_base']:+.4f}) "
            f"air {bsl['airtime_mean']:.1f}->{r['airtime_mean']:.1f} "
            f"({r['airtime_vs_base_pct']:+.1f}%) SAFE={'OK' if r['safe'] else 'X'}")
results["stress_floodlevel"] = stress_A

# --- 6e) M4-Ebene (Multi-Tick-Reinforcement): Komposit-Adoption ueber Linkausfall ---
log("\n=== 6e) M4-Reinforcement (Multi-Tick) — Adoptions-Sweep ueber Linkausfall ===")
reinforce_sweep = []
for lf in [0.10, 0.20]:
    for a in ADOPTION:
        r = evaluate_multitick(a, "top_traffic", True, lf, 0.0, LEARN_LOSS, seeds)
        reinforce_sweep.append(r)
        if a in (1.0, 0.10, 0.0):
            log(f"  lf={lf:.0%} a={str(a):5s}: deliv {r['base_deliv']:.3f}->{r['mode_deliv']:.3f} "
                f"({r['deliv_delta']:+.4f}) air net {r['air_net_pct']:+.1f}% "
                f"reflood saved {r['reflood_saved_pct']:+.1f}%")
results["reinforce_sweep"] = reinforce_sweep

# M4 unter Churn bei voller Adoption
log("\n=== 6e2) M4 unter Churn (voll adoptiert) ===")
reinforce_churn = []
for cs in [0.10, 0.20]:
    r = evaluate_multitick(1.0, "top_traffic", True, 0.10, cs, LEARN_LOSS, seeds)
    reinforce_churn.append(r)
    log(f"  churn={cs:.0%}: deliv {r['base_deliv']:.3f}->{r['mode_deliv']:.3f} "
        f"({r['deliv_delta']:+.4f}) air net {r['air_net_pct']:+.1f}% "
        f"reflood saved {r['reflood_saved_pct']:+.1f}%")
results["reinforce_churn"] = reinforce_churn

# ======================================================================================
# 7) AUSWERTUNG: Monotonie, Safety, Wendepunkt
# ======================================================================================
log("\n=== 7) Monotonie / Safety / Wendepunkt ===")
ct = composite_sweep["top_traffic"]
# Monotonie der Airtime-Ersparnis (immer staerker negativ mit steigender Adoption?)
air_pcts = [r["airtime_vs_base_pct"] for r in ct]
monotone_air = all(air_pcts[i + 1] <= air_pcts[i] + 0.5 for i in range(len(air_pcts) - 1))
# Safety bei JEDER Stufe (Lieferquote >= Baseline - Tol)?
safety_each = all(r["delivery"] >= BASE_DELIV - DELIV_TOL for r in ct)
worst_deliv = min(r["delivery_vs_base"] for r in ct)
# "Lohnt sich"-Schwelle: erste Adoption mit >=2 % Airtime-Ersparnis
worth_threshold = None
for r in ct:
    if r["alpha"] != 0.0 and r["airtime_vs_base_pct"] <= -2.0:
        worth_threshold = str(r["alpha"])
        break
# Wendepunkt = Adoption mit groesstem marginalem Airtime-Gewinn (steilster Abschnitt)
marg = []
for i in range(1, len(ct)):
    marg.append((str(ct[i]["alpha"]), air_pcts[i] - air_pcts[i - 1]))
inflection = min(marg, key=lambda kv: kv[1]) if marg else (None, 0.0)

results["analysis"] = {
    "monotone_airtime_savings": bool(monotone_air),
    "safety_each_level": bool(safety_each),
    "worst_delivery_vs_base": worst_deliv,
    "worth_threshold_alpha": worth_threshold,
    "inflection_alpha": inflection[0],
    "inflection_marginal_air_pct": inflection[1],
    "air_pct_per_alpha_top_traffic": {str(ct[i]["alpha"]): air_pcts[i] for i in range(len(ct))},
}
log(f"  Monotone Airtime-Ersparnis (top_traffic): {monotone_air}")
log(f"  Lieferquote >= Baseline bei JEDER Stufe: {safety_each} (worst Δ={worst_deliv:+.4f})")
log(f"  'Lohnt sich'-Schwelle (>=2% Airtime gespart): a={worth_threshold}")
log(f"  Wendepunkt (steilster Airtime-Gewinn): a={inflection[0]} ({inflection[1]:+.1f} pp)")

# ======================================================================================
# 8) JSON SCHREIBEN (inkrementell — bereits hier)
# ======================================================================================
RES_F = os.path.join(HERE, "composite_adoption_results.json")
json.dump(results, open(RES_F, "w"), indent=2, ensure_ascii=False)
log(f"\n  composite_adoption_results.json geschrieben ({os.path.getsize(RES_F)} B).")

# ======================================================================================
# 9) PLOTS
# ======================================================================================
log("\n=== 9) Plots schreiben ===")
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})
alpha_x = [str(a) for a in ADOPTION]
xpos = list(range(len(ADOPTION)))

# fig_comp_airtime_vs_adoption
fig, ax = plt.subplots(figsize=(8.2, 5.2))
for ro, col, mk in [("top_traffic", "#2e7d32", "o-"), ("random", "#2980b9", "s--")]:
    y = [r["airtime_vs_base_pct"] for r in composite_sweep[ro]]
    ax.plot(xpos, y, mk, color=col, label=f"Komposit ({ro})")
ax.axhline(0, color="#888", lw=0.8)
ax.axhline(-2, color="#c0392b", ls=":", lw=1, label="'lohnt sich'-Schwelle (−2%)")
ax.set_xticks(xpos); ax.set_xticklabels(alpha_x)
ax.set_xlabel("Adoptionsanteil α"); ax.set_ylabel("Airtime Δ% vs. Baseline (0 %)")
ax.set_title("Komposit-Schicht: Airtime-Ersparnis über Adoption\n(Ebene A: Single-Flood, M1+M2+M3)")
ax.legend(fontsize=8); ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_comp_airtime_vs_adoption.png")); plt.close(fig)

# fig_comp_delivery_reliability
fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.8))
for ro, col, mk in [("top_traffic", "#2e7d32", "o-"), ("random", "#2980b9", "s--")]:
    axs[0].plot(xpos, [r["delivery"] for r in composite_sweep[ro]], mk, color=col, label=ro)
axs[0].axhline(BASE_DELIV, color="#333", ls=":", lw=1, label=f"Baseline {BASE_DELIV:.3f}")
axs[0].set_xticks(xpos); axs[0].set_xticklabels(alpha_x)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Lieferquote")
axs[0].set_title("Lieferquote (Safety: ≥ Baseline)"); axs[0].legend(fontsize=8); axs[0].grid(alpha=0.25)
for ro, col, mk in [("top_traffic", "#2e7d32", "o-"), ("random", "#2980b9", "s--")]:
    axs[1].plot(xpos, [r["reliability_mean"] for r in composite_sweep[ro]], mk, color=col, label=ro)
axs[1].axhline(base_A["reliability_mean"], color="#333", ls=":", lw=1,
               label=f"Baseline {base_A['reliability_mean']:.3f}")
axs[1].set_xticks(xpos); axs[1].set_xticklabels(alpha_x)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Ø Pfad-Reliability")
axs[1].set_title("Pfad-Reliability über Adoption"); axs[1].legend(fontsize=8); axs[1].grid(alpha=0.25)
fig.suptitle("Komposit-Schicht: Lieferquote & Reliability über Adoption")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_comp_delivery_reliability.png")); plt.close(fig)

# fig_comp_hops_detour
fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.8))
for ro, col, mk in [("top_traffic", "#2e7d32", "o-"), ("random", "#2980b9", "s--")]:
    axs[0].plot(xpos, [r["hops_mean"] for r in composite_sweep[ro]], mk, color=col, label=ro)
axs[0].axhline(BASE_HOPS, color="#333", ls=":", lw=1, label=f"Baseline {BASE_HOPS:.2f}")
axs[0].set_xticks(xpos); axs[0].set_xticklabels(alpha_x)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Ø Hops")
axs[0].set_title("Mittlere Hop-Zahl über Adoption"); axs[0].legend(fontsize=8); axs[0].grid(alpha=0.25)
for ro, col, mk in [("top_traffic", "#2e7d32", "o-"), ("random", "#2980b9", "s--")]:
    axs[1].plot(xpos, [r["detour_median"] for r in composite_sweep[ro]], mk, color=col, label=ro)
axs[1].axhline(BASE_DETOUR, color="#333", ls=":", lw=1, label=f"Baseline {BASE_DETOUR:.2f}")
axs[1].set_xticks(xpos); axs[1].set_xticklabels(alpha_x)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Detour-Ratio (Median)")
axs[1].set_title("Detour-Ratio (Pfad/Kürzest) über Adoption"); axs[1].legend(fontsize=8); axs[1].grid(alpha=0.25)
fig.suptitle("Komposit-Schicht: Hops & Detour über Adoption")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_comp_hops_detour.png")); plt.close(fig)

# fig_comp_contribution — gestapelte inkrementelle Airtime-Beitraege je Adoption
fig, ax = plt.subplots(figsize=(9.0, 5.4))
labels_c = [c["alpha"] for c in contribution]
m2_gain = [-c["cumulative"]["M2"]["airtime_vs_base_pct"] for c in contribution]  # >0 = Ersparnis
m1_gain = [c["cumulative"]["M2_M1"]["incr_air_gain_pct"] for c in contribution]
m3_gain = [c["cumulative"]["COMBI_A"]["incr_air_gain_pct"] for c in contribution]
xc = np.arange(len(labels_c))
ax.bar(xc, m2_gain, label="M2 (flood.max=15)", color="#2980b9")
ax.bar(xc, m1_gain, bottom=m2_gain, label="M1 (hop-Timing)", color="#e67e22")
ax.bar(xc, m3_gain, bottom=[a + b for a, b in zip(m2_gain, m1_gain)],
       label="M3 (guarded Suppression)", color="#2e7d32")
ax.set_xticks(xc); ax.set_xticklabels(labels_c)
ax.set_xlabel("Adoptionsanteil α"); ax.set_ylabel("Airtime-Ersparnis (pp vs. Baseline, kumulativ gestapelt)")
ax.set_title("Beitrags-Zerlegung: welcher Mechanismus trägt wieviel je Adoption\n"
             "(Ebene A, top_traffic; inkrementelles Zuschalten M2→+M1→+M3)")
ax.axhline(0, color="#888", lw=0.8); ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.25)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_comp_contribution.png")); plt.close(fig)

# fig_comp_under_stress — Lieferquote & Airtime unter Stoerung (a=1.0) + M4-Reflood-Ersparnis
fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
slabels = [c[0] for c in stress_cases]
base_d = [stress_A[l]["baseline"]["delivery"] for l in slabels]
comp_d = [next(r for r in stress_A[l]["rows"] if r["alpha"] == 1.0)["delivery"] for l in slabels]
xs = np.arange(len(slabels)); w = 0.35
axs[0].bar(xs - w/2, base_d, w, label="Baseline (0 %)", color="#c0392b")
axs[0].bar(xs + w/2, comp_d, w, label="Komposit (100 %)", color="#2e7d32")
axs[0].set_xticks(xs); axs[0].set_xticklabels(slabels, rotation=20, ha="right")
axs[0].set_ylabel("Lieferquote"); axs[0].set_title("Lieferquote unter Störung (Ebene A, a=1.0)")
axs[0].legend(fontsize=8); axs[0].grid(axis="y", alpha=0.25)
# rechts: M4 Reflood-Airtime gespart + Netto-Airtime unter Linkausfall (voll adoptiert)
lf_pts = {0.10: None, 0.20: None}
for r in reinforce_sweep:
    if r["alpha"] == 1.0 and r["linkfail"] in lf_pts:
        lf_pts[r["linkfail"]] = r
lfx = ["lf 10%", "lf 20%"]
rf_saved = [lf_pts[0.10]["reflood_saved_pct"], lf_pts[0.20]["reflood_saved_pct"]]
net_air = [lf_pts[0.10]["air_net_pct"], lf_pts[0.20]["air_net_pct"]]
axs[1].bar(np.arange(2) - w/2, rf_saved, w, label="Re-Discovery-Airtime gespart %", color="#2980b9")
axs[1].bar(np.arange(2) + w/2, net_air, w, label="Netto-Airtime Δ% (B vs Base)", color="#8e44ad")
axs[1].axhline(0, color="#888", lw=0.8)
axs[1].set_xticks(np.arange(2)); axs[1].set_xticklabels(lfx)
axs[1].set_ylabel("%"); axs[1].set_title("M4 Reinforcement unter Linkausfall (Ebene B, a=1.0)")
axs[1].legend(fontsize=8); axs[1].grid(axis="y", alpha=0.25)
fig.suptitle("Komposit-Schicht unter Störung: Robustheit von Lieferquote, Airtime & Re-Discovery")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_comp_under_stress.png")); plt.close(fig)

log("  Plots geschrieben: fig_comp_airtime_vs_adoption.png, fig_comp_delivery_reliability.png, "
    "fig_comp_hops_detour.png, fig_comp_contribution.png, fig_comp_under_stress.png")

# ======================================================================================
# 10) KENNZAHLEN-DUMP (fuer Bericht/Markdown)
# ======================================================================================
log("\n=== KENNZAHLEN-DUMP (Bericht) ===")
dump = {
    "baseline": {"delivery": round(BASE_DELIV, 4), "airtime": round(BASE_AIR, 2),
                 "hops": round(BASE_HOPS, 2), "detour_med": round(BASE_DETOUR, 2),
                 "stability": round(BASE_STAB, 3)},
    "composite_top_traffic": [
        {"alpha": str(r["alpha"]), "deliv": round(r["delivery"], 4),
         "deliv_vs_base": round(r["delivery_vs_base"], 4),
         "air": round(r["airtime_mean"], 2), "air_pct": round(r["airtime_vs_base_pct"], 1),
         "hops": round(r["hops_mean"], 2), "hops_pct": round(r["hops_vs_base_pct"], 1),
         "detour_med": round(r["detour_median"], 2), "detour_pct": round(r["detour_vs_base_pct"], 1),
         "stab": round(r["route_stability"], 3), "stab_pct": round(r["stab_vs_base_pct"], 1),
         "safe": r["safe"]}
        for r in composite_sweep["top_traffic"]],
    "interaction_a1": {k: (round(v, 2) if isinstance(v, float) else v)
                       for k, v in results["interaction_a1"].items() if k != "isolated"},
    "isolated_a1": {k: round(v["airtime_vs_base_pct"], 1) for k, v in iso.items()},
    "analysis": results["analysis"],
    "reinforce_full_lf20": next(({"deliv_delta": round(r["deliv_delta"], 4),
                                  "air_net_pct": round(r["air_net_pct"], 1),
                                  "reflood_saved_pct": round(r["reflood_saved_pct"], 1)}
                                 for r in reinforce_sweep
                                 if r["alpha"] == 1.0 and r["linkfail"] == 0.20), {}),
}
log(json.dumps(dump, ensure_ascii=False, indent=2))
results["report_dump"] = dump
json.dump(results, open(RES_F, "w"), indent=2, ensure_ascii=False)
log("\n=== FERTIG ===")
