#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — LOKAL-KALIBRIERTE Flood-Suppression (Stufe B++, raeumliche Heterogenitaet)
==========================================================================================

HYPOTHESE (anders als das bisherige NO-GO des SCHNELLEN supp_prob-Reglers!):
  Das bisherige NO-GO (Adaptive_Controller_Design.md) mass den NETZ-DURCHSCHNITT eines
  schnellen (1-2h) Reglers und fand nur +0,76 pp ueber statisch. Neue Hypothese: der Wert
  liegt in der RAEUMLICHEN HETEROGENITAET. Ein LANGSAM (12-48h) pro Knoten an die EIGENE
  lokale Dichte/Rolle/Redundanz kalibrierter supp_prob-GRUNDWERT schlaegt den globalen
  statischen Kompromiss — v.a. an den Extremen: dichte Hubs duerfen aggressiver schweigen,
  sparse Bruecken muessen konservativ bleiben.

Was diese Sim macht:
  1. Drei Strategien auf der REALEN Topologie ueber den Adoptions-Sweep (1 Knoten -> 100%):
       (a) Baseline  (Stock-Flood, kein Suppress)
       (b) global-statisch  (fester sicherer Satz p=0.8 fuer ALLE)
       (c) lokal-kalibriert (jeder Knoten setzt seinen supp_prob aus seiner gemittelten
           lokalen Dichte + gehoerter Redundanz + Churn, bounded im sicheren Fenster).
     Guards G1-G4 bleiben in (b)+(c) IMMER aktiv (Safety per Paket).
  2. HETEROGENITAETS-AUFSCHLUESSELUNG (Kern): Knoten in Klassen (Hubs/Mittel/Bruecken-Blaetter)
     nach Grad. Airtime-Gewinn UND Lieferquote GETRENNT je Klasse. Frage: holt (c) bei Hubs
     mehr Airtime als (b), OHNE bei Bruecken die Lieferquote zu senken?
  3. Safety-Invariante HART: Lieferquote >= Baseline bei ALLEN Adoptionsgraden UND in JEDER
     Knotenklasse (Klassen-Delivery = Floods, deren Ziel in der Klasse liegt).
  4. Falls (c) signifikant > (b): Intervall-Sweetspot (12/24/48h als Ticks) + adaptives Intervall.
  5. On-Node-Machbarkeit: (c) auf eine MINIMALE Feature->Parameter-Kennlinie reduzieren
     (Lookup aus 2-3 lokal messbaren Groessen). Wieviel % des Gewinns bleibt erhalten?

Aufruf:  python3 local_calib_sim.py
Env (Debug): LC_FAST=1, LC_SEEDS, LC_PAIRS
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
FAST = os.environ.get("LC_FAST", "0") == "1"
N_PAIRS = int(os.environ.get("LC_PAIRS", "300" if not FAST else "60"))
N_SEEDS = int(os.environ.get("LC_SEEDS", "5" if not FAST else "2"))
# Kalibrier-Ticks: ueber die der Knoten seine lokale Lage mittelt, BEVOR er kalibriert.
N_CALIB_TICKS = int(os.environ.get("LC_CALIB_TICKS", "24" if not FAST else "8"))

# Physik / LoRa (identisch suppression_sim.py / adaptive_sim.py)
SNR_THR = -12.0
SNR_SCALE = 4.0
FLOOD_MAX_BASE = 64
FLOOD_MAX_MHR = 15
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0
TX_HOP_WEIGHT = 0.6

# --- Validiertes sicheres Fenster (aus SUPPRESSION_VALIDATION.md) ---
SAFE_K_COVER = 2
SAFE_MIN_DEGREE = 3
SAFE_SNR_FLOOR = -6.0
P_MIN = 0.50          # konservativste validierte Variante (untere Grenze von p)
P_MAX = 1.00          # aggressivste validierte Variante
P_STATIC = 0.80       # globaler statischer Referenzwert (Strategie b)
GUARDS_ALL = {"G1", "G2", "G3", "G4", "G5"}

# Adoptions-Sweep (alpha = Anteil MHR-Knoten, Hubs zuerst -> konservativster Fall)
ALPHAS = [0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 1.0]   # 0.0 = "1 Knoten" Sonderfall unten
SINGLE_NODE = True  # zusaetzlich der "1 Knoten"-Fall


def log(msg):
    print(msg, flush=True)


# ======================================================================================
# 1) REALE TOPOLOGIE
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json ===")
ng = json.load(open(NG_F))
cal = json.load(open(CAL_F))
SNR_THR = float(cal.get("snr_threshold_db", SNR_THR))


def snr_reliability(snr_db):
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / SNR_SCALE)), 0.02, 0.995))


G = nx.Graph()
SNR = {}
PREL = {}
n_ambig = 0
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

for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

adv_raw = {}
role_raw = {}
try:
    nd = json.load(open(NODES_F))["nodes"]
    for x in nd:
        pk = (x.get("public_key") or "").lower()
        if pk:
            adv_raw[pk] = int(x.get("advert_count", 0) or 0)
            role_raw[pk] = x.get("role", "")
except Exception as ex:  # pragma: no cover
    log(f"  WARN nodes.json nicht ladbar ({ex})")

NODE = {}
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    NODE[pk] = {"advert": adv_raw.get(pk, 0),
                "role": role_raw.get(pk, ""),
                "neighbor_count": x.get("neighbor_count", 0) or 0}

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)
giant_set = set(giant_list)

ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}
NBR = {u: set(GC.neighbors(u)) for u in GC.nodes()}
DEG = dict(GC.degree())

log(f"  Kern-Graph: {G.number_of_nodes()} Knoten, {G.number_of_edges()} Kanten, "
    f"ambiguous verworfen: {n_ambig}")
log(f"  Grad min/median/mean/max: {min(degs)}/{statistics.median(degs):.0f}/"
    f"{statistics.mean(degs):.2f}/{max(degs)}")
log(f"  Riesenkomponente (Simulation): {GC.number_of_nodes()} Knoten, {GC.number_of_edges()} Kanten")


