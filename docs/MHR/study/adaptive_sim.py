#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — Validierung eines ADAPTIVEN Flood-Suppression-Reglers (Stufe B+)
================================================================================

Zweck:
  Die guarded Suppression (Guards G1-G5, siehe Suppression_Design.md +
  SUPPRESSION_VALIDATION.md) schuetzt PER PAKET die Lieferquote fuer JEDEN
  Parameterwert. ZUSAETZLICH wird hier ein LANGSAMER aeusserer Regelkreis
  simuliert, der pro Knoten nur die Suppressions-AGGRESSIVITAET (supp_prob,
  G5) INNERHALB des validierten sicheren Fensters nachstellt — anhand rein
  lokal messbarer, passiver Groessen.

  Kern-Argument: Weil die Guards G1-G4 die Abdeckung beweisen BEVOR geschwiegen
  wird, kann die Adaption von supp_prob nur Airtime gegen Sicherheitsmarge
  tauschen — sie kann die Delivery konstruktiv nicht unter Baseline druecken
  (der Default = senden bleibt die sichere Aktion).

  Diese Sim BEWEIST/WIDERLEGT simulativ:
    (i)   Safety ueber ALLE Adaptions-Ticks/Szenarien: Lieferquote >= Baseline.
    (ii)  Konvergenz & Oszillation: supp_prob-Trajektorien (Varianz, Wechselrate).
    (iii) Mehrwert vs. statisch: spart die Adaption messbar mehr Airtime als der
          feste sichere Satz (p=0.8) — oder lohnt sie den Aufwand NICHT?

Baut DIREKT auf suppression_sim.py auf (gleicher echter neighbor-graph, gleiches
Flood-/Guard-Modell, gleiche Reproduzierbarkeit). Wir importieren NICHT, sondern
spiegeln den validierten Harness-Kern hier (eigenstaendig lauffaehig), erweitert
um den Regler-Zustand und die Umwelt-Szenarien ueber Adaptions-Ticks.

Aufruf:  python3 adaptive_sim.py
Env (Debug): ADAPT_FAST=1, ADAPT_SEEDS, ADAPT_PAIRS, ADAPT_TICKS
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
FAST = os.environ.get("ADAPT_FAST", "0") == "1"
# Pro Tick werden N_PAIRS Floods gemessen; >=5 Seeds; viele Adaptions-Ticks je Szenario.
N_PAIRS = int(os.environ.get("ADAPT_PAIRS", "80" if not FAST else "20"))
N_SEEDS = int(os.environ.get("ADAPT_SEEDS", "5" if not FAST else "2"))
N_TICKS = int(os.environ.get("ADAPT_TICKS", "40" if not FAST else "12"))

# Physik / LoRa (identisch suppression_sim.py / v4)
SNR_THR = -12.0
SNR_SCALE = 4.0
FLOOD_MAX_BASE = 64
FLOOD_MAX_MHR = 15
BASE_AIR = 0.10        # (nur Timing; Airtime-Metrik = #Sender, siehe run_flood)
PER_HOP_AIR = 0.012
JITTER = 5.0
TX_HOP_WEIGHT = 0.6

# --- Validiertes sicheres Fenster (aus SUPPRESSION_VALIDATION.md) ---
# Fixe Guards-Parameter (NICHT adaptiert, ausser supp_prob):
SAFE_K_COVER = 2
SAFE_MIN_DEGREE = 3
SAFE_SNR_FLOOR = -6.0
# supp_prob-Fenster: harte Ober-/Untergrenzen. p=0.8 ist der statische Produktionswert.
P_MIN = 0.50          # nie aggressiver als ... nein: p ist Schweige-Wahrscheinlichkeit;
P_MAX = 1.00          # p_max = aggressivste validierte Variante (prob=1.0)
P_STATIC = 0.80       # statischer sicherer Referenzwert
P_INIT = 0.80         # Regler startet konservativ beim statischen Wert
GUARDS_ALL = {"G1", "G2", "G3", "G4", "G5"}


def log(msg):
    print(msg, flush=True)


# ======================================================================================
# 1) REALE TOPOLOGIE (echte Kanten + echtes Per-Link-SNR), wie suppression_sim.py
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json aufbauen ===")
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

# advert_count fuer Churn-Profil aus nodes.json (echt; neighbor_graph hat 0)
adv_raw = {}
try:
    nd = json.load(open(NODES_F))["nodes"]
    for x in nd:
        pk = (x.get("public_key") or "").lower()
        if pk:
            adv_raw[pk] = int(x.get("advert_count", 0) or 0)
except Exception as ex:  # pragma: no cover
    log(f"  WARN nodes.json nicht ladbar ({ex}) -> Churn nutzt neighbor_count-Fallback")

