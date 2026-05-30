#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — Validierung der REDUNDANZ-GESICHERTEN Flood-Suppression (Stufe B)
=================================================================================

Zweck (VOR dem Code):
  Pruefe per Simulation auf der ECHTEN Mesh-Topologie, ob das 5-Guard-Design
  (G1-G5, siehe docs/MHR/study/Suppression_Design.md) die Safety-Invariante
  haelt — Lieferquote >= Baseline UND Airtime <= Baseline — ueber den GESAMTEN
  Adoptions-Sweep (1 Knoten -> 100 %). Erst wenn ein Parametersatz das ehrlich
  schafft UND nennenswert Airtime spart, wird der Mechanismus codiert.

Basis (identisch zum v4-Harness mhr_sim_real_v4.py -> Vergleichbarkeit):
  - reale Kanten aus neighbor_graph.json, ambiguous-Kanten verworfen
  - Link-Reliability logistisch aus ECHTEM avg_snr (NICHT aus Distanz)
  - timing-getriebener Flood, first-packet-wins-Dedup
  - Airtime = Anzahl tatsaechlich sendender Knoten je Zustellung
  - hop-gewichtetes Rebroadcast-Delay (Stufe A) fuer MHR-Knoten
  - >=5 Seeds, Seed 42 als Master, reproduzierbar

Die Suppression-Logik (lokale Regel eines MHR-Knotens R, der eine Flood-Kopie P
empfangen hat und VOR seinem Rebroadcast entscheidet — schweigen NUR wenn ALLE
aktiven Guards erfuellt sind, sonst senden = exakt wie Upstream):
  G1 Low-Degree : R.degree >= supp_min_degree              (Blatt-/Bruecken-Schutz)
  G2 Cover-Count: >= supp_k_cover andere Knoten haben P vor/gleichzeitig mit R gesendet
  G3 Nb-Coverage: jeder Nachbar von R ist Nachbar >=1 Cover-Senders (2-Hop-Wissen)
  G4 Reliab-Floor: Cover-Sender-Links haben avg_snr >= supp_snr_floor
  G5 Prob-Margin: mit Wahrscheinlichkeit supp_prob tatsaechlich schweigen

NAIVE Suppression (Kontrast, = altes M3/M4-Versagen): nur G2 (Cover-Count), keine
G1/G3/G4-Schutzschichten.