# ======================================================================================
# 2) KNOTENKLASSEN (Heterogenitaets-Aufschluesselung)
# ======================================================================================
# Einteilung nach Grad (Dichte-Proxy). Bruecken/Blaetter = niedriger Grad (gefaehrlich),
# Hubs = hoher Grad (duerfen am ehesten schweigen). Quantilgrenzen aus der Grad-Verteilung
# der Riesenkomponente.
gdeg = np.array([DEG[u] for u in giant_list])
Q_LOW = float(np.quantile(gdeg, 0.40))    # untere 40% = Bruecken/Blaetter
Q_HIGH = float(np.quantile(gdeg, 0.80))   # obere 20% = Hubs


def node_class(u):
    d = DEG.get(u, 0)
    if d <= Q_LOW:
        return "bruecke_blatt"
    if d >= Q_HIGH:
        return "hub"
    return "mittel"


CLASS = {u: node_class(u) for u in giant_list}
CLASSES = ["bruecke_blatt", "mittel", "hub"]
class_counts = collections.Counter(CLASS.values())
log(f"  Knotenklassen (Grad-Quantile q40={Q_LOW:.0f}, q80={Q_HIGH:.0f}): "
    + ", ".join(f"{c}={class_counts[c]}" for c in CLASSES))


# ======================================================================================
# 3) FLOOD-MODELL mit GUARDED Suppression (per-Knoten supp_prob)
# ======================================================================================
def rebroadcast_delay(hops, is_mhr, rstate):
    air = BASE_AIR + PER_HOP_AIR * hops
    if is_mhr:
        base = air * (1.0 + TX_HOP_WEIGHT * hops)
        return base + rstate.uniform(0.0, 1.5 * air)
    return air + rstate.uniform(0.0, JITTER * air)


def guards_pass(R, cover_senders, cover_snr, node_prob, known_nbr, rng):
    """Schweigen NUR wenn ALLE Guards erfuellt; sonst senden. supp_prob (G5) pro Knoten.
    G1-G4 immer aktiv (sicheres Fenster, fix). Identisch zur validierten Logik."""
    if DEG.get(R, 0) < SAFE_MIN_DEGREE:           # G1 Low-Degree
        return False
    if len(cover_senders) < SAFE_K_COVER:          # G2 Cover-Count
        return False
    good = [x for x in cover_senders if cover_snr.get(x, -99.0) >= SAFE_SNR_FLOOR]  # G4
    if len(good) < SAFE_K_COVER:
        return False
    cover_eff = set(good)
    my_nbrs = known_nbr.get(R, set())              # G3 Neighbour-Coverage (load-bearing)
    covered = set()
    for x in cover_eff:
        covered |= known_nbr.get(x, set())
        covered.add(x)
    for nb in my_nbrs:
        if nb == R:
            continue
        if nb not in covered:
            return False
    if rng.random() >= node_prob.get(R, P_STATIC):  # G5 Prob-Margin
        return False
    return True


def run_flood(src, dst, mhr_set, rstate, node_prob, known_nbr, adapt_on,
              dead_nodes=None, dead_edges=None, measure=None):
    """Timing-getriebener Flood. adapt_on=False -> Baseline.
    Rueckgabe: (delivered, n_tx). measure akkumuliert pro Knoten die lokal
    beobachtbaren Regler-Inputs (cover-count)."""
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    accepted = {}
    sent = set()
    cover_of = collections.defaultdict(set)
    delivered_flag = False
    seq = 0
    pq = []

    def schedule_send(u, t_send, hops, prev):
        nonlocal seq
        heapq.heappush(pq, (t_send, seq, u, hops, prev))
        seq += 1

    schedule_send(src, 0.0, 0, None)
    accepted[src] = (0, None)

    while pq:
        t_send, _, u, hops_u, prev_u = heapq.heappop(pq)
        node_max = FLOOD_MAX_MHR if (u in mhr_set) else FLOOD_MAX_BASE
        if hops_u >= node_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:
            continue

        if measure is not None and (u in mhr_set) and u != src and u != dst:
            cs = cover_of.get(u, set())
            m = measure[u]
            m["cover_sum"] += len(cs)
            m["cover_n"] += 1
            if len(cs) >= SAFE_K_COVER:
                m["redundant_n"] += 1

        if adapt_on and (u in mhr_set) and u != src and u != dst:
            cover_senders = cover_of.get(u, set())
            cover_snr = {x: SNR.get((u, x), -99.0) for x in cover_senders}
            if guards_pass(u, cover_senders, cover_snr, node_prob, known_nbr, rstate):
                continue

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
                delivered_flag = True
            if v not in accepted:
                accepted[v] = (out_hops, u)
                d = rebroadcast_delay(out_hops, v in mhr_set, rstate)
                schedule_send(v, t_send + d, out_hops, u)

    delivered = (dst in accepted) or delivered_flag
    return delivered, len(sent)


# ======================================================================================
# 4) PAARE / MHR-AUSWAHL
# ======================================================================================
top_traffic_order = sorted(giant_list,
                           key=lambda pk: (NODE.get(pk, {}).get("advert", 0),
                                           DEG.get(pk, 0)), reverse=True)


def select_mhr(alpha, single=False):
    if single:
        return set(top_traffic_order[:1])
    if alpha <= 0.0:
        return set()
    if alpha >= 0.999:
        return set(giant_list)
    n = len(giant_list)
    k = max(1, int(round(alpha * n)))
    return set(top_traffic_order[:k])


def make_pairs(seed, n_pairs):
    rstate = np.random.default_rng(seed * 7919 + 13)
    arr = list(giant_list)
    pairs = []
    for _ in range(n_pairs):
        i, j = rstate.choice(len(arr), size=2, replace=False)
        pairs.append((arr[i], arr[j]))
    return pairs