NODE = {}
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    NODE[pk] = {"advert": adv_raw.get(pk, 0),
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
log(f"  Riesenkomponente (Simulation): {GC.number_of_nodes()} Knoten, "
    f"{GC.number_of_edges()} Kanten")
n_adv = sum(1 for u in giant_list if NODE.get(u, {}).get("advert", 0) > 0)
log(f"  advert_count aus nodes.json: {n_adv}/{len(giant_list)} Riesen-Knoten mit Daten")


# ======================================================================================
# 2) FLOOD-MODELL mit GUARDED Suppression (per-Knoten supp_prob)
#    Identisch zu suppression_sim.py, ABER: prob ist pro Knoten (node_prob dict),
#    und wir messen pro Knoten die lokal beobachtbaren Regler-Inputs (Redundanz etc.).
# ======================================================================================
def rebroadcast_delay(hops, is_mhr, rstate):
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


def guards_pass(R, cover_senders, cover_snr, node_prob, known_nbr, rng):
    """Schweigen NUR wenn ALLE Guards erfuellt; sonst senden. supp_prob (G5) ist
    pro Knoten (node_prob[R]); k_cover/min_degree/snr_floor sind fix (sicheres Fenster).
    G1-G4 bleiben IMMER aktiv (G3 nie aus) — nur G5 ist der Regel-Freiheitsgrad."""
    # G1 Low-Degree / Leaf-Schutz
    if DEG.get(R, 0) < SAFE_MIN_DEGREE:
        return False
    # G2 Cover-Count
    if len(cover_senders) < SAFE_K_COVER:
        return False
    # G4 Reliability-Floor
    good = [x for x in cover_senders if cover_snr.get(x, -99.0) >= SAFE_SNR_FLOOR]
    if len(good) < SAFE_K_COVER:
        return False
    cover_eff = set(good)
    # G3 Neighbour-Coverage (load-bearing; nie deaktiviert)
    my_nbrs = known_nbr.get(R, set())
    covered = set()
    for x in cover_eff:
        covered |= known_nbr.get(x, set())
        covered.add(x)
    for nb in my_nbrs:
        if nb == R:
            continue
        if nb not in covered:
            return False
    # G5 Prob-Margin (DER adaptive Freiheitsgrad)
    if rng.random() >= node_prob.get(R, P_STATIC):
        return False
    return True


def run_flood(src, dst, mhr_set, rstate, node_prob, known_nbr, adapt_on,
              dead_nodes=None, dead_edges=None, measure=None):
    """Timing-getriebener Flood. adapt_on=False -> Baseline (kein Suppress).
    measure: optionales dict, in das pro Knoten lokal beobachtbare Groessen
    akkumuliert werden (cover_count_sum, cover_count_n, heard_flood) — fuer den Regler.
    Rueckgabe: (delivered, n_tx)."""
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

        # Lokale Mess-Erfassung (passiv): wie viele Cover-Sender hat u beim Senden gehoert?
        if measure is not None and (u in mhr_set) and u != src and u != dst:
            cs = cover_of.get(u, set())
            m = measure[u]
            m["cover_sum"] += len(cs)
            m["cover_n"] += 1
            if len(cs) >= SAFE_K_COVER:
                m["redundant_n"] += 1

        # SUPPRESSION (nur MHR-Knoten, nicht src/dst)
        if adapt_on and (u in mhr_set) and u != src and u != dst:
            cover_senders = cover_of.get(u, set())
            cover_snr = {x: SNR.get((u, x), -99.0) for x in cover_senders}
            if guards_pass(u, cover_senders, cover_snr, node_prob, known_nbr, rstate):
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
                delivered_flag = True
            if v not in accepted:
                accepted[v] = (out_hops, u)
                d = rebroadcast_delay(out_hops, v in mhr_set, rstate)
                schedule_send(v, t_send + d, out_hops, u)

    delivered = (dst in accepted) or delivered_flag
    return delivered, len(sent)


# ======================================================================================
# 3) PAARE / MHR-AUSWAHL / 2-HOP-WISSEN
# ======================================================================================
top_traffic_order = sorted(giant_list,
                           key=lambda pk: (NODE.get(pk, {}).get("advert", 0),
                                           DEG.get(pk, 0)), reverse=True)


def select_mhr(alpha, seed):
    """alpha=1.0 -> alle MHR (konservativster, interessantester Fall fuer Adaption)."""
    if alpha >= 0.999:
        return set(giant_list)
    rstate = np.random.default_rng(seed * 100003 + 7)
    n = len(giant_list)
    k = max(1, int(round(alpha * n)))
    return set(top_traffic_order[:k])


def make_pairs(seed, tick, n_pairs):
    """Paare variieren je Tick (Traffic ist nicht statisch)."""
    rstate = np.random.default_rng(seed * 7919 + 13 + tick * 101)
    arr = list(giant_list)
    pairs = []
    for _ in range(n_pairs):
        i, j = rstate.choice(len(arr), size=2, replace=False)
        pairs.append((arr[i], arr[j]))
    return pairs


# ======================================================================================
# 4) DER ADAPTIVE REGLER (pro Knoten)
# ======================================================================================
# Zustand je Knoten: node_prob[u] in [P_MIN, P_MAX], gestartet bei P_INIT.
# Lokale, PASSIV messbare Inputs je Adaptions-Tick (aus run_flood-measure):
#   - redundancy_ratio = Anteil gehoerter Floods, in denen u >= k_cover Cover-Sender hatte
#   - mean_cover       = mittlere Cover-Kopien je gehoerter Flood (Redundanz-Tiefe)
#   - degree           = lokaler Grad (Dichte-Proxy, statisch hier, dynamisch unter Churn/Linkausfall via erreichbarer Nachbarn)
#   - churn_signal     = Anteil Nachbarn, die seit letztem Tick verschwunden/neu sind
#
# Update-Regel (TCP-artig, asymmetrisch, gedaempft, bounded, Hysterese):
#   * REICHLICHE, STABILE Redundanz (redundancy_ratio hoch, kein Churn):
#       langsam VOR -> p += STEP_UP            (additive increase)
#   * SINKENDE Redundanz ODER Churn ODER Grad-Einbruch:
#       schnell ZURUECK -> p *= BETA           (multiplicative decrease)
#   * Dazwischen (Hysterese-Totband): keine Aenderung.
# Harte Clamps auf [P_MIN, P_MAX]. G1-G4 bleiben immer aktiv (Safety unabhaengig von p).
STEP_UP = 0.04          # additive increase (langsam vor)
BETA = 0.70             # multiplicative decrease (schnell zurueck)
RED_HI = 0.70           # Hysterese: ueber diesem Redundanz-Anteil -> vorsichtig vor
RED_LO = 0.45           # unter diesem -> zurueck
CHURN_TRIG = 0.10       # >10 % Nachbar-Churn -> sofort zurueck
EWMA_A = 0.5            # Glaettung der gemessenen Redundanz (Daempfung)


def controller_update(p, red_ewma, churn_sig, degree, base_degree):
    """Liefert neues p (geclamped). Reine Skalar-Arithmetik (firmware-tauglich:
    wenige floats, kein Heap, kein Sort). Asymmetrisch + Hysterese + bounded."""
    # Grad-Einbruch (Dichte schrumpft / Links weg) ist ein harter Rueckwaerts-Trigger
    degree_drop = (base_degree > 0) and (degree < 0.7 * base_degree)
    if churn_sig > CHURN_TRIG or degree_drop or red_ewma < RED_LO:
        # schnell zurueck auf konservativ
        p = max(P_MIN, p * BETA)
    elif red_ewma > RED_HI:
        # langsam vor (reichliche, stabile Redundanz)
        p = min(P_MAX, p + STEP_UP)
    # Totband [RED_LO, RED_HI] ohne harte Trigger -> p bleibt (Hysterese)
    return float(np.clip(p, P_MIN, P_MAX))


# ======================================================================================
# 5) UMWELT-SZENARIEN ueber Adaptions-Ticks
# ======================================================================================
# Jedes Szenario liefert je Tick: (traffic_scale, dead_nodes, dead_edges, density_mask).
# density_mask: Menge AKTIVER Knoten (Dichte-Verschiebung schaltet Region zu/ab).
def scenario_state(name, tick, n_ticks, seed):
    """Gibt (traffic_scale, dead_nodes, dead_edges) fuer diesen Tick zurueck.
    Reproduzierbar je (seed, tick)."""
    rng = np.random.default_rng(seed * 13 + tick * 7 + hash(name) % 9973)
    traffic = 1.0
    dead_nodes, dead_edges = set(), set()

    if name == "tag_nacht":
        # Periodische Last (Tag/Nacht): Traffic-Rate schwankt sinusfoermig.
        phase = 2 * math.pi * tick / max(1, (n_ticks / 3.0))
        traffic = 0.6 + 0.4 * (0.5 * (1 + math.sin(phase)))  # 0.6..1.0

    elif name == "churn":
        # Knoten-Churn nach advert_count-Profil: selten gehoerte Knoten gehen an/aus.
        order = sorted(giant_list, key=lambda pk: NODE.get(pk, {}).get("advert", 0))
        k = int(0.05 * len(order))
        cand = order[:max(k * 4, k)]
        if cand:
            # je Tick andere Auswahl -> echtes Kommen/Gehen
            frac = 0.5 + 0.5 * math.sin(2 * math.pi * tick / 6.0)  # variiert Intensitaet
            kk = max(1, int(k * frac))
            sel = rng.choice(len(cand), size=min(kk, len(cand)), replace=False)
            dead_nodes = set(cand[i] for i in sel)

    elif name == "dichte_shift":
        # Region waechst/schrumpft: eine raeumliche Region (per Knoten-Index-Block)
        # wird ueber die Ticks zu-/abgeschaltet (Dichte-Verschiebung).
        n = len(giant_list)
        region = giant_list[: n // 3]  # "wachsende/schrumpfende" Region
        # erste Haelfte der Ticks: Region schrumpft (mehr tot), dann waechst wieder
        half = n_ticks / 2.0
        if tick < half:
            frac = tick / half            # 0 -> 1 (schrumpft)
        else:
            frac = (n_ticks - tick) / half  # 1 -> 0 (waechst zurueck)
        kk = int(frac * len(region))
        if kk > 0:
            sel = rng.choice(len(region), size=min(kk, len(region)), replace=False)
            dead_nodes = set(region[i] for i in sel)

    elif name == "linkausfall":
        # Transienter Linkausfall 10/20 %: tritt in einem Fenster auf, dann Erholung.
        all_e = list(GC.edges())
        # Ausfall-Fenster: mittleres Drittel der Ticks, Spitze 20 %
        t0, t1 = n_ticks // 3, 2 * n_ticks // 3
        if t0 <= tick < t1:
            frac = 0.20 if (tick - t0) >= (t1 - t0) // 2 else 0.10
            k = int(frac * len(all_e))
            if all_e and k > 0:
                sel = rng.choice(len(all_e), size=k, replace=False)
                dead_edges = set(frozenset(all_e[i]) for i in sel)

    return traffic, dead_nodes, dead_edges


SCENARIOS = ["tag_nacht", "churn", "dichte_shift", "linkausfall"]


# ======================================================================================
# 6) EIN TICK auswerten (Baseline / statisch / adaptiv)
# ======================================================================================
def eval_tick(mode, mhr_set, node_prob, known_nbr, pairs, crn_base,
              dead_nodes, dead_edges, traffic_scale, measure):
    """mode: 'baseline' (kein Suppress) | 'static' | 'adaptive'.
    traffic_scale skaliert die Zahl der gemessenen Floods (Tag/Nacht-Last).
    measure: dict node-> {cover_sum,cover_n,redundant_n} (nur bei adaptive/static genutzt).

    WICHTIG (Common Random Numbers): jeder Flood j bekommt einen DETERMINISTISCH aus
    (crn_base, j) abgeleiteten RNG-Stream. baseline/static/adaptive sehen so EXAKT die
    gleichen Floods (gleiche Link-/Delay-Zufallszahlen) -> Deliveries sind GEPAART. Damit
    misst der Vergleich den ECHTEN Effekt der Suppression, nicht Sampling-Rauschen zwischen
    unabhaengig geseedeten Modi (das war der Methoden-Bug der ersten Version)."""
    adapt_on = (mode != "baseline")
    n_use = max(5, int(round(traffic_scale * len(pairs))))
    use_pairs = pairs[:n_use]
    deliv, air = [], []
    for j, (s, d) in enumerate(use_pairs):
        if s in dead_nodes or d in dead_nodes:
            continue
        # gleicher Stream je (Tick, Flood-Index) ueber alle Modi
        rstate = np.random.default_rng((crn_base + j * 2862933555777941757) % (2 ** 63))
        ok, ntx = run_flood(s, d, mhr_set, rstate, node_prob, known_nbr, adapt_on,
                            dead_nodes=dead_nodes, dead_edges=dead_edges,
                            measure=measure if adapt_on else None)
        deliv.append(1.0 if ok else 0.0)
        if ok:
            air.append(ntx)
    dm = float(np.mean(deliv)) if deliv else 0.0
    am = float(np.mean(air)) if air else 0.0
    return dm, am, len(deliv)


def reachable_degree(u, dead_nodes, dead_edges):
    """Lokal beobachtbarer EFFEKTIVER Grad: Nachbarn, die nicht tot / deren Link nicht weg."""
    c = 0
    for v in NBR[u]:
        if v in dead_nodes:
            continue
        if frozenset((u, v)) in dead_edges:
            continue
        c += 1
    return c


# ======================================================================================
# 7) HAUPT-LAUF: pro Szenario, pro Seed, ueber alle Ticks; 3 Regime parallel
# ======================================================================================
def run_scenario(name, seeds, alpha=1.0):
    """Laeuft das Szenario ueber N_TICKS fuer baseline/static/adaptive.
    Gibt aggregierte Zeitreihen + Trajektorien zurueck."""
    # Pro Regime: Zeitreihen deliv/air ueber Ticks, gemittelt ueber Seeds.
    series = {m: {"deliv": np.zeros((len(seeds), N_TICKS)),
                  "air": np.zeros((len(seeds), N_TICKS))}
              for m in ("baseline", "static", "adaptive")}
    # supp_prob-Trajektorien (adaptiv): mean ueber Knoten + Sample-Knoten, je Seed/Tick
    prob_mean = np.zeros((len(seeds), N_TICKS))
    prob_var = np.zeros((len(seeds), N_TICKS))
    # Wechselrate: |Delta p| je Knoten je Tick, gemittelt
    prob_change = np.zeros((len(seeds), N_TICKS))
    # Sample-Knoten-Trajektorien (fuer Plot) — erste Seed, ein paar Hub-Knoten
    sample_nodes = [u for u in top_traffic_order[:6]]
    sample_traj = {u: np.full(N_TICKS, np.nan) for u in sample_nodes}

    for si, seed in enumerate(seeds):
        mhr_set = select_mhr(alpha, seed) & giant_set
        known_nbr = NBR  # perfektes 2-Hop-Wissen (validiert; G1 deckt Luecken ab)
        # Regler-Zustand pro Knoten
        node_prob = {u: P_INIT for u in mhr_set}
        red_ewma = {u: 1.0 for u in mhr_set}   # startet optimistisch->wird real gemessen
        base_degree = {u: DEG.get(u, 0) for u in mhr_set}
        prev_nbr = {u: set(NBR[u]) for u in mhr_set}

        for tick in range(N_TICKS):
            traffic, dead_nodes, dead_edges = scenario_state(name, tick, N_TICKS, seed)
            pairs = make_pairs(seed, tick, N_PAIRS)
            # Common-Random-Numbers-Basis fuer DIESEN Tick: alle 3 Modi nutzen sie ->
            # exakt gleiche Floods -> gepaarter Vergleich (Safety ohne Sampling-Rauschen).
            crn_base = (seed * 2654435761 + tick * 40503 + si * 7) % (2 ** 63)

            # --- baseline ---
            db, ab, _ = eval_tick("baseline", mhr_set, node_prob, known_nbr, pairs,
                                  crn_base, dead_nodes, dead_edges, traffic, None)
            # --- static (fester p=0.8) ---
            static_prob = {u: P_STATIC for u in mhr_set}
            ds, as_, _ = eval_tick("static", mhr_set, static_prob, known_nbr, pairs,
                                   crn_base, dead_nodes, dead_edges, traffic, None)
            # --- adaptive (aktueller node_prob) + Messung der Regler-Inputs ---
            measure = {u: {"cover_sum": 0, "cover_n": 0, "redundant_n": 0} for u in mhr_set}
            da, aa, _ = eval_tick("adaptive", mhr_set, node_prob, known_nbr, pairs,
                                  crn_base, dead_nodes, dead_edges, traffic, measure)

            series["baseline"]["deliv"][si, tick] = db
            series["baseline"]["air"][si, tick] = ab
            series["static"]["deliv"][si, tick] = ds
            series["static"]["air"][si, tick] = as_
            series["adaptive"]["deliv"][si, tick] = da
            series["adaptive"]["air"][si, tick] = aa

            # --- Regler-Update (langsamer Tick) anhand der LOKALEN Messung ---
            old_probs = dict(node_prob)
            for u in mhr_set:
                m = measure[u]
                if m["cover_n"] > 0:
                    red_ratio = m["redundant_n"] / m["cover_n"]
                else:
                    # nicht gehoert diesen Tick -> keine frische Evidenz; leicht konservativ
                    red_ratio = red_ewma[u]
                red_ewma[u] = EWMA_A * red_ratio + (1 - EWMA_A) * red_ewma[u]
                # Churn-Signal: Nachbar-Fluktuation (erscheinen/verschwinden) lokal beobachtbar
                cur_nbr = set(v for v in NBR[u]
                              if v not in dead_nodes and frozenset((u, v)) not in dead_edges)
                ch = len(cur_nbr ^ prev_nbr[u]) / max(1, len(NBR[u]))
                prev_nbr[u] = cur_nbr
                deg_eff = reachable_degree(u, dead_nodes, dead_edges)
                node_prob[u] = controller_update(node_prob[u], red_ewma[u], ch,
                                                  deg_eff, base_degree[u])

            # Trajektorien-Statistik
            pv = np.array([node_prob[u] for u in mhr_set], dtype=float)
            prob_mean[si, tick] = float(pv.mean()) if pv.size else P_INIT
            prob_var[si, tick] = float(pv.var()) if pv.size else 0.0
            changes = np.array([abs(node_prob[u] - old_probs[u]) for u in mhr_set])
            prob_change[si, tick] = float(changes.mean()) if changes.size else 0.0
            if si == 0:
                for u in sample_nodes:
                    if u in node_prob:
                        sample_traj[u][tick] = node_prob[u]

    return {
        "name": name,
        "series": {m: {"deliv": series[m]["deliv"], "air": series[m]["air"]}
                   for m in series},
        "prob_mean": prob_mean, "prob_var": prob_var, "prob_change": prob_change,
        "sample_traj": {u: sample_traj[u].tolist() for u in sample_traj},
    }


# ======================================================================================
# 8) AUSFUEHRUNG
# ======================================================================================
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
log(f"\n=== Adaptive Sim: {N_SEEDS} Seeds, {N_TICKS} Ticks, {N_PAIRS} Paare/Tick, "
    f"alpha=1.0 (alle Knoten MHR) ===")
log(f"  Regler: p in [{P_MIN},{P_MAX}], init={P_INIT}, statisch={P_STATIC}, "
    f"STEP_UP={STEP_UP}, BETA={BETA}, RED_HI={RED_HI}, RED_LO={RED_LO}, churn>{CHURN_TRIG}")

results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_ticks": N_TICKS, "n_pairs": N_PAIRS,
    "fast": FAST,
    "controller": dict(P_MIN=P_MIN, P_MAX=P_MAX, P_INIT=P_INIT, P_STATIC=P_STATIC,
                       STEP_UP=STEP_UP, BETA=BETA, RED_HI=RED_HI, RED_LO=RED_LO,
                       CHURN_TRIG=CHURN_TRIG, EWMA_A=EWMA_A,
                       k_cover=SAFE_K_COVER, min_degree=SAFE_MIN_DEGREE,
                       snr_floor=SAFE_SNR_FLOOR),
    "topology": {"giant_nodes": GC.number_of_nodes(), "giant_edges": GC.number_of_edges(),
                 "avg_degree": float(statistics.mean(degs))},
    "scenarios": {},
}

scenario_runs = {}
for name in SCENARIOS:
    log(f"\n--- Szenario: {name} ---")
    r = run_scenario(name, seeds, alpha=1.0)
    scenario_runs[name] = r

    # Aggregate ueber alle Ticks/Seeds
    def agg(m, key):
        return r["series"][m][key]
    base_d = agg("baseline", "deliv"); base_a = agg("baseline", "air")
    stat_d = agg("static", "deliv"); stat_a = agg("static", "air")
    adap_d = agg("adaptive", "deliv"); adap_a = agg("adaptive", "air")

    # Safety: pro Tick (gemittelt ueber Seeds) deliv >= baseline-deliv?  (haerter: jeder Tick)
    base_d_tick = base_d.mean(axis=0)
    stat_d_tick = stat_d.mean(axis=0)
    adap_d_tick = adap_d.mean(axis=0)
    # Rauschband: 2*SEM ueber Seeds der Baseline, pro Tick
    base_sem_tick = (base_d.std(axis=0, ddof=1) / math.sqrt(len(seeds))
                     if len(seeds) >= 2 else np.zeros(N_TICKS))
    tol = np.maximum(2 * base_sem_tick, 0.005)
    static_safe_ticks = int(np.sum(stat_d_tick >= base_d_tick - tol))
    adapt_safe_ticks = int(np.sum(adap_d_tick >= base_d_tick - tol))
    # strikt (ohne Toleranz)
    static_safe_strict = int(np.sum(stat_d_tick >= base_d_tick - 1e-9))
    adapt_safe_strict = int(np.sum(adap_d_tick >= base_d_tick - 1e-9))

    # Airtime-Mehrwert: relativ zur Baseline-Airtime, statisch vs adaptiv (Mittel ueber Ticks)
    base_a_m = float(base_a.mean()); stat_a_m = float(stat_a.mean()); adap_a_m = float(adap_a.mean())
    static_gain = 100.0 * (base_a_m - stat_a_m) / base_a_m if base_a_m > 0 else 0.0
    adapt_gain = 100.0 * (base_a_m - adap_a_m) / base_a_m if base_a_m > 0 else 0.0
    extra = adapt_gain - static_gain  # Mehrwert adaptiv ueber statisch (Prozentpunkte)

    # Konvergenz / Oszillation
    pm = r["prob_mean"]            # (seeds, ticks)
    pv = r["prob_var"]
    pc = r["prob_change"]
    # Konvergenz: Varianz der mittleren p in der ZWEITEN Haelfte (eingeschwungen?)
    half = N_TICKS // 2
    settle_var = float(np.mean(np.var(pm[:, half:], axis=1)))      # zeitliche Varianz spaet
    settle_mean = float(np.mean(pm[:, half:]))
    mean_change_late = float(np.mean(pc[:, half:]))                # Wechselrate spaet
    mean_change_all = float(np.mean(pc))
    netz_var = float(np.mean(pv))   # netz-weite p-Streuung (Knoten untereinander)

    results["scenarios"][name] = {
        "baseline_deliv_mean": float(base_d.mean()), "baseline_air_mean": base_a_m,
        "static_deliv_mean": float(stat_d.mean()), "static_air_mean": stat_a_m,
        "adaptive_deliv_mean": float(adap_d.mean()), "adaptive_air_mean": adap_a_m,
        "static_airtime_gain_pct": static_gain,
        "adaptive_airtime_gain_pct": adapt_gain,
        "adaptive_extra_vs_static_pp": extra,
        "static_safe_ticks": static_safe_ticks, "adapt_safe_ticks": adapt_safe_ticks,
        "static_safe_strict_ticks": static_safe_strict,
        "adapt_safe_strict_ticks": adapt_safe_strict,
        "n_ticks": N_TICKS,
        "worst_adaptive_deliv_vs_base": float(np.min(adap_d_tick - base_d_tick)),
        "worst_static_deliv_vs_base": float(np.min(stat_d_tick - base_d_tick)),
        "convergence_settle_var": settle_var,
        "convergence_settle_mean_p": settle_mean,
        "mean_param_change_rate": mean_change_all,
        "mean_param_change_rate_late": mean_change_late,
        "netz_param_variance": netz_var,
        # Zeitreihen (Mittel ueber Seeds) fuer Plots/JSON
        "ts_base_deliv": base_d_tick.tolist(), "ts_static_deliv": stat_d_tick.tolist(),
        "ts_adapt_deliv": adap_d_tick.tolist(),
        "ts_base_air": base_a.mean(axis=0).tolist(),
        "ts_static_air": stat_a.mean(axis=0).tolist(),
        "ts_adapt_air": adap_a.mean(axis=0).tolist(),
        "ts_prob_mean": pm.mean(axis=0).tolist(),
        "ts_prob_change": pc.mean(axis=0).tolist(),
        "ts_prob_netzvar": pv.mean(axis=0).tolist(),
        "sample_traj": r["sample_traj"],
    }
    e = results["scenarios"][name]
    log(f"  baseline deliv={e['baseline_deliv_mean']:.4f} air={base_a_m:.1f}")
    log(f"  static   deliv={e['static_deliv_mean']:.4f} air={stat_a_m:.1f} "
        f"gain={static_gain:+.1f}% safe_ticks={static_safe_ticks}/{N_TICKS} "
        f"(strikt {static_safe_strict}/{N_TICKS})")
    log(f"  adaptive deliv={e['adaptive_deliv_mean']:.4f} air={adap_a_m:.1f} "
        f"gain={adapt_gain:+.1f}% safe_ticks={adapt_safe_ticks}/{N_TICKS} "
        f"(strikt {adapt_safe_strict}/{N_TICKS})")
    log(f"  -> Mehrwert adaptiv vs statisch: {extra:+.2f} Prozentpunkte Airtime")
    log(f"  -> Konvergenz: settle_var(spaet)={settle_var:.5f} settle_mean_p={settle_mean:.3f} "
        f"Wechselrate(spaet)={mean_change_late:.4f} Netz-Var={netz_var:.4f}")
    log(f"  -> worst adaptive deliv vs base (ueber Ticks): "
        f"{e['worst_adaptive_deliv_vs_base']:+.4f}")


# ======================================================================================
# 9) GO / NO-GO
# ======================================================================================
log("\n=== GO/NO-GO (adaptiver Regler vs. statischer sicherer Satz) ===")
all_safe = True
extras = []
osc_flags = []
for name in SCENARIOS:
    e = results["scenarios"][name]
    # Safety: adaptive Lieferquote in JEDEM Tick >= Baseline (im Rauschband)
    safe = (e["adapt_safe_ticks"] == e["n_ticks"])
    all_safe = all_safe and safe
    extras.append(e["adaptive_extra_vs_static_pp"])
    # Oszillation: Wechselrate spaet sollte klein sein (eingeschwungen). Schwelle ~1 STEP_UP.
    oscillates = e["mean_param_change_rate_late"] > (STEP_UP)  # mehr als 1 Step/Tick spaet = unruhig
    osc_flags.append(oscillates)

mean_extra = float(np.mean(extras))
max_abs_extra = float(np.max(np.abs(extras)))
any_osc = any(osc_flags)

# Schwelle Mehrwert: lohnt sich erst, wenn adaptiv SPUERBAR mehr spart als statisch
# (>= ~2 Prozentpunkte Airtime ueber alle Szenarien gemittelt) UND nicht oszilliert
# UND Safety ueberall gehalten.
USEFUL_THRESHOLD_PP = 2.0
useful = mean_extra >= USEFUL_THRESHOLD_PP
go = bool(all_safe and useful and not any_osc)

results["decision"] = {
    "all_scenarios_safe": bool(all_safe),
    "mean_extra_airtime_pp_vs_static": mean_extra,
    "max_abs_extra_pp": max_abs_extra,
    "any_oscillation": bool(any_osc),
    "useful_threshold_pp": USEFUL_THRESHOLD_PP,
    "go": go,
}
log(f"  Safety ueber ALLE Szenarien/Ticks gehalten: {all_safe}")
log(f"  Mehrwert adaptiv vs statisch (Mittel): {mean_extra:+.2f} pp Airtime "
    f"(max |{max_abs_extra:.2f}| pp)")
log(f"  Oszillation in irgendeinem Szenario: {any_osc}")
log(f"  ==> ENTSCHEIDUNG: {'GO' if go else 'NO-GO'} "
    f"(Safety={all_safe}, Mehrwert>={USEFUL_THRESHOLD_PP}pp={useful}, keine Oszillation={not any_osc})")


# ======================================================================================
# 10) JSON SCHREIBEN
# ======================================================================================
json.dump(results, open(os.path.join(HERE, "adaptive_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("\n  adaptive_results.json geschrieben.")


# ======================================================================================
# 11) PLOTS
# ======================================================================================
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})
ticks_x = list(range(N_TICKS))
COL = {"baseline": "#333333", "static": "#2980b9", "adaptive": "#2e7d32"}

# --- fig_adapt_trajectories: supp_prob ueber Zeit (mehrere Sample-Knoten) + Netz-Mittel
fig, axs = plt.subplots(2, 2, figsize=(13, 8))
for ax, name in zip(axs.flat, SCENARIOS):
    e = results["scenarios"][name]
    # Sample-Knoten
    for u, tr in e["sample_traj"].items():
        ax.plot(ticks_x, tr, lw=0.9, alpha=0.55)
    # Netz-Mittel fett
    ax.plot(ticks_x, e["ts_prob_mean"], color="black", lw=2.2, label="Netz-Mittel p")
    ax.axhline(P_STATIC, color="#2980b9", ls=":", lw=1.2, label=f"statisch p={P_STATIC}")
    ax.axhline(P_MAX, color="#888", ls="--", lw=0.7)
    ax.axhline(P_MIN, color="#888", ls="--", lw=0.7)
    ax.set_ylim(P_MIN - 0.05, P_MAX + 0.05)
    ax.set_title(f"{name}  (settle_var={e['convergence_settle_var']:.4f})")
    ax.set_xlabel("Adaptions-Tick"); ax.set_ylabel("supp_prob (G5)")
    ax.grid(alpha=0.25); ax.legend(fontsize=7, loc="lower left")
fig.suptitle("fig_adapt_trajectories — supp_prob je Knoten (duenn) + Netz-Mittel (fett) "
             "ueber Adaptions-Ticks")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_adapt_trajectories.png")); plt.close(fig)

# --- fig_adapt_scenarios: deliv + airtime je Szenario (baseline/static/adaptive)
fig, axs = plt.subplots(2, 4, figsize=(17, 8))
for j, name in enumerate(SCENARIOS):
    e = results["scenarios"][name]
    # oben: deliv
    axd = axs[0, j]
    axd.plot(ticks_x, e["ts_base_deliv"], color=COL["baseline"], lw=1.6, label="Baseline")
    axd.plot(ticks_x, e["ts_static_deliv"], color=COL["static"], lw=1.3, label="statisch")
    axd.plot(ticks_x, e["ts_adapt_deliv"], color=COL["adaptive"], lw=1.3, label="adaptiv")
    axd.set_title(f"{name} — Lieferquote")
    axd.set_xlabel("Tick"); axd.set_ylabel("Lieferquote")
    axd.grid(alpha=0.25); axd.legend(fontsize=7)
    # unten: airtime
    axa = axs[1, j]
    axa.plot(ticks_x, e["ts_base_air"], color=COL["baseline"], lw=1.6, label="Baseline")
    axa.plot(ticks_x, e["ts_static_air"], color=COL["static"], lw=1.3, label="statisch")
    axa.plot(ticks_x, e["ts_adapt_air"], color=COL["adaptive"], lw=1.3, label="adaptiv")
    axa.set_title(f"{name} — Airtime (Δstat={e['static_airtime_gain_pct']:+.1f}% "
                  f"adapt={e['adaptive_airtime_gain_pct']:+.1f}%)")
    axa.set_xlabel("Tick"); axa.set_ylabel("Ø Airtime (#Sender)")
    axa.grid(alpha=0.25); axa.legend(fontsize=7)
fig.suptitle("fig_adapt_scenarios — Lieferquote (oben) & Airtime (unten) je Szenario: "
             "Baseline vs statisch vs adaptiv")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_adapt_scenarios.png")); plt.close(fig)

# --- fig_adapt_stability: Konvergenz/Oszillation (Wechselrate + Netz-Varianz je Szenario)
fig, axs = plt.subplots(1, 2, figsize=(13, 5))
for name in SCENARIOS:
    e = results["scenarios"][name]
    axs[0].plot(ticks_x, e["ts_prob_change"], lw=1.4, label=name)
    axs[1].plot(ticks_x, e["ts_prob_netzvar"], lw=1.4, label=name)
axs[0].axhline(STEP_UP, color="#c0392b", ls=":", lw=1, label=f"1 Step ({STEP_UP})")
axs[0].set_title("Wechselrate |Δp|/Tick (Oszillations-Maß)\nklein & fallend = konvergiert")
axs[0].set_xlabel("Adaptions-Tick"); axs[0].set_ylabel("mittl. |Δp| je Knoten")
axs[0].grid(alpha=0.25); axs[0].legend(fontsize=8)
axs[1].set_title("Netz-weite p-Varianz (Knoten untereinander)\nStreuung der Aggressivitaet")
axs[1].set_xlabel("Adaptions-Tick"); axs[1].set_ylabel("Var(p) ueber Knoten")
axs[1].grid(alpha=0.25); axs[1].legend(fontsize=8)
fig.suptitle("fig_adapt_stability — Konvergenz- & Oszillations-Maße")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "fig_adapt_stability.png")); plt.close(fig)

log("  Plots geschrieben: fig_adapt_trajectories.png, fig_adapt_scenarios.png, "
    "fig_adapt_stability.png")

log("\n=== KENNZAHLEN-DUMP (Bericht) ===")
log(json.dumps({
    "decision": results["decision"],
    "per_scenario": {n: {
        "adapt_safe_ticks": results["scenarios"][n]["adapt_safe_ticks"],
        "static_safe_ticks": results["scenarios"][n]["static_safe_ticks"],
        "static_gain_pct": round(results["scenarios"][n]["static_airtime_gain_pct"], 2),
        "adaptive_gain_pct": round(results["scenarios"][n]["adaptive_airtime_gain_pct"], 2),
        "extra_pp": round(results["scenarios"][n]["adaptive_extra_vs_static_pp"], 2),
        "settle_var": round(results["scenarios"][n]["convergence_settle_var"], 5),
        "change_late": round(results["scenarios"][n]["mean_param_change_rate_late"], 4),
        "worst_adapt_vs_base": round(results["scenarios"][n]["worst_adaptive_deliv_vs_base"], 4),
    } for n in SCENARIOS},
}, ensure_ascii=False, indent=2))
log("\n=== FERTIG ===")