Aufruf:  python3 suppression_sim.py
Env (Debug): SUPP_FAST=1, SUPP_PAIRS, SUPP_SEEDS
"""

import json
import math
import os
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
SIM = os.path.join(os.path.dirname(HERE), "sim")
DATA = os.path.join(SIM, "data")
NG_F = os.path.join(DATA, "neighbor_graph.json")
NODES_F = os.path.join(DATA, "nodes.json")
CAL_F = os.path.join(DATA, "snr_calibration.json")

MASTER_SEED = 42
FAST = os.environ.get("SUPP_FAST", "0") == "1"
N_PAIRS = int(os.environ.get("SUPP_PAIRS", "120" if not FAST else "20"))
N_SEEDS = int(os.environ.get("SUPP_SEEDS", "6" if not FAST else "2"))   # >=5 fuer Zufall

# Physik / LoRa (identisch v4)
SNR_THR = -12.0
SNR_SCALE = 4.0

# Flood-Timing-Modell (identisch v4)
FLOOD_MAX_BASE = 64
FLOOD_MAX_MHR = 15            # Stufe-A-Wert, MHR-Knoten nutzen ihn
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0
TX_HOP_WEIGHT = 0.6           # hop-gewichtetes Delay (Stufe A) fuer MHR-Knoten

# Default-Suppression-Parameter (aus dem Design)
DEFAULT_PARAMS = dict(min_degree=4, k_cover=2, snr_floor=-6.0, prob=0.8)


def log(msg):
    print(msg, flush=True)


# ======================================================================================
# 1) REALE TOPOLOGIE AUFBAUEN (echte Kanten + echtes Per-Link-SNR), wie v4
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json aufbauen (wie v4) ===")
ng = json.load(open(NG_F))
cal = json.load(open(CAL_F))
SNR_THR = float(cal.get("snr_threshold_db", SNR_THR))


def snr_reliability(snr_db):
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / SNR_SCALE)), 0.02, 0.995))


G = nx.Graph()
SNR = {}
PREL = {}
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
    G.add_edge(u, v, snr=float(s), prel=pr)
    SNR[(u, v)] = SNR[(v, u)] = float(s)
    PREL[(u, v)] = PREL[(v, u)] = pr
    snr_used.append(float(s))

for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

# Anreicherung fuer top_traffic-Rollout (advert/relay) — nur fuer Knoten-Auswahl/Churn
NODE = {}
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    NODE[pk] = {"advert": x.get("advert_count", 0) or 0,
                "neighbor_count": x.get("neighbor_count", 0) or 0}

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)
giant_set = set(giant_list)

# Adjazenz (Nachbar, Reliability) + Grad + Nachbarmengen (als gelerntes 2-Hop-Wissen)
ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}
NBR = {u: set(GC.neighbors(u)) for u in GC.nodes()}      # 1-Hop-Nachbarmenge (Ground-Truth)
DEG = dict(GC.degree())

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


# ======================================================================================
# Imperfektes 2-Hop-Wissen: pro Seed eine eventuell UNVOLLSTAENDIGE Nachbar-Sicht
# ======================================================================================
def build_known_nbr(coverage, seed):
    """Liefert KNOWN_NBR[u] = bekannte Teilmenge von NBR[u].
    coverage=1.0 -> perfekt; <1.0 -> je Knoten zufaellig nur coverage-Anteil der Nachbarn
    bekannt (modelliert lueckenhaftes passives Lernen frischer Firmware)."""
    if coverage >= 0.999:
        return NBR
    rng = np.random.default_rng(seed * 911 + int(coverage * 1000) + 1)
    known = {}
    for u in giant_list:
        nb = list(NBR[u])
        if not nb:
            known[u] = set()
            continue
        k = max(1, int(round(coverage * len(nb))))
        idx = rng.choice(len(nb), size=min(k, len(nb)), replace=False)
        known[u] = set(nb[i] for i in idx)
    return known


# ======================================================================================
# 2) FLOOD-MODELL mit GUARDED / NAIVE Suppression
# ======================================================================================
def rebroadcast_delay(hops, is_mhr, rstate):
    """Stock: Basis + voller Jitter. MHR: hop-gewichtet (frueher bei weniger Hops)."""
    air = BASE_AIR + PER_HOP_AIR * hops
    if is_mhr:
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


def guards_pass(R, cover_senders, cover_snr, params, known_nbr, active, rng):
    """Entscheidet, ob Knoten R SCHWEIGEN darf. cover_senders = Menge der Nachbarn,
    die P (vor/gleichzeitig) bereits gesendet haben. cover_snr[x] = Link-SNR R-x.
    active = Menge aktiver Guards, z.B. {'G2'} (naiv) oder {'G1','G2','G3','G4','G5'}.
    Schweigen NUR wenn alle aktiven Guards erfuellt; sonst senden (sicher)."""
    # G1 Low-Degree / Leaf-Schutz
    if "G1" in active:
        if DEG.get(R, 0) < params["min_degree"]:
            return False
    # G2 Cover-Count
    if "G2" in active:
        if len(cover_senders) < params["k_cover"]:
            return False
    # G4 Reliability-Floor: zaehle nur Cover-Sender mit ausreichendem Link-SNR
    if "G4" in active:
        good = [x for x in cover_senders if cover_snr.get(x, -99.0) >= params["snr_floor"]]
        if len(good) < params["k_cover"]:
            return False
        cover_eff = set(good)
    else:
        cover_eff = set(cover_senders)
    # G3 Neighbour-Coverage: jeder (bekannte) Nachbar von R ist Nachbar >=1 Cover-Senders.
    # Nutzt das (evtl. unvollstaendige) 2-Hop-Wissen known_nbr.
    if "G3" in active:
        my_nbrs = known_nbr.get(R, set())
        # Vereinige Nachbarmengen aller (effektiven) Cover-Sender (deren bekannte Nachbarn)
        covered = set()
        for x in cover_eff:
            covered |= known_nbr.get(x, set())
            covered.add(x)
        # jeder eigene Nachbar (ausser den Cover-Sendern selbst) muss abgedeckt sein
        for nb in my_nbrs:
            if nb == R:
                continue
            if nb not in covered:
                return False
    # G5 Prob-Margin: nur mit Wahrscheinlichkeit prob tatsaechlich schweigen
    if "G5" in active:
        if rng.random() >= params["prob"]:
            return False
    return True


def run_flood(src, dst, mhr_set, rstate, params, active, known_nbr,
              dead_nodes=None, dead_edges=None, flood_max_mhr=FLOOD_MAX_MHR):
    """Timing-getriebener Flood mit lokaler Suppression-Regel der MHR-Knoten.
    mhr_set: Knoten mit neuer Firmware (Suppression-faehig).
    active : Menge aktiver Guards; leer => keine Suppression (= Baseline-Verhalten).
    Rueckgabe: (delivered, used_path, n_tx). n_tx = Airtime (sendende Knoten)."""
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    accepted = {}                       # node -> (hops, prev)
    acc_hops = {}
    sent = set()                        # Knoten, die TATSAECHLICH gesendet haben
    # Cover-Tracking: fuer jeden Knoten v die Nachbarn, die P bereits gesendet haben
    cover_of = collections.defaultdict(set)
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
        node_max = flood_max_mhr if (u in mhr_set) else FLOOD_MAX_BASE
        if hops_u >= node_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:
            continue

        # ---- SUPPRESSION-ENTSCHEIDUNG (nur MHR-Knoten, nicht src/dst) ----
        if active and (u in mhr_set) and u != src and u != dst:
            cover_senders = cover_of.get(u, set())
            cover_snr = {x: SNR.get((u, x), -99.0) for x in cover_senders}
            if guards_pass(u, cover_senders, cover_snr, params, known_nbr, active, rstate):
                # R schweigt: sendet NICHT. (u bleibt aus 'sent'.)
                continue

        sent.add(u)
        out_hops = hops_u + 1
        rnd = rstate.random
        for v, pr in ADJ[u]:
            if has_dead and not link_ok(u, v):
                continue
            if rnd() > pr:
                continue
            # v hoert, dass u (ein Nachbar) gesendet hat -> u ist Cover-Sender fuer v
            cover_of[v].add(u)
            if v == dst:
                p = reconstruct_path(accepted, u, src) + [v]
                dst_paths.append((out_hops, p))
            if v not in accepted:
                accepted[v] = (out_hops, u)
                acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, v in mhr_set, rstate)
                schedule_send(v, t_send + d, out_hops, u)

    delivered = dst in accepted or len(dst_paths) > 0
    if not delivered:
        return False, None, len(sent)
    used = reconstruct_path(accepted, dst, src)
    return True, used, len(sent)


# ======================================================================================
# Rollout / Sweep-Infrastruktur (wie v4)
# ======================================================================================
top_traffic_order = sorted(giant_list,
                           key=lambda pk: (NODE.get(pk, {}).get("advert", 0),
                                           DEG.get(pk, 0)), reverse=True)


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


def shortest_hops(s, d):
    key = (s, d)
    if key in _sp_cache:
        return _sp_cache[key]
    try:
        v = nx.shortest_path_length(GC, s, d)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        v = None
    _sp_cache[key] = v
    return v


def evaluate(alpha, rollout, seeds, n_pairs, params, active,
             knowledge_coverage=1.0, dead_nodes_frac=0.0, dead_edges_frac=0.0,
             churn=False, stress_seed_off=0):
    """Ein Konfig-Lauf. active=set() -> Baseline (kein Suppress). Gibt Aggregat zurueck."""
    air_list, deliv_list = [], []
    per_seed_deliv, per_seed_air = [], []

    for si, seed in enumerate(seeds):
        seed_deliv, seed_air = [], []
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503 + stress_seed_off)
        mhr_set = select_mhr(alpha, rollout, seed) & giant_set
        known_nbr = build_known_nbr(knowledge_coverage, seed)

        dead_nodes, dead_edges = set(), set()
        if churn:
            # Churn: instabile (selten gehoerte) Knoten fallen weg, nach advert_count
            order = sorted(giant_list, key=lambda pk: NODE.get(pk, {}).get("advert", 0))
            k = int(0.05 * len(order))
            cand = order[:max(k * 3, k)]
            if cand:
                sel = rstate.choice(len(cand), size=min(k, len(cand)), replace=False)
                dead_nodes = set(cand[i] for i in sel)
        if dead_nodes_frac > 0:
            order = sorted(giant_list, key=lambda pk: NODE.get(pk, {}).get("advert", 0))
            k = int(dead_nodes_frac * len(order))
            cand = order[:max(k * 3, k)]
            if cand:
                sel = rstate.choice(len(cand), size=min(k, len(cand)), replace=False)
                dead_nodes |= set(cand[i] for i in sel)
        if dead_edges_frac > 0:
            all_e = list(GC.edges())
            k = int(dead_edges_frac * len(all_e))
            if all_e and k > 0:
                sel = rstate.choice(len(all_e), size=k, replace=False)
                dead_edges = set(frozenset(all_e[i]) for i in sel)

        for (s, d) in make_pairs(seed, n_pairs):
            if s in dead_nodes or d in dead_nodes:
                continue
            ok, used, ntx = run_flood(s, d, mhr_set, rstate, params, active, known_nbr,
                                      dead_nodes=dead_nodes, dead_edges=dead_edges)
            deliv_list.append(1.0 if ok else 0.0)
            seed_deliv.append(1.0 if ok else 0.0)
            if ok and used:
                air_list.append(ntx)
                seed_air.append(ntx)
        if seed_deliv:
            per_seed_deliv.append(float(np.mean(seed_deliv)))
        if seed_air:
            per_seed_air.append(float(np.mean(seed_air)))

    def safem(x):
        return float(np.mean(x)) if len(x) else 0.0

    return {
        "alpha": alpha, "rollout": rollout, "n_obs": len(deliv_list),
        "delivery": safem(deliv_list),
        "airtime_mean": safem(air_list),
        "deliv_sem": (float(np.std(per_seed_deliv, ddof=1) / math.sqrt(len(per_seed_deliv)))
                      if len(per_seed_deliv) >= 2 else 0.0),
        "air_sem": (float(np.std(per_seed_air, ddof=1) / math.sqrt(len(per_seed_air)))
                    if len(per_seed_air) >= 2 else 0.0),
    }


# ======================================================================================
# Baseline (kein Suppress, alle Knoten Stock)
# ======================================================================================
log("\n=== Baseline (Stock-Flood, kein Suppress) ===")
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
GUARDS_ALL = {"G1", "G2", "G3", "G4", "G5"}
GUARDS_NAIVE = {"G2"}

base = evaluate(0.0, "random", seeds, N_PAIRS, DEFAULT_PARAMS, active=set())
BASE_DELIV = base["delivery"]
BASE_AIR = base["airtime_mean"]
DELIV_TOL = max(2.0 * base["deliv_sem"], 0.005)
AIR_TOL = max(2.0 * base["air_sem"], 0.005 * BASE_AIR)
log(f"  Baseline: delivery={BASE_DELIV:.4f}  airtime={BASE_AIR:.2f}")
log(f"  Rausch-Band (2*SEM,min): deliv +-{DELIV_TOL:.4f}, air +-{AIR_TOL:.3f}")


def safe_flag(row):
    """Safety-Invariante mit Rausch-Band: deliv >= Baseline UND airtime <= Baseline."""
    return (row["delivery"] >= BASE_DELIV - DELIV_TOL) and \
           (row["airtime_mean"] <= BASE_AIR + AIR_TOL)


def annotate(row):
    row["delivery_vs_base"] = row["delivery"] - BASE_DELIV
    row["airtime_vs_base_pct"] = (100 * (row["airtime_mean"] - BASE_AIR) / BASE_AIR
                                  if BASE_AIR > 0 else 0.0)
    row["safe"] = bool(safe_flag(row))
    row["safe_strict"] = bool((row["delivery"] >= BASE_DELIV - 1e-9) and
                              (row["airtime_mean"] <= BASE_AIR + 1e-9))
    return row


ALPHAS = ["1node", 0.05, 0.10, 0.25, 0.50, 1.0]
ROLLOUT = "top_traffic"     # konservativster Fall: Hub-Knoten schweigen zuerst


# ======================================================================================
# EXPERIMENT 1: Safety-Sweep ueber Adoption (guarded vs naive vs baseline)
# ======================================================================================
log("\n=== EXP 1: Safety-Sweep ueber Adoption (Default-Params) ===")
log(f"  Params: {DEFAULT_PARAMS}  Rollout: {ROLLOUT}")
exp1 = {"guarded": [], "naive": []}
for alpha in ALPHAS:
    g = annotate(evaluate(alpha, ROLLOUT, seeds, N_PAIRS, DEFAULT_PARAMS, GUARDS_ALL))
    n = annotate(evaluate(alpha, ROLLOUT, seeds, N_PAIRS, DEFAULT_PARAMS, GUARDS_NAIVE))
    exp1["guarded"].append(g)
    exp1["naive"].append(n)
    log(f"  a={str(alpha):6s} | GUARDED deliv={g['delivery']:.4f} "
        f"({g['delivery_vs_base']:+.4f}) air={g['airtime_mean']:6.2f} "
        f"({g['airtime_vs_base_pct']:+6.1f}%) SAFE={'OK' if g['safe'] else 'X'}"
        f"  || NAIVE deliv={n['delivery']:.4f} ({n['delivery_vs_base']:+.4f}) "
        f"air={n['airtime_mean']:6.2f} ({n['airtime_vs_base_pct']:+6.1f}%) "
        f"SAFE={'OK' if n['safe'] else 'X'}")

guarded_all_safe = all(r["safe"] for r in exp1["guarded"])
naive_all_safe = all(r["safe"] for r in exp1["naive"])
# Airtime-Gewinn der guarded-Variante (nur bei gehaltener Lieferquote interessant)
g_air_gains = [-r["airtime_vs_base_pct"] for r in exp1["guarded"] if r["delivery"] >= BASE_DELIV - DELIV_TOL]
log(f"  -> GUARDED ueberall safe: {guarded_all_safe} | NAIVE ueberall safe: {naive_all_safe}")


# ======================================================================================
# EXPERIMENT 2: Parameter-Sweep — sicheren Sweet-Spot finden
# ======================================================================================
log("\n=== EXP 2: Parameter-Sweep (k_cover, min_degree, prob) ===")
exp2 = []
for k_cover in (2, 3):
    for min_degree in (3, 4, 5):
        for prob in (0.6, 0.8, 1.0):
            params = dict(min_degree=min_degree, k_cover=k_cover,
                          snr_floor=DEFAULT_PARAMS["snr_floor"], prob=prob)
            rows = [annotate(evaluate(a, ROLLOUT, seeds, N_PAIRS, params, GUARDS_ALL))
                    for a in ALPHAS]
            all_safe = all(r["safe"] for r in rows)
            # Airtime-Gewinn (Mittel ueber alpha) nur wenn alle safe
            mean_air_gain = float(np.mean([-r["airtime_vs_base_pct"] for r in rows]))
            # Hoch-Adoptions-Gewinn (a >= 0.5): DORT soll der Mechanismus wirken. Bei
            # niedrigem alpha ist 0 Ersparnis korrekt (Stock-Netz flutet ohnehin voll).
            high = [r for r in rows if r["alpha"] in (0.5, 1.0)]
            high_air_gain = float(np.mean([-r["airtime_vs_base_pct"] for r in high])) if high else 0.0
            a1 = [r for r in rows if r["alpha"] == 1.0]
            air_gain_a1 = (-a1[0]["airtime_vs_base_pct"]) if a1 else 0.0
            min_deliv = min(r["delivery"] for r in rows)
            worst_deliv_vs_base = min(r["delivery_vs_base"] for r in rows)
            entry = {
                "k_cover": k_cover, "min_degree": min_degree, "prob": prob,
                "all_safe": bool(all_safe), "mean_airtime_gain_pct": mean_air_gain,
                "high_alpha_airtime_gain_pct": high_air_gain,
                "airtime_gain_a1_pct": air_gain_a1,
                "min_delivery": min_deliv, "worst_delivery_vs_base": worst_deliv_vs_base,
                "per_alpha": rows,
            }
            exp2.append(entry)
            log(f"  k={k_cover} deg={min_degree} p={prob}: all_safe={all_safe} "
                f"mean_gain={mean_air_gain:+5.1f}% high_a_gain(a>=.5)={high_air_gain:+5.1f}% "
                f"a=1.0_gain={air_gain_a1:+5.1f}% worst_deliv_vs_base={worst_deliv_vs_base:+.4f}")

safe_configs = [e for e in exp2 if e["all_safe"]]
sweet_spot = None
if safe_configs:
    # Sweet-Spot = max Airtime-Gewinn bei HOHER Adoption (dort wirkt die Suppression),
    # unter der harten Bedingung all_safe ueber ALLE alpha.
    sweet_spot = max(safe_configs, key=lambda e: e["high_alpha_airtime_gain_pct"])
    log(f"  -> SWEET-SPOT (max Hoch-Adoptions-Airtime-Gewinn bei all_safe): "
        f"k={sweet_spot['k_cover']} deg={sweet_spot['min_degree']} "
        f"p={sweet_spot['prob']} | high_a_gain={sweet_spot['high_alpha_airtime_gain_pct']:+.1f}% "
        f"a=1.0_gain={sweet_spot['airtime_gain_a1_pct']:+.1f}%")
else:
    log("  -> KEIN Parametersatz haelt die Invariante ueber ALLE alpha (safe-Band).")


# ======================================================================================
# EXPERIMENT 3: Ablation — Beitrag jeder Guard
# ======================================================================================
log("\n=== EXP 3: Ablation (nur G2 / G2+G3 / G2+G3+G1 / alle) ===")
ABLATIONS = collections.OrderedDict([
    ("nur G2", {"G2"}),
    ("G2+G3", {"G2", "G3"}),
    ("G2+G3+G1", {"G1", "G2", "G3"}),
    ("alle (G1-G5)", GUARDS_ALL),
])
# Ablation mit Default-Params (so wird G3 als load-bearing isoliert sichtbar)
exp3 = {}
for label, active in ABLATIONS.items():
    rows = [annotate(evaluate(a, ROLLOUT, seeds, N_PAIRS, DEFAULT_PARAMS, active))
            for a in ALPHAS]
    all_safe = all(r["safe"] for r in rows)
    min_deliv_vs_base = min(r["delivery_vs_base"] for r in rows)
    mean_air_gain = float(np.mean([-r["airtime_vs_base_pct"] for r in rows]))
    exp3[label] = {"active": sorted(active), "all_safe": bool(all_safe),
                   "min_delivery_vs_base": min_deliv_vs_base,
                   "mean_airtime_gain_pct": mean_air_gain, "per_alpha": rows}
    log(f"  {label:14s}: all_safe={all_safe} worst_deliv_vs_base="
        f"{min_deliv_vs_base:+.4f} mean_air_gain={mean_air_gain:+5.1f}%")

g3_load_bearing = (not exp3["nur G2"]["all_safe"]) and exp3["G2+G3"]["all_safe"]


# ======================================================================================
# EXPERIMENT 4: Imperfektes 2-Hop-Wissen (60/80/100 %)
# ======================================================================================
log("\n=== EXP 4: Imperfektes 2-Hop-Wissen (Knowledge-Coverage 0.6/0.8/1.0) ===")
params_e4 = sweet_spot if sweet_spot else DEFAULT_PARAMS
params_e4 = ({"min_degree": params_e4["min_degree"], "k_cover": params_e4["k_cover"],
              "snr_floor": DEFAULT_PARAMS["snr_floor"], "prob": params_e4["prob"]}
             if sweet_spot else DEFAULT_PARAMS)
log(f"  Params: {params_e4}")
exp4 = {}
for cov in (0.6, 0.8, 1.0):
    rows = [annotate(evaluate(a, ROLLOUT, seeds, N_PAIRS, params_e4, GUARDS_ALL,
                              knowledge_coverage=cov)) for a in ALPHAS]
    all_safe = all(r["safe"] for r in rows)
    min_deliv_vs_base = min(r["delivery_vs_base"] for r in rows)
    mean_air_gain = float(np.mean([-r["airtime_vs_base_pct"] for r in rows]))
    exp4[f"{cov:.1f}"] = {"all_safe": bool(all_safe),
                          "min_delivery_vs_base": min_deliv_vs_base,
                          "mean_airtime_gain_pct": mean_air_gain, "per_alpha": rows}
    log(f"  coverage={cov:.1f}: all_safe={all_safe} worst_deliv_vs_base="
        f"{min_deliv_vs_base:+.4f} mean_air_gain={mean_air_gain:+5.1f}%")

imperfect_holds = all(exp4[k]["all_safe"] for k in exp4)


# ======================================================================================
# EXPERIMENT 5: Stress — Churn + Linkausfall 10/20 % bei alpha=1.0
# ======================================================================================
log("\n=== EXP 5: Stress (Churn + Linkausfall 10/20 %) bei alpha=1.0 ===")
params_e5 = params_e4
exp5 = {}
stress_cases = [
    ("churn", dict(churn=True)),
    ("link_fail_10", dict(dead_edges_frac=0.10)),
    ("link_fail_20", dict(dead_edges_frac=0.20)),
    ("churn+link20", dict(churn=True, dead_edges_frac=0.20)),
]
for label, kw in stress_cases:
    # Baseline UNTER der gleichen Stoerung (fairer Vergleich: Invariante vs gestoerte Baseline)
    bsl = evaluate(0.0, "random", seeds, N_PAIRS, params_e5, active=set(), **kw)
    g = evaluate(1.0, ROLLOUT, seeds, N_PAIRS, params_e5, GUARDS_ALL, **kw)
    deliv_ok = g["delivery"] >= bsl["delivery"] - DELIV_TOL
    air_ok = g["airtime_mean"] <= bsl["airtime_mean"] + AIR_TOL
    exp5[label] = {
        "baseline_delivery": bsl["delivery"], "baseline_airtime": bsl["airtime_mean"],
        "guarded_delivery": g["delivery"], "guarded_airtime": g["airtime_mean"],
        "delivery_vs_base": g["delivery"] - bsl["delivery"],
        "airtime_vs_base_pct": (100 * (g["airtime_mean"] - bsl["airtime_mean"]) /
                                bsl["airtime_mean"] if bsl["airtime_mean"] > 0 else 0.0),
        "safe": bool(deliv_ok and air_ok),
    }
    e = exp5[label]
    log(f"  {label:14s}: base_deliv={bsl['delivery']:.4f} g_deliv={g['delivery']:.4f} "
        f"({e['delivery_vs_base']:+.4f}) g_air={g['airtime_mean']:.2f} "
        f"({e['airtime_vs_base_pct']:+.1f}%) SAFE={'OK' if e['safe'] else 'X'}")

stress_holds = all(exp5[k]["safe"] for k in exp5)


# ======================================================================================
# GO / NO-GO
# ======================================================================================
# Hartes Gate: Safety-Invariante ueber ALLE alpha gehalten (das ist der nicht
# verhandelbare Kern). Nutzen-Schwelle: nennenswerter Airtime-Gewinn DORT, wo der
# Mechanismus wirken soll (hohe Adoption), >= 10 %. Mittel-ueber-alpha waere
# irrefuehrend, weil bei niedrigem alpha 0 Ersparnis KORREKT ist.
safety_all_alpha = bool(sweet_spot is not None and imperfect_holds)
useful_savings = bool(sweet_spot is not None and
                      sweet_spot["high_alpha_airtime_gain_pct"] >= 10.0)
go = bool(safety_all_alpha and useful_savings)
log("\n=== GO/NO-GO ===")
log(f"  Sweet-Spot vorhanden & all_safe ueber alle alpha: {sweet_spot is not None}")
if sweet_spot:
    log(f"  Airtime-Gewinn Sweet-Spot: a>=0.5 ={sweet_spot['high_alpha_airtime_gain_pct']:+.1f}%, "
        f"a=1.0 ={sweet_spot['airtime_gain_a1_pct']:+.1f}%, "
        f"Mittel-alle-alpha ={sweet_spot['mean_airtime_gain_pct']:+.1f}%")
log(f"  Imperfektes Wissen haelt Invariante: {imperfect_holds}")
log(f"  G3 load-bearing: {g3_load_bearing}")
log(f"  Stress haelt (inkl. Linkausfall 20 %): {stress_holds}")
log(f"  ==> ENTSCHEIDUNG: {'GO' if go else 'NO-GO'} "
    f"(Safety ueber alle alpha: {safety_all_alpha}, nutzbare Ersparnis >=10%%: {useful_savings})")


# ======================================================================================
# ERGEBNISSE SCHREIBEN
# ======================================================================================
results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS, "fast_mode": FAST,
    "rollout": ROLLOUT,
    "topology": {
        "core_nodes": G.number_of_nodes(), "core_edges": G.number_of_edges(),
        "ambiguous_dropped": n_ambig, "giant_nodes": GC.number_of_nodes(),
        "giant_edges": GC.number_of_edges(),
        "avg_degree": float(statistics.mean(degs)),
        "degree_median": float(statistics.median(degs)),
    },
    "baseline": {"delivery": BASE_DELIV, "airtime_mean": BASE_AIR},
    "safety_tolerance": {"delivery_tol": DELIV_TOL, "airtime_tol": AIR_TOL},
    "default_params": DEFAULT_PARAMS,
    "exp1_safety_sweep": exp1,
    "exp1_guarded_all_safe": guarded_all_safe,
    "exp1_naive_all_safe": naive_all_safe,
    "exp2_param_sweep": exp2,
    "exp2_sweet_spot": sweet_spot,
    "exp3_ablation": exp3,
    "exp3_g3_load_bearing": g3_load_bearing,
    "exp4_imperfect_knowledge": exp4,
    "exp4_params_used": params_e4,
    "exp4_holds": imperfect_holds,
    "exp5_stress": exp5,
    "exp5_holds": stress_holds,
    "decision_go": go,
}
json.dump(results, open(os.path.join(HERE, "suppression_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("\n  suppression_results.json geschrieben.")


# ======================================================================================
# PLOTS
# ======================================================================================
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})
ax_str = [str(a) for a in ALPHAS]
xpos = list(range(len(ALPHAS)))


def base_line(ax, val, color, label):
    ax.axhline(val, color=color, ls=":", lw=1, label=label)


# --- fig_supp_safety_sweep: deliv + airtime ueber alpha, guarded vs naive vs baseline
fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.8))
gd = [r["delivery"] for r in exp1["guarded"]]
nd = [r["delivery"] for r in exp1["naive"]]
axs[0].plot(xpos, gd, "o-", color="#2e7d32", label="Guarded (G1-G5)")
axs[0].plot(xpos, nd, "s--", color="#c0392b", label="Naiv (nur G2)")
base_line(axs[0], BASE_DELIV, "#333", f"Baseline {BASE_DELIV:.3f}")
axs[0].set_xticks(xpos); axs[0].set_xticklabels(ax_str)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Lieferquote")
axs[0].set_title("Lieferquote über Adoption\n(Invariante: ≥ Baseline)")
axs[0].legend(fontsize=8); axs[0].grid(alpha=0.25)

ga = [r["airtime_mean"] for r in exp1["guarded"]]
na = [r["airtime_mean"] for r in exp1["naive"]]
axs[1].plot(xpos, ga, "o-", color="#2e7d32", label="Guarded (G1-G5)")
axs[1].plot(xpos, na, "s--", color="#c0392b", label="Naiv (nur G2)")
base_line(axs[1], BASE_AIR, "#333", f"Baseline {BASE_AIR:.1f}")
axs[1].set_xticks(xpos); axs[1].set_xticklabels(ax_str)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Ø Airtime (Sende-Ereignisse)")
axs[1].set_title("Airtime über Adoption\n(Invariante: ≤ Baseline)")
axs[1].legend(fontsize=8); axs[1].grid(alpha=0.25)
fig.suptitle(f"EXP 1: Safety-Sweep — Default-Params {DEFAULT_PARAMS}, "
             f"{ROLLOUT}, {N_SEEDS} Seeds, {N_PAIRS} Paare")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_supp_safety_sweep.png")); plt.close(fig)


# --- fig_supp_param_sweep: Heatmap Airtime-Gewinn, X=Params, grün=all_safe
fig, ax = plt.subplots(figsize=(11.5, 5.0))
labels = [f"k{e['k_cover']} d{e['min_degree']} p{e['prob']}" for e in exp2]
gains = [e["mean_airtime_gain_pct"] if e["all_safe"] else 0.0 for e in exp2]
colors = ["#2e7d32" if e["all_safe"] else "#c0392b" for e in exp2]
bars = ax.bar(range(len(exp2)), [e["mean_airtime_gain_pct"] for e in exp2], color=colors)
ax.set_xticks(range(len(exp2))); ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
ax.set_ylabel("Ø Airtime-Gewinn über α (%)")
ax.axhline(0, color="k", lw=0.8)
ax.set_title("EXP 2: Parameter-Sweep — grün = Invariante über ALLE α gehalten, "
             "rot = irgendwo verletzt")
for i, e in enumerate(exp2):
    ax.text(i, e["mean_airtime_gain_pct"],
            f"{e['mean_airtime_gain_pct']:.0f}", ha="center",
            va="bottom" if e["mean_airtime_gain_pct"] >= 0 else "top", fontsize=6)
if sweet_spot:
    si = exp2.index(sweet_spot)
    ax.annotate("Sweet-Spot", xy=(si, sweet_spot["mean_airtime_gain_pct"]),
                xytext=(si, sweet_spot["mean_airtime_gain_pct"] + 8),
                ha="center", fontsize=8, color="#2e7d32",
                arrowprops=dict(arrowstyle="->", color="#2e7d32"))
ax.grid(axis="y", alpha=0.25)
fig.subplots_adjust(bottom=0.30)
fig.savefig(os.path.join(HERE, "fig_supp_param_sweep.png")); plt.close(fig)


# --- fig_supp_ablation: deliv über alpha je Ablationsvariante
fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.8))
colmap = {"nur G2": "#c0392b", "G2+G3": "#e67e22", "G2+G3+G1": "#2980b9",
          "alle (G1-G5)": "#2e7d32"}
for label, data in exp3.items():
    dl = [r["delivery"] for r in data["per_alpha"]]
    axs[0].plot(xpos, dl, "o-", color=colmap[label], label=label)
base_line(axs[0], BASE_DELIV, "#333", f"Baseline {BASE_DELIV:.3f}")
axs[0].set_xticks(xpos); axs[0].set_xticklabels(ax_str)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Lieferquote")
axs[0].set_title("EXP 3: Ablation — Lieferquote\n(G3 = load-bearing?)")
axs[0].legend(fontsize=8); axs[0].grid(alpha=0.25)
for label, data in exp3.items():
    al = [r["airtime_mean"] for r in data["per_alpha"]]
    axs[1].plot(xpos, al, "o-", color=colmap[label], label=label)
base_line(axs[1], BASE_AIR, "#333", f"Baseline {BASE_AIR:.1f}")
axs[1].set_xticks(xpos); axs[1].set_xticklabels(ax_str)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Ø Airtime")
axs[1].set_title("EXP 3: Ablation — Airtime")
axs[1].legend(fontsize=8); axs[1].grid(alpha=0.25)
fig.suptitle(f"EXP 3: Guard-Ablation (Default-Params, {ROLLOUT}) — "
             f"G3 load-bearing: {g3_load_bearing}")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_supp_ablation.png")); plt.close(fig)


# --- fig_supp_imperfect_knowledge
fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.8))
covcol = {"0.6": "#c0392b", "0.8": "#e67e22", "1.0": "#2e7d32"}
for cov in ("0.6", "0.8", "1.0"):
    dl = [r["delivery"] for r in exp4[cov]["per_alpha"]]
    axs[0].plot(xpos, dl, "o-", color=covcol[cov], label=f"2-Hop-Wissen {cov}")
base_line(axs[0], BASE_DELIV, "#333", f"Baseline {BASE_DELIV:.3f}")
axs[0].set_xticks(xpos); axs[0].set_xticklabels(ax_str)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Lieferquote")
axs[0].set_title("EXP 4: Imperfektes 2-Hop-Wissen — Lieferquote\n(hält Invariante dank G1-Fallback?)")
axs[0].legend(fontsize=8); axs[0].grid(alpha=0.25)
for cov in ("0.6", "0.8", "1.0"):
    al = [r["airtime_mean"] for r in exp4[cov]["per_alpha"]]
    axs[1].plot(xpos, al, "o-", color=covcol[cov], label=f"2-Hop-Wissen {cov}")
base_line(axs[1], BASE_AIR, "#333", f"Baseline {BASE_AIR:.1f}")
axs[1].set_xticks(xpos); axs[1].set_xticklabels(ax_str)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Ø Airtime")
axs[1].set_title("EXP 4: Imperfektes 2-Hop-Wissen — Airtime")
axs[1].legend(fontsize=8); axs[1].grid(alpha=0.25)
fig.suptitle(f"EXP 4: Robustheit gegen lückenhaftes 2-Hop-Lernen (Params {params_e4})")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_supp_imperfect_knowledge.png")); plt.close(fig)

log("  Plots geschrieben: fig_supp_safety_sweep.png, fig_supp_param_sweep.png, "
    "fig_supp_ablation.png, fig_supp_imperfect_knowledge.png")

# Kennzahlen-Dump fuer den Bericht
log("\n=== KENNZAHLEN-DUMP (Bericht) ===")
log(json.dumps({
    "base_deliv": round(BASE_DELIV, 4), "base_air": round(BASE_AIR, 2),
    "guarded_all_safe": guarded_all_safe, "naive_all_safe": naive_all_safe,
    "sweet_spot": ({k: sweet_spot[k] for k in
                    ("k_cover", "min_degree", "prob", "mean_airtime_gain_pct",
                     "high_alpha_airtime_gain_pct", "airtime_gain_a1_pct")}
                   if sweet_spot else None),
    "g3_load_bearing": g3_load_bearing,
    "imperfect_holds": imperfect_holds, "stress_holds": stress_holds,
    "decision_go": go,
}, ensure_ascii=False))
log("\n=== FERTIG ===")