# ======================================================================================
# 5) DIE LOKALE KALIBRIERUNG (Strategie c) — pro Knoten, langsam (12-48h Tick)
# ======================================================================================
# Jeder Knoten beobachtet ueber N_CALIB_TICKS passiv:
#   - eigene lokale Dichte (Grad, statisch beobachtbar)
#   - gehoerte Redundanz: mittlere Cover-Kopien je gehoerter Flood (cover_mean)
#   - Redundanz-Stabilitaet/Churn (Streuung der Cover-Kopien ueber Ticks)
# Daraus setzt er EINEN supp_prob-Grundwert (kalibriert), bounded [P_MIN,P_MAX].
#
# VOLLE Kennlinie (Strategie c, "reich"): glatte Abbildung aller drei Features.
#   Idee: hoher Grad + viel stabile Redundanz -> p hoch (aggressiv schweigen).
#         niedriger Grad ODER wenig/instabile Redundanz -> p niedrig (konservativ).
def calib_full(degree, cover_mean, cover_cv):
    """Volle Feature->p Abbildung (Strategie c).
    degree    : lokaler Grad
    cover_mean: mittlere gehoerte Cover-Sender je Flood (Redundanz-Tiefe)
    cover_cv  : Variationskoeffizient der Cover-Kopien ueber Ticks (Churn/Instabilitaet)
    Liefert p in [P_MIN, P_MAX]."""
    # Dichte-Score: sigmoid um den Median-Grad; Hubs -> ~1, Bruecken -> ~0
    dens = 1.0 / (1.0 + math.exp(-(degree - 4.0) / 2.0))
    # Redundanz-Score: wie viel Cover ueber dem k_cover-Minimum? saturiert.
    red = max(0.0, min(1.0, (cover_mean - SAFE_K_COVER) / 4.0))
    # Stabilitaets-Score: hohe Streuung (cv) -> abwerten (instabile Redundanz = riskant)
    stab = max(0.0, 1.0 - cover_cv)
    # Gewichtete Kombination, dann auf [P_MIN,P_MAX] mappen.
    score = 0.45 * dens + 0.40 * red + 0.15 * stab
    p = P_MIN + (P_MAX - P_MIN) * score
    return float(np.clip(p, P_MIN, P_MAX))


# ABGESPECKTE on-device-Kennlinie: stueckweise-konstante Lookup aus NUR 2 lokal
# messbaren Groessen (Grad-Band x Redundanz-Band). 3x3 = 9 Bytes (p*100 als uint8).
# Wird unten datengetrieben aus calib_full quantisiert/abgeglichen, dann fix gesetzt.
LOOKUP_DEG_EDGES = [3.0, 6.0]      # Grad-Baender: <3 (eh G1-gesperrt), 3-5, >=6
LOOKUP_RED_EDGES = [2.5, 4.0]      # Redundanz-Baender (cover_mean): <2.5, 2.5-3.9, >=4
# Lookup-Tabelle (deg_band x red_band) -> p. Datengetrieben unten gefuellt.
LOOKUP_TABLE = None  # wird in build_lookup() gesetzt


def deg_band(degree):
    if degree < LOOKUP_DEG_EDGES[0]:
        return 0
    if degree < LOOKUP_DEG_EDGES[1]:
        return 1
    return 2


def red_band(cover_mean):
    if cover_mean < LOOKUP_RED_EDGES[0]:
        return 0
    if cover_mean < LOOKUP_RED_EDGES[1]:
        return 1
    return 2


def calib_lookup(degree, cover_mean, cover_cv):
    """On-device-Variante: 2 Features (Grad, Redundanz) -> 3x3-Lookup. cover_cv ignoriert."""
    return LOOKUP_TABLE[deg_band(degree)][red_band(cover_mean)]


def build_lookup():
    """Quantisiere calib_full auf das 3x3-Grid. Fuer jede (deg_band,red_band)-Zelle
    nimm das mittlere calib_full ueber repraesentative Feature-Werte in der Zelle.
    Ergebnis: 9 p-Werte, on-device als uint8 (p*100) speicherbar."""
    deg_reps = [2.0, 4.5, 8.0]              # Repraesentanten je Grad-Band
    red_reps = [2.0, 3.0, 5.0]              # Repraesentanten je Redundanz-Band
    tbl = []
    for db_, dr in enumerate(deg_reps):
        row = []
        for rb_, rr in enumerate(red_reps):
            # cv-neutral (mittlere Stabilitaet) fuer die abgespeckte Kennlinie
            row.append(calib_full(dr, rr, 0.3))
        tbl.append(row)
    return tbl


# ======================================================================================
# 6) KALIBRIER-PHASE: jeder Knoten misst seine lokalen Features ueber N_CALIB_TICKS
# ======================================================================================
def measure_local_features(mhr_set, known_nbr, seeds, n_ticks):
    """Laeuft eine Kalibrier-Phase: ueber n_ticks Floods mit STATISCHEM p (Beobachtung),
    sammelt pro Knoten cover_mean (Redundanz-Tiefe) + cover_cv (Stabilitaet ueber Ticks).
    Reproduzierbar. Liefert {u: (degree, cover_mean, cover_cv)}."""
    # Pro Knoten ueber Ticks: Liste der mittleren Cover-Kopien je Tick.
    per_tick_cover = {u: [] for u in mhr_set}
    static_prob = {u: P_STATIC for u in mhr_set}
    for seed in seeds:
        pairs = make_pairs(seed, N_PAIRS)
        for tick in range(n_ticks):
            measure = {u: {"cover_sum": 0, "cover_n": 0, "redundant_n": 0} for u in mhr_set}
            crn = (seed * 2654435761 + tick * 40503) % (2 ** 63)
            for j, (s, d) in enumerate(pairs):
                rstate = np.random.default_rng((crn + j * 2862933555777941757) % (2 ** 63))
                run_flood(s, d, mhr_set, rstate, static_prob, known_nbr, True,
                          measure=measure)
            for u in mhr_set:
                m = measure[u]
                if m["cover_n"] > 0:
                    per_tick_cover[u].append(m["cover_sum"] / m["cover_n"])
    feats = {}
    for u in mhr_set:
        cov = per_tick_cover[u]
        if cov:
            cm = float(np.mean(cov))
            cstd = float(np.std(cov))
            ccv = cstd / cm if cm > 1e-9 else 1.0
        else:
            cm, ccv = 0.0, 1.0   # nie gehoert -> keine Redundanz-Evidenz -> konservativ
        feats[u] = (DEG.get(u, 0), cm, ccv)
    return feats


# ======================================================================================
# 7) EVAL einer Strategie bei einem alpha (Common Random Numbers, per-Klasse Delivery)
# ======================================================================================
def eval_strategy(mode, mhr_set, node_prob, known_nbr, pairs, crn_base,
                  dead_nodes=None, dead_edges=None):
    """mode: 'baseline'|'guarded'. node_prob = per-Knoten p (bei guarded).
    Liefert: gesamt deliv, gesamt air, und per-Klasse deliv/air (Klasse = die des ZIELS d).
    CRN: jeder Flood j nutzt einen aus (crn_base,j) abgeleiteten Stream -> gepaart ueber Modi."""
    adapt_on = (mode != "baseline")
    deliv, air = [], []
    cls_deliv = {c: [] for c in CLASSES}
    cls_air = {c: [] for c in CLASSES}
    for j, (s, d) in enumerate(pairs):
        rstate = np.random.default_rng((crn_base + j * 2862933555777941757) % (2 ** 63))
        ok, ntx = run_flood(s, d, mhr_set, rstate, node_prob, known_nbr, adapt_on,
                            dead_nodes=dead_nodes, dead_edges=dead_edges)
        deliv.append(1.0 if ok else 0.0)
        c = CLASS.get(d, "mittel")
        cls_deliv[c].append(1.0 if ok else 0.0)
        if ok:
            air.append(ntx)
            cls_air[c].append(ntx)
    dm = float(np.mean(deliv)) if deliv else 0.0
    am = float(np.mean(air)) if air else 0.0
    out = {"deliv": dm, "air": am}
    for c in CLASSES:
        out[f"deliv_{c}"] = float(np.mean(cls_deliv[c])) if cls_deliv[c] else float("nan")
        out[f"air_{c}"] = float(np.mean(cls_air[c])) if cls_air[c] else float("nan")
    return out


# ======================================================================================
# 8) HAUPT-VERGLEICH ueber den Adoptions-Sweep
# ======================================================================================
log("\n=== 8) Haupt-Vergleich: (a) Baseline / (b) statisch / (c) lokal-kalibriert ===")
LOOKUP_TABLE = build_lookup()
log("  On-Node Lookup-Tabelle (3x3, deg-Band x red-Band) -> supp_prob:")
for i, dr in enumerate(["<3", "3-5", ">=6"]):
    log(f"    deg {dr:>4}: " + "  ".join(f"{LOOKUP_TABLE[i][j]:.2f}" for j in range(3))
        + "   (red <2.5 / 2.5-3.9 / >=4)")

seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
known_nbr = NBR  # perfektes 2-Hop-Wissen (validiert; G1 deckt Luecken ab)

# Sweep-Punkte: SINGLE_NODE plus die ALPHAS>0
sweep_points = ([("single", None)] if SINGLE_NODE else []) + \
               [(a, a) for a in ALPHAS if a > 0.0]

results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS,
    "n_calib_ticks": N_CALIB_TICKS, "fast": FAST,
    "safe_window": dict(k_cover=SAFE_K_COVER, min_degree=SAFE_MIN_DEGREE,
                        snr_floor=SAFE_SNR_FLOOR, p_min=P_MIN, p_max=P_MAX,
                        p_static=P_STATIC),
    "topology": {"giant_nodes": GC.number_of_nodes(), "giant_edges": GC.number_of_edges(),
                 "avg_degree": float(statistics.mean(degs)),
                 "class_quantiles": {"q40_deg": Q_LOW, "q80_deg": Q_HIGH},
                 "class_counts": dict(class_counts)},
    "lookup_table": LOOKUP_TABLE,
    "lookup_edges": {"deg": LOOKUP_DEG_EDGES, "red": LOOKUP_RED_EDGES},
    "sweep": [],
}

# Wir messen 4 Strategien: baseline, static, local_full (c), local_lookup (c-abgespeckt).
STRATS = ["baseline", "static", "local_full", "local_lookup"]


def aggregate_over_seeds(per_seed):
    """per_seed: list of dicts (eine pro Seed) -> mean + sem je key."""
    keys = per_seed[0].keys()
    out = {}
    for k in keys:
        vals = np.array([d[k] for d in per_seed], dtype=float)
        vals = vals[~np.isnan(vals)]
        out[k] = float(np.mean(vals)) if vals.size else float("nan")
        out[k + "_sem"] = (float(np.std(vals, ddof=1) / math.sqrt(vals.size))
                           if vals.size >= 2 else 0.0)
    return out


for sp_label, sp_alpha in sweep_points:
    single = (sp_label == "single")
    alpha = 1.0 if single else sp_alpha
    mhr_set = select_mhr(alpha, single=single) & giant_set

    # Per-Knoten p fuer die lokal-kalibrierten Strategien (EINMAL kalibriert, langsam).
    if mhr_set:
        feats = measure_local_features(mhr_set, known_nbr, seeds, N_CALIB_TICKS)
        p_full = {u: calib_full(*feats[u]) for u in mhr_set}
        p_lookup = {u: calib_lookup(*feats[u]) for u in mhr_set}
    else:
        feats, p_full, p_lookup = {}, {}, {}
    p_static = {u: P_STATIC for u in mhr_set}

    per_seed = {st: [] for st in STRATS}
    for seed in seeds:
        pairs = make_pairs(seed, N_PAIRS)
        crn_base = (seed * 2654435761 + 99991) % (2 ** 63)
        per_seed["baseline"].append(
            eval_strategy("baseline", mhr_set, {}, known_nbr, pairs, crn_base))
        per_seed["static"].append(
            eval_strategy("guarded", mhr_set, p_static, known_nbr, pairs, crn_base))
        per_seed["local_full"].append(
            eval_strategy("guarded", mhr_set, p_full, known_nbr, pairs, crn_base))
        per_seed["local_lookup"].append(
            eval_strategy("guarded", mhr_set, p_lookup, known_nbr, pairs, crn_base))

    agg = {st: aggregate_over_seeds(per_seed[st]) for st in STRATS}

    # Airtime-Gewinn relativ zur Baseline (gesamt + per Klasse)
    def gain(st, key="air"):
        b = agg["baseline"][key]; v = agg[st][key]
        return 100.0 * (b - v) / b if (b and not math.isnan(b) and not math.isnan(v)) else 0.0

    # mittleres p je Klasse (nur local_full / static)
    p_by_class = {st: {} for st in ("static", "local_full", "local_lookup")}
    for c in CLASSES:
        cl_nodes = [u for u in mhr_set if CLASS.get(u) == c]
        for st, pdict in (("static", p_static), ("local_full", p_full),
                          ("local_lookup", p_lookup)):
            pvals = [pdict[u] for u in cl_nodes] if cl_nodes else []
            p_by_class[st][c] = float(np.mean(pvals)) if pvals else float("nan")

    entry = {
        "label": sp_label, "alpha": alpha, "n_mhr": len(mhr_set),
        "agg": agg,
        "airtime_gain": {st: gain(st) for st in ("static", "local_full", "local_lookup")},
        "airtime_gain_by_class": {
            st: {c: gain(st, f"air_{c}") for c in CLASSES}
            for st in ("static", "local_full", "local_lookup")},
        "p_by_class": p_by_class,
    }
    results["sweep"].append(entry)

    # Log
    b = agg["baseline"]
    log(f"\n  --- {sp_label} (alpha={alpha:.2f}, MHR={len(mhr_set)}) ---")
    log(f"    baseline    deliv={b['deliv']:.4f}  air={b['air']:.1f}")
    for st, nm in (("static", "statisch"), ("local_full", "lokal-voll"),
                   ("local_lookup", "lokal-lookup")):
        a = agg[st]
        log(f"    {nm:<12} deliv={a['deliv']:.4f} (Δ{a['deliv']-b['deliv']:+.4f}) "
            f"air={a['air']:.1f}  gain={entry['airtime_gain'][st]:+.1f}%")
    log("    per Klasse Airtime-Gewinn (statisch -> lokal-voll):")
    for c in CLASSES:
        gs = entry["airtime_gain_by_class"]["static"][c]
        gf = entry["airtime_gain_by_class"]["local_full"][c]
        ps = entry["p_by_class"]["static"][c]
        pf = entry["p_by_class"]["local_full"][c]
        log(f"      {c:<14} air {gs:+.1f}% -> {gf:+.1f}%   (p: {ps:.2f} -> {pf:.2f})")


# ======================================================================================
# 9) SAFETY-INVARIANTE (gesamt + per Klasse, ueber alle Sweep-Punkte)
# ======================================================================================
log("\n=== 9) Safety-Invariante: Lieferquote >= Baseline (gesamt + JEDE Klasse) ===")


def safety_check():
    """Prueft fuer local_full + local_lookup + static: deliv >= baseline_deliv - tol
    bei JEDEM Sweep-Punkt und in JEDER Klasse. tol = max(2*SEM_baseline, 0.005)."""
    rep = {st: {"overall_ok": True, "class_ok": True, "worst_overall": 99.0,
                "worst_class": 99.0, "worst_class_where": None}
           for st in ("static", "local_full", "local_lookup")}
    for entry in results["sweep"]:
        agg = entry["agg"]
        b = agg["baseline"]
        for st in ("static", "local_full", "local_lookup"):
            a = agg[st]
            tol = max(2 * b.get("deliv_sem", 0.0), 0.005)
            dlt = a["deliv"] - b["deliv"]
            if dlt < rep[st]["worst_overall"]:
                rep[st]["worst_overall"] = dlt
            if dlt < -tol:
                rep[st]["overall_ok"] = False
            for c in CLASSES:
                bc = b.get(f"deliv_{c}", float("nan"))
                ac = a.get(f"deliv_{c}", float("nan"))
                if math.isnan(bc) or math.isnan(ac):
                    continue
                tolc = max(2 * b.get(f"deliv_{c}_sem", 0.0), 0.01)
                dltc = ac - bc
                if dltc < rep[st]["worst_class"]:
                    rep[st]["worst_class"] = dltc
                    rep[st]["worst_class_where"] = f"{entry['label']}/{c}"
                if dltc < -tolc:
                    rep[st]["class_ok"] = False
    return rep


safety = safety_check()
results["safety"] = safety
for st in ("static", "local_full", "local_lookup"):
    s = safety[st]
    log(f"  {st:<13} overall_safe={s['overall_ok']} (worst Δdeliv={s['worst_overall']:+.4f})  "
        f"class_safe={s['class_ok']} (worst {s['worst_class']:+.4f} @ {s['worst_class_where']})")


# ======================================================================================
# 10) GO/NO-GO: lokal-kalibriert vs. statisch (signifikant + sicher?)
# ======================================================================================
log("\n=== 10) GO/NO-GO: lokal-kalibriert vs. statisch ===")
# Nutzen-Kennzahl: Extra-Airtime-Gewinn (pp) von local_full ueber static, gewichtet auf
# HOHE Adoption (alpha>=0.5, wo Suppression ueberhaupt wirkt) — analog SUPPRESSION_VALIDATION.
high_pts = [e for e in results["sweep"] if (e["alpha"] >= 0.5)]
extra_full = [e["airtime_gain"]["local_full"] - e["airtime_gain"]["static"] for e in high_pts]
extra_lookup = [e["airtime_gain"]["local_lookup"] - e["airtime_gain"]["static"] for e in high_pts]
mean_extra_full = float(np.mean(extra_full)) if extra_full else 0.0
mean_extra_lookup = float(np.mean(extra_lookup)) if extra_lookup else 0.0

# Per-Klasse: holt local_full bei HUBS mehr Airtime als static, OHNE Bruecken-Delivery zu senken?
hub_extra = []
bridge_deliv_delta = []
for e in high_pts:
    hub_extra.append(e["airtime_gain_by_class"]["local_full"]["hub"]
                     - e["airtime_gain_by_class"]["static"]["hub"])
    bd = (e["agg"]["local_full"].get("deliv_bruecke_blatt", float("nan"))
          - e["agg"]["baseline"].get("deliv_bruecke_blatt", float("nan")))
    if not math.isnan(bd):
        bridge_deliv_delta.append(bd)
mean_hub_extra = float(np.mean(hub_extra)) if hub_extra else 0.0
worst_bridge_deliv = float(np.min(bridge_deliv_delta)) if bridge_deliv_delta else 0.0

USEFUL_THRESHOLD_PP = 2.0
safe_full = safety["local_full"]["overall_ok"] and safety["local_full"]["class_ok"]
useful_full = mean_extra_full >= USEFUL_THRESHOLD_PP
go_full = bool(safe_full and useful_full)

# Behaltener Gewinn-Anteil der abgespeckten on-device-Kennlinie
retained = (100.0 * mean_extra_lookup / mean_extra_full) if mean_extra_full > 1e-9 else 0.0

results["decision"] = {
    "mean_extra_full_pp_high_alpha": mean_extra_full,
    "mean_extra_lookup_pp_high_alpha": mean_extra_lookup,
    "mean_hub_extra_pp": mean_hub_extra,
    "worst_bridge_deliv_delta": worst_bridge_deliv,
    "local_full_safe": safe_full,
    "useful_threshold_pp": USEFUL_THRESHOLD_PP,
    "go_local_full": go_full,
    "lookup_retained_gain_pct": retained,
}
log(f"  Extra Airtime lokal-voll vs statisch (alpha>=0.5, Mittel): {mean_extra_full:+.2f} pp")
log(f"  Davon bei HUBS: {mean_hub_extra:+.2f} pp;  Bruecken-Delivery worst Δ: {worst_bridge_deliv:+.4f}")
log(f"  Safety lokal-voll (overall+class): {safe_full}")
log(f"  ==> ENTSCHEIDUNG lokal-kalibriert: {'GO' if go_full else 'NO-GO'} "
    f"(Mehrwert>={USEFUL_THRESHOLD_PP}pp={useful_full}, safe={safe_full})")
log(f"  On-Node-Lookup behaelt {retained:.0f}% des vollen Mehrwerts "
    f"({mean_extra_lookup:+.2f} pp).")


# ======================================================================================
# 11) INTERVALL-SWEEP (12/24/48h als Kalibrier-Ticks) + ADAPTIVES INTERVALL
#     Nur sinnvoll wenn local_full ueberhaupt etwas bringt — sonst informativ markiert.
# ======================================================================================
log("\n=== 11) Intervall-Sweep (Kalibrier-Stabilitaet) + adaptives Intervall ===")
# Modell: 12h/24h/48h entsprechen unterschiedlich vielen gemittelten Kalibrier-Ticks.
#   - kurzes Intervall (12h): WENIGE Ticks gemittelt -> reaktiver, aber verrauschter
#     (Gefahr: instabile/zufaellig hohe Redundanz -> zu aggressiv -> Safety-Marge sinkt).
#   - langes Intervall (48h): VIELE Ticks gemittelt -> stabil, aber traeger.
# Wir vergleichen die Kalibrier-FEATURE-Stabilitaet + den resultierenden Gewinn/Safety bei
# alpha=1.0 (voller Adoption, wo es wirkt).
INTERVAL_TICKS = {"12h": max(2, N_CALIB_TICKS // 4),
                  "24h": max(4, N_CALIB_TICKS // 2),
                  "48h": N_CALIB_TICKS}
interval_res = {}
mhr_all = set(giant_list)
p_static_all = {u: P_STATIC for u in mhr_all}
for iv_name, iv_ticks in INTERVAL_TICKS.items():
    feats_iv = measure_local_features(mhr_all, known_nbr, seeds, iv_ticks)
    p_iv = {u: calib_full(*feats_iv[u]) for u in mhr_all}
    # Feature-Stabilitaet: mittlerer cover_cv (Streuung der gemessenen Redundanz)
    mean_cv = float(np.mean([feats_iv[u][2] for u in mhr_all]))
    # Gewinn + Safety bei alpha=1.0
    per_seed_b, per_seed_iv = [], []
    for seed in seeds:
        pairs = make_pairs(seed, N_PAIRS)
        crn_base = (seed * 2654435761 + 99991) % (2 ** 63)
        per_seed_b.append(eval_strategy("baseline", mhr_all, {}, known_nbr, pairs, crn_base))
        per_seed_iv.append(eval_strategy("guarded", mhr_all, p_iv, known_nbr, pairs, crn_base))
    ab = aggregate_over_seeds(per_seed_b)
    av = aggregate_over_seeds(per_seed_iv)
    gain_iv = 100.0 * (ab["air"] - av["air"]) / ab["air"] if ab["air"] else 0.0
    # static gain bei alpha=1.0 (Referenz)
    per_seed_s = []
    for seed in seeds:
        pairs = make_pairs(seed, N_PAIRS)
        crn_base = (seed * 2654435761 + 99991) % (2 ** 63)
        per_seed_s.append(eval_strategy("guarded", mhr_all, p_static_all, known_nbr, pairs, crn_base))
    as_ = aggregate_over_seeds(per_seed_s)
    gain_static = 100.0 * (ab["air"] - as_["air"]) / ab["air"] if ab["air"] else 0.0
    interval_res[iv_name] = {
        "calib_ticks": iv_ticks, "mean_cover_cv": mean_cv,
        "airtime_gain_pct": gain_iv, "static_gain_pct": gain_static,
        "extra_vs_static_pp": gain_iv - gain_static,
        "deliv": av["deliv"], "baseline_deliv": ab["deliv"],
        "deliv_delta": av["deliv"] - ab["deliv"],
    }
    log(f"  {iv_name} ({iv_ticks} Ticks): gain={gain_iv:+.1f}% "
        f"(extra vs stat {gain_iv-gain_static:+.2f}pp)  cover_cv={mean_cv:.3f}  "
        f"deliv Δ={av['deliv']-ab['deliv']:+.4f}")

# Adaptives Intervall: schneller re-kalibrieren wo cover_cv hoch (instabile Lage), langsamer
# wo stabil. Modell: pro Knoten waehle die ANZAHL gemittelter Ticks invers zur lokalen
# Stabilitaet -> stabile Hubs nutzen lange (48h)-Schaetzung, unruhige Knoten kurze (12h).
# Wir messen, ob das den Gewinn/Safety ggue. dem BESTEN festen Intervall verbessert.
feats_short = measure_local_features(mhr_all, known_nbr, seeds, INTERVAL_TICKS["12h"])
feats_long = measure_local_features(mhr_all, known_nbr, seeds, INTERVAL_TICKS["48h"])
p_adaptive = {}
for u in mhr_all:
    cv_long = feats_long[u][2]
    # hohe Streuung -> nutze die (reaktivere, aber konservativere) Kurz-Schaetzung
    src_feats = feats_short[u] if cv_long > 0.5 else feats_long[u]
    p_adaptive[u] = calib_full(*src_feats)
per_seed_ad, per_seed_b2 = [], []
for seed in seeds:
    pairs = make_pairs(seed, N_PAIRS)
    crn_base = (seed * 2654435761 + 99991) % (2 ** 63)
    per_seed_b2.append(eval_strategy("baseline", mhr_all, {}, known_nbr, pairs, crn_base))
    per_seed_ad.append(eval_strategy("guarded", mhr_all, p_adaptive, known_nbr, pairs, crn_base))
ab2 = aggregate_over_seeds(per_seed_b2)
aad = aggregate_over_seeds(per_seed_ad)
gain_adaptive = 100.0 * (ab2["air"] - aad["air"]) / ab2["air"] if ab2["air"] else 0.0
best_fixed = max(interval_res.values(), key=lambda r: r["airtime_gain_pct"])
adaptive_help_pp = gain_adaptive - best_fixed["airtime_gain_pct"]
results["interval_sweep"] = interval_res
results["adaptive_interval"] = {
    "airtime_gain_pct": gain_adaptive,
    "deliv_delta": aad["deliv"] - ab2["deliv"],
    "best_fixed_gain_pct": best_fixed["airtime_gain_pct"],
    "adaptive_vs_best_fixed_pp": adaptive_help_pp,
}
# Sweetspot = bestes festes Intervall mit gehaltener Lieferquote
sweet = max((r for r in interval_res.items() if r[1]["deliv_delta"] >= -0.01),
            key=lambda kv: kv[1]["airtime_gain_pct"], default=None)
results["interval_sweetspot"] = sweet[0] if sweet else None
log(f"  adaptives Intervall: gain={gain_adaptive:+.1f}%  vs bestes festes "
    f"({best_fixed['calib_ticks']} Ticks, {best_fixed['airtime_gain_pct']:+.1f}%): "
    f"{adaptive_help_pp:+.2f} pp")
log(f"  -> Intervall-Sweetspot (max gain bei deliv>=Baseline): {results['interval_sweetspot']}")


# ======================================================================================
# 12) ON-NODE-MACHBARKEIT: RAM/CPU-Schaetzung der abgespeckten Kennlinie
# ======================================================================================
# Lookup = 3x3 uint8 (p*100) = 9 Bytes. Eval: 2 Vergleiche (deg_band) + 2 (red_band) + 1 Index.
# Features: degree (schon vorhanden), cover_mean (EWMA ueber gehoerte Floods, 1 float/2 Bytes fixpoint).
results["on_node"] = {
    "lookup_bytes": 9,
    "extra_state_bytes_per_node": 4,  # cover_mean EWMA (2B) + tick-counter (2B), degree existiert
    "eval_ops": "≤5 Integer-Vergleiche + 1 Array-Index; kein float, kein Heap, kein Sort",
    "calib_period": "12-48h (Sweetspot s.o.); 1x pro Periode neuer Lookup-Read",
    "features_used": ["degree (lokal, vorhanden)", "cover_mean (EWMA gehoerter Cover-Sender)"],
}
log("\n=== 12) On-Node-Machbarkeit ===")
log(f"  Lookup: {results['on_node']['lookup_bytes']} Bytes (3x3 uint8 p*100), "
    f"+{results['on_node']['extra_state_bytes_per_node']} B Zustand/Knoten (cover_mean EWMA).")
log(f"  Eval: {results['on_node']['eval_ops']}.")


# ======================================================================================
# 13) JSON SCHREIBEN
# ======================================================================================
json.dump(results, open(os.path.join(HERE, "local_calib_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("\n  local_calib_results.json geschrieben.")


# ======================================================================================
# 14) PLOTS
# ======================================================================================
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})
sweep = results["sweep"]
xs = list(range(len(sweep)))
xlabels = [e["label"] if e["label"] == "single" else f"{e['alpha']:.2f}" for e in sweep]
COL = {"baseline": "#333333", "static": "#2980b9",
       "local_full": "#2e7d32", "local_lookup": "#e67e22"}
NAME = {"baseline": "Baseline", "static": "statisch",
        "local_full": "lokal-voll", "local_lookup": "lokal-lookup"}

# --- fig_lc_strategies: deliv + airtime-gain ueber Adoption (a/b/c) ---
fig, axs = plt.subplots(1, 2, figsize=(14, 5))
for st in STRATS:
    d = [e["agg"][st]["deliv"] for e in sweep]
    axs[0].plot(xs, d, "o-", color=COL[st], lw=1.6, label=NAME[st])
axs[0].set_xticks(xs); axs[0].set_xticklabels(xlabels)
axs[0].set_title("Lieferquote ueber Adoption (Safety: alle >= Baseline)")
axs[0].set_xlabel("Adoption (alpha)"); axs[0].set_ylabel("Lieferquote")
axs[0].grid(alpha=0.25); axs[0].legend(fontsize=8)
for st in ("static", "local_full", "local_lookup"):
    g = [e["airtime_gain"][st] for e in sweep]
    axs[1].plot(xs, g, "o-", color=COL[st], lw=1.6, label=NAME[st])
axs[1].set_xticks(xs); axs[1].set_xticklabels(xlabels)
axs[1].set_title("Airtime-Gewinn vs Baseline ueber Adoption")
axs[1].set_xlabel("Adoption (alpha)"); axs[1].set_ylabel("Airtime-Gewinn [%]")
axs[1].grid(alpha=0.25); axs[1].legend(fontsize=8)
fig.suptitle("fig_lc_strategies — (a) Baseline / (b) statisch / (c) lokal-kalibriert")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_lc_strategies.png")); plt.close(fig)

# --- fig_lc_by_class: Airtime-Gewinn + Delivery je Knotenklasse (statisch vs lokal-voll) ---
fig, axs = plt.subplots(2, 3, figsize=(15, 8))
for j, c in enumerate(CLASSES):
    # oben: airtime-gain je Klasse
    axg = axs[0, j]
    for st in ("static", "local_full"):
        g = [e["airtime_gain_by_class"][st][c] for e in sweep]
        axg.plot(xs, g, "o-", color=COL[st], lw=1.5, label=NAME[st])
    axg.set_xticks(xs); axg.set_xticklabels(xlabels, fontsize=7)
    axg.set_title(f"{c} — Airtime-Gewinn [%]")
    axg.set_xlabel("alpha"); axg.set_ylabel("gain [%]")
    axg.grid(alpha=0.25); axg.legend(fontsize=7)
    # unten: delivery je Klasse vs baseline
    axd = axs[1, j]
    db = [e["agg"]["baseline"].get(f"deliv_{c}", float("nan")) for e in sweep]
    axd.plot(xs, db, "o-", color=COL["baseline"], lw=1.5, label="Baseline")
    for st in ("static", "local_full"):
        d = [e["agg"][st].get(f"deliv_{c}", float("nan")) for e in sweep]
        axd.plot(xs, d, "o-", color=COL[st], lw=1.5, label=NAME[st])
    axd.set_xticks(xs); axd.set_xticklabels(xlabels, fontsize=7)
    axd.set_title(f"{c} — Lieferquote (Safety je Klasse)")
    axd.set_xlabel("alpha"); axd.set_ylabel("Lieferquote")
    axd.grid(alpha=0.25); axd.legend(fontsize=7)
fig.suptitle("fig_lc_by_class — Heterogenitaet: Airtime (oben) & Lieferquote (unten) je Klasse")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_lc_by_class.png")); plt.close(fig)

# --- fig_lc_interval: Intervall-Sweep ---
fig, axs = plt.subplots(1, 2, figsize=(13, 5))
iv_names = list(INTERVAL_TICKS.keys())
gains = [interval_res[n]["airtime_gain_pct"] for n in iv_names]
extras = [interval_res[n]["extra_vs_static_pp"] for n in iv_names]
cvs = [interval_res[n]["mean_cover_cv"] for n in iv_names]
dds = [interval_res[n]["deliv_delta"] for n in iv_names]
axs[0].bar(iv_names, gains, color="#2e7d32", alpha=0.7, label="lokal-voll gain%")
axs[0].axhline(interval_res[iv_names[0]]["static_gain_pct"], color="#2980b9", ls="--",
               label="statisch gain%")
axs[0].bar([f"adaptiv"], [gain_adaptive], color="#8e44ad", alpha=0.7)
axs[0].set_title("Airtime-Gewinn je Kalibrier-Intervall (+ adaptiv)")
axs[0].set_ylabel("Airtime-Gewinn [%] @ alpha=1.0")
axs[0].grid(alpha=0.25, axis="y"); axs[0].legend(fontsize=8)
ax2 = axs[1]
ax2.plot(iv_names, cvs, "o-", color="#c0392b", label="cover_cv (Instabilitaet)")
ax2.set_ylabel("mittl. cover_cv", color="#c0392b")
ax2b = ax2.twinx()
ax2b.plot(iv_names, dds, "s-", color="#16a085", label="Δ deliv vs Baseline")
ax2b.set_ylabel("Δ Lieferquote", color="#16a085")
ax2.set_title("Stabilitaet vs Reaktionsfaehigkeit\nlanges Intervall -> stabiler (cv sinkt)")
ax2.grid(alpha=0.25)
fig.suptitle("fig_lc_interval — Kalibrier-Intervall-Sweetspot (12/24/48h) + adaptives Intervall")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_lc_interval.png")); plt.close(fig)

# --- fig_lc_lookup: volle vs abgespeckte Kennlinie ---
fig, axs = plt.subplots(1, 2, figsize=(14, 5))
# links: volle Kennlinie p(degree) bei verschiedenen Redundanz-Tiefen + lookup-Stufen
degs_x = np.linspace(0, 12, 80)
for cm, col in [(2.0, "#3498db"), (3.5, "#2e7d32"), (6.0, "#16a085")]:
    pf = [calib_full(d, cm, 0.3) for d in degs_x]
    axs[0].plot(degs_x, pf, color=col, lw=1.8, label=f"voll, cover_mean={cm}")
    pl = [calib_lookup(d, cm, 0.3) for d in degs_x]
    axs[0].plot(degs_x, pl, color=col, lw=1.2, ls="--", alpha=0.7)
axs[0].axhline(P_STATIC, color="#888", ls=":", label=f"statisch p={P_STATIC}")
axs[0].set_title("Kennlinie p(Grad): voll (durchgezogen) vs Lookup (gestrichelt)")
axs[0].set_xlabel("lokaler Grad"); axs[0].set_ylabel("supp_prob")
axs[0].set_ylim(P_MIN - 0.05, P_MAX + 0.05)
axs[0].grid(alpha=0.25); axs[0].legend(fontsize=7)
# rechts: behaltener Gewinn-Anteil + Mehrwert pro Sweep-Punkt
ef = [e["airtime_gain"]["local_full"] - e["airtime_gain"]["static"] for e in sweep]
el = [e["airtime_gain"]["local_lookup"] - e["airtime_gain"]["static"] for e in sweep]
axs[1].plot(xs, ef, "o-", color="#2e7d32", lw=1.6, label="lokal-voll extra vs stat")
axs[1].plot(xs, el, "s--", color="#e67e22", lw=1.6, label="lokal-lookup extra vs stat")
axs[1].axhline(0, color="#888", lw=0.8)
axs[1].set_xticks(xs); axs[1].set_xticklabels(xlabels, fontsize=7)
axs[1].set_title(f"Extra-Airtime vs statisch [pp]\nLookup behaelt {retained:.0f}% des Mehrwerts")
axs[1].set_xlabel("Adoption (alpha)"); axs[1].set_ylabel("extra Airtime [pp]")
axs[1].grid(alpha=0.25); axs[1].legend(fontsize=8)
fig.suptitle("fig_lc_lookup — volle Strategie (c) vs on-device-Lookup")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_lc_lookup.png")); plt.close(fig)

log("  Plots: fig_lc_strategies.png, fig_lc_by_class.png, fig_lc_interval.png, fig_lc_lookup.png")

log("\n=== KENNZAHLEN-DUMP ===")
log(json.dumps({
    "decision": results["decision"],
    "safety": {st: {"overall_ok": results["safety"][st]["overall_ok"],
                    "class_ok": results["safety"][st]["class_ok"],
                    "worst_class": round(results["safety"][st]["worst_class"], 4)}
               for st in ("static", "local_full", "local_lookup")},
    "interval_sweetspot": results.get("interval_sweetspot"),
    "adaptive_interval_vs_best_fixed_pp": round(
        results["adaptive_interval"]["adaptive_vs_best_fixed_pp"], 3),
}, ensure_ascii=False, indent=2))
log("\n=== FERTIG ===")
