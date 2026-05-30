#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — MECHANISMUS B: Pfad-Erfolgs-Reinforcement (node-lokal)
=====================================================================

Frage (ehrlich, NO-GO erlaubt):
  Bringt ein node-lokales EWMA-Erfolgsmass je gecachtem Pfad + ein passiv gelernter
  Backup-Pfad (feasible successor) NETTO etwas? D.h.: spart das proaktive Umschalten
  auf den Backup BEVOR ein Pfad ganz ausfaellt mehr Re-Discovery-Airtime als es durch
  Pfad-Flattern / schlechtere Backups / Zustellungen ueber suboptimale Pfade kostet —
  bei Lieferquote >= Baseline?

Modell (was MeshCore HEUTE macht vs. B):
  Baseline (flood-and-cache, "first packet wins"):
    - Ein Knoten als Quelle cached pro Ziel GENAU EINEN Pfad (den ersten erfolgreichen
      Flood-Pfad). Solange er haelt, geht jede Nachricht unicast darueber (Airtime = Hops).
    - Zustellung scheitert, wenn eine Kante des Pfads in diesem Tick transient down ist
      ODER ein Zwischenknoten weg ist (Churn).
    - Erst nach 3 AUFEINANDERFOLGENDEN Fehlern wird der Pfad verworfen -> teure
      Re-Discovery via FLOOD (Airtime = Anzahl sendender Knoten im Flood).
  B (Reinforcement, rein node-lokal):
    - Zusaetzlich ein EWMA-Erfolgsmass s in [0,1] pro Ziel (aus ACK/Zustell-Feedback).
    - Zusaetzlich EIN Backup-Pfad (kantendisjunkt soweit moeglich), passiv gelernt aus
      gehoerten Flood-/Pfad-Ketten (0 Airtime). Modelliert als 2.-bester Pfad im
      *gelernten* (evtl. unvollstaendigen) Graphen.
    - Logik: faellt s unter einen Schwellwert (SWITCH_THR), schalte PROAKTIV auf den
      Backup um (1 Versuch), BEVOR die 3 harten Fehler + Re-Flood noetig werden.
      Bewaehrt sich der Backup, wird er primaer. Re-Flood nur, wenn auch der Backup
      faellt (oder kein Backup bekannt).

Ehrlich gemessen (getrennt):
  NUTZEN  : eingesparte Re-Discovery-Floods (Airtime), Lieferquote, weniger Routen-Ausfaelle.
  VERLUST : Pfad-Flattern (Wechsel/Zeit), schlechtere Backups (Hop/Reliability-Aufschlag),
            Extra-Zustellungen ueber suboptimale Pfade, Speicher/Ziel.
  NETTO   : Airtime gesamt (Unicast + Re-Floods), Lieferquote, Routen-Stabilitaet.

Topologie/Flood/Daten wiederverwendet aus mhr_sim_real_v4 / local_calib_sim (echte Kanten,
echtes avg_snr, advert_count -> Churn-Profil). >=5 Seeds, Seed 42.

Aufruf: python3 reinforce_sim.py   (Env-Debug: RF_FAST=1, RF_SEEDS, RF_DESTS, RF_TICKS)
"""

import json
import math
import os
import heapq
import zlib
import collections
import statistics
import numpy as np


def shash(x):
    """Stabiler, prozess-unabhaengiger Hash (Reproduzierbarkeit ueber Laeufe, Seed 42).
    Pythons eingebauter hash() ist pro Prozess gesalzen -> hier NICHT verwenden."""
    return zlib.crc32(repr(x).encode("utf-8"))
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
FAST = os.environ.get("RF_FAST", "0") == "1"
N_SEEDS = int(os.environ.get("RF_SEEDS", "6" if not FAST else "2"))   # >=5
N_SRC = int(os.environ.get("RF_SRC", "40" if not FAST else "8"))      # Quell-Knoten je Seed
N_DEST = int(os.environ.get("RF_DESTS", "4" if not FAST else "2"))    # Ziele je Quelle
N_TICKS = int(os.environ.get("RF_TICKS", "60" if not FAST else "15")) # Sende-Ticks je (s,d)

# Physik / LoRa (identisch v4 / local_calib -> Vergleichbarkeit)
SNR_THR = -12.0
SNR_SCALE = 4.0
FLOOD_MAX_BASE = 64
FLOOD_MAX = 15
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0

# Reinforcement-Parameter
EWMA_ALPHA = 0.30        # Glaettung des Erfolgsmasses (reaktiv genug, aber gedaempft)
SWITCH_THR = 0.55        # s < THR -> proaktiv auf Backup umschalten
HARD_FAIL_LIMIT = 3      # Baseline: 3 Fehler in Folge -> Re-Flood (heutiges Verhalten)
S_INIT = 1.0             # frisch gelernter Pfad startet optimistisch
# Stoer-Niveaus (transienter Linkausfall je Tick)
LINKFAIL_LEVELS = [0.10, 0.20, 0.30]


def log(msg):
    print(msg, flush=True)


# ======================================================================================
# 1) REALE TOPOLOGIE (wie v4 / local_calib)
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
ETXW = {}
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
    etx = 1.0 / max(pr * pr, 1e-4)
    G.add_edge(u, v, snr=float(s), prel=pr, etx=etx)
    SNR[(u, v)] = SNR[(v, u)] = float(s)
    PREL[(u, v)] = PREL[(v, u)] = pr
    ETXW[(u, v)] = ETXW[(v, u)] = etx

for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

# advert_count -> Churn-Profil (wenig Adverts == instabiler / eher offline)
adv_raw = {}
try:
    nd = json.load(open(NODES_F))["nodes"]
    for x in nd:
        pk = (x.get("public_key") or "").lower()
        if pk:
            adv_raw[pk] = int(x.get("advert_count", 0) or 0)
except Exception as ex:
    log(f"  WARN nodes.json nicht ladbar ({ex})")

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)
giant_set = set(giant_list)
DEG = dict(GC.degree())

# Churn-Wahrscheinlichkeit je Knoten aus advert_count: viele Adverts -> stabil (kleine p_off).
adv_vals = np.array([adv_raw.get(pk, 0) for pk in giant_list], dtype=float)
adv_p90 = float(np.percentile(adv_vals, 90)) if len(adv_vals) else 1.0
adv_p90 = max(adv_p90, 1.0)


def churn_off_prob(pk, churn_scale):
    """Per-Tick-Offline-Wahrscheinlichkeit eines Knotens. Niedriger advert_count ->
    hoeher. churn_scale skaliert das Niveau (0 = aus)."""
    a = adv_raw.get(pk, 0)
    rel_stab = min(1.0, a / adv_p90)        # 0 (instabil) .. 1 (sehr stabil)
    return churn_scale * (1.0 - rel_stab)   # bis churn_scale fuer ganz instabile Knoten


log(f"  Riesenkomponente: {GC.number_of_nodes()} Knoten, {GC.number_of_edges()} Kanten, "
    f"Ø-Grad {statistics.mean(degs):.2f}, ambiguous verworfen {n_ambig}")
log(f"  advert_count P90={adv_p90:.0f} (Churn-Skala) | Knoten mit advert>0: "
    f"{sum(1 for pk in giant_list if adv_raw.get(pk,0)>0)}/{len(giant_list)}")


# ======================================================================================
# 2) FLOOD (Re-Discovery) — identisches Modell wie v4 (Airtime = sendende Knoten)
# ======================================================================================
ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}


def rebroadcast_delay(hops, rstate):
    air = BASE_AIR + PER_HOP_AIR * hops
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


def run_flood(src, dst, rstate, dead_nodes=None, dead_edges=None, flood_max=FLOOD_MAX):
    """Timing-getriebener Flood (first-wins). Rueckgabe: (delivered, used_path, n_tx).
    n_tx = Airtime der Re-Discovery (Anzahl sendender Knoten)."""
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    accepted = {src: (0, None)}
    acc_hops = {src: 0}
    sent = set()
    seq = 0
    pq = []
    heapq.heappush(pq, (0.0, seq, src, 0, None))
    seq += 1
    dst_paths = []

    while pq:
        t_send, _, u, hops_u, prev_u = heapq.heappop(pq)
        if hops_u >= flood_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:
            continue
        sent.add(u)
        out_hops = hops_u + 1
        rnd = rstate.random
        for v, pr in ADJ[u]:
            if has_dead and not link_ok(u, v):
                continue
            if rnd() > pr:
                continue
            if v == dst:
                p = reconstruct_path(accepted, u, src) + [v]
                dst_paths.append((out_hops, p))
            if v not in accepted:
                accepted[v] = (out_hops, u)
                acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, rstate)
                heapq.heappush(pq, (t_send + d, seq, v, out_hops, u))
                seq += 1

    delivered = dst in accepted or len(dst_paths) > 0
    if not delivered:
        return False, None, len(sent)
    used = reconstruct_path(accepted, dst, src)
    return True, used, len(sent)


# ======================================================================================
# 3) PFAD-QUALITAET / UNICAST-ZUSTELLUNG
# ======================================================================================
def path_hops(p):
    return (len(p) - 1) if p else 0


def path_reliability(p):
    if not p or len(p) < 2:
        return 1.0
    r = 1.0
    for a, b in zip(p, p[1:]):
        r *= PREL.get((a, b), 0.0)
    return r


def path_alive(path, dead_nodes, dead_edges):
    """Liefert True, wenn ALLE Knoten/Kanten des Pfads in diesem Tick verfuegbar sind."""
    if not path:
        return False
    for n in path[1:]:                      # Quelle selbst nie 'dead'
        if n in dead_nodes:
            return False
    for a, b in zip(path, path[1:]):
        if frozenset((a, b)) in dead_edges:
            return False
    return True


def unicast_attempt(path, dead_nodes, dead_edges, rstate):
    """Ein Unicast-Versuch entlang des Pfads. Airtime = Hops (Sende-Ereignisse, gezaehlt
    bis zum Abbruch). Erfolg, wenn Pfad lebt UND jeder Hop physikalisch ankommt (PREL)."""
    if not path or len(path) < 2:
        return (path is not None and len(path) == 1), 0
    tx = 0
    for a, b in zip(path, path[1:]):
        tx += 1                              # a sendet
        if b in dead_nodes or frozenset((a, b)) in dead_edges:
            return False, tx
        if rstate.random() > PREL.get((a, b), 0.0):
            return False, tx                 # transienter Linkverlust (SNR)
    return True, tx


# Backup-Pfad: passiv gelernt -> 2.-bester (kantendisjunkter) Pfad im gelernten Graphen.
def compute_paths(src, dst):
    """Primaer = ETX-bester Pfad; Backup = bester Pfad nach Entfernen der Primaer-
    Zwischenkanten (so disjunkt wie moeglich). Beide auf dem VOLLEN Graphen
    (idealisierte passive Lernbarkeit; Qualitaetsabschlag separat modelliert)."""
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


# ======================================================================================
# 4) SZENARIO-LAUF: Baseline vs. B ueber N_TICKS, je (src,dst)
# ======================================================================================
def run_pair(src, dst, mode, rstate, linkfail, churn_scale, edge_index, learn_loss):
    """Simuliert N_TICKS Sendeversuche src->dst.
    mode: 'baseline' | 'B'.
    linkfail: Anteil transient toter Kanten je Tick. churn_scale: Knoten-Offline-Skala.
    edge_index: Liste aller Kanten (frozenset) fuer transiente Auswahl.
    learn_loss: Wahrscheinlichkeit, dass der Backup beim passiven Lernen NICHT verfuegbar
                ist (Realismus: passives Lernen unvollstaendig).
    Rueckgabe: dict mit unicast_air, reflood_air, delivered, switches, refloods,
               suboptimal_deliv, hops_used_sum, deliv_count, backup_used.
    """
    primary, backup_full = compute_paths(src, dst)
    if primary is None:
        return None

    # Backup-Verfuegbarkeit aus passivem Lernen (B): mit Wahrscheinlichkeit learn_loss
    # kennt der Knoten KEINEN Backup -> faellt auf Re-Flood zurueck wie Baseline.
    backup = backup_full
    if mode == "B":
        if backup_full is None or rstate.random() < learn_loss:
            backup = None

    cur_path = list(primary)
    cur_is_backup = False
    s_ewma = S_INIT
    consec_fail = 0

    unicast_air = 0.0
    reflood_air = 0.0
    delivered = 0
    deliv_count = 0
    switches = 0
    refloods = 0
    suboptimal_deliv = 0      # Zustellungen, die ueber laengeren als optimalen Pfad gingen
    hops_used_sum = 0
    backup_used_deliv = 0

    opt_hops = path_hops(primary)
    all_edges = edge_index

    for tick in range(N_TICKS):
        # --- transiente Stoerung diesen Tick aufbauen (reproduzierbar) ---
        dead_edges = set()
        if linkfail > 0 and all_edges:
            k = int(linkfail * len(all_edges))
            if k > 0:
                idx = rstate.choice(len(all_edges), size=k, replace=False)
                dead_edges = set(all_edges[i] for i in idx)
        dead_nodes = set()
        if churn_scale > 0:
            # Nur Knoten auf den relevanten Pfaden pruefen (Performance) + Stichprobe.
            cand = set(cur_path) | (set(backup) if backup else set()) | set(primary)
            for n in cand:
                if n == src:
                    continue
                if rstate.random() < churn_off_prob(n, churn_scale):
                    dead_nodes.add(n)

        if mode == "baseline":
            ok, tx = unicast_attempt(cur_path, dead_nodes, dead_edges, rstate)
            unicast_air += tx
            if ok:
                delivered += 1
                deliv_count += 1
                hops_used_sum += path_hops(cur_path)
                consec_fail = 0
            else:
                consec_fail += 1
                if consec_fail >= HARD_FAIL_LIMIT:
                    # teure Re-Discovery via Flood
                    fok, fpath, ftx = run_flood(src, dst, rstate,
                                                dead_nodes=dead_nodes, dead_edges=dead_edges)
                    reflood_air += ftx
                    refloods += 1
                    consec_fail = 0
                    if fok and fpath:
                        cur_path = fpath
                        delivered += 1
                        deliv_count += 1
                        hops_used_sum += path_hops(fpath)
                        if path_hops(fpath) > opt_hops:
                            suboptimal_deliv += 1
                    # sonst: in diesem Tick verloren (kein lebender Pfad gefunden)
            continue

        # --- mode == "B" -----------------------------------------------------------
        # 1) proaktives Umschalten VOR Ausfall, wenn EWMA-Erfolg unter Schwelle & Backup da.
        if (not cur_is_backup) and backup is not None and s_ewma < SWITCH_THR:
            cur_path = list(backup)
            cur_is_backup = True
            switches += 1
            s_ewma = S_INIT * 0.8     # neuer Pfad startet leicht optimistisch, nicht naiv

        ok, tx = unicast_attempt(cur_path, dead_nodes, dead_edges, rstate)
        unicast_air += tx
        s_ewma = (1 - EWMA_ALPHA) * s_ewma + EWMA_ALPHA * (1.0 if ok else 0.0)

        if ok:
            delivered += 1
            deliv_count += 1
            hops_used_sum += path_hops(cur_path)
            if cur_is_backup:
                backup_used_deliv += 1
            if path_hops(cur_path) > opt_hops:
                suboptimal_deliv += 1
            consec_fail = 0
        else:
            consec_fail += 1
            # 2) sofort den jeweils ANDEREN bekannten Pfad probieren (proaktiv, vor Re-Flood)
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
                    deliv_count += 1
                    hops_used_sum += path_hops(cur_path)
                    if cur_is_backup:
                        backup_used_deliv += 1
                    if path_hops(cur_path) > opt_hops:
                        suboptimal_deliv += 1
                    consec_fail = 0
            # 3) erst wenn auch der Alternativpfad versagt UND die Geduld erschoepft -> Re-Flood
            if not switched_here and consec_fail >= HARD_FAIL_LIMIT:
                fok, fpath, ftx = run_flood(src, dst, rstate,
                                            dead_nodes=dead_nodes, dead_edges=dead_edges)
                reflood_air += ftx
                refloods += 1
                consec_fail = 0
                if fok and fpath:
                    cur_path = fpath
                    cur_is_backup = False
                    s_ewma = S_INIT
                    delivered += 1
                    deliv_count += 1
                    hops_used_sum += path_hops(fpath)
                    if path_hops(fpath) > opt_hops:
                        suboptimal_deliv += 1

    return {
        "unicast_air": unicast_air,
        "reflood_air": reflood_air,
        "total_air": unicast_air + reflood_air,
        "delivered": delivered,
        "deliv_rate": delivered / N_TICKS,
        "switches": switches,
        "refloods": refloods,
        "suboptimal_deliv": suboptimal_deliv,
        "backup_used_deliv": backup_used_deliv,
        "deliv_count": deliv_count,
        "hops_used_sum": hops_used_sum,
        "opt_hops": opt_hops,
        "has_backup": int(backup is not None),
        "backup_extra_hops": (path_hops(backup) - opt_hops) if backup else 0,
        "backup_rel_drop": (path_reliability(primary) - path_reliability(backup)) if backup else 0.0,
    }


# ======================================================================================
# 5) ADOPTIONS-SWEEP-INFRASTRUKTUR
# ======================================================================================
# Adoption von B: Anteil der QUELL-Knoten, die B benutzen. (Reinforcement ist eine reine
# Quell-/Cache-Entscheidung; ein Nicht-B-Knoten verhaelt sich wie Baseline.) Da B node-lokal
# und unabhaengig je Quelle wirkt, ist der relevante Sweep der Anteil adoptierender Quellen.
ALPHAS = [0.0, "1node", 0.01, 0.05, 0.10, 0.25, 0.50, 1.0]

top_adv_order = sorted(giant_list, key=lambda pk: (adv_raw.get(pk, 0), DEG.get(pk, 0)),
                       reverse=True)


def select_adopters(alpha, seed):
    n = len(giant_list)
    if alpha == 0.0:
        return set()
    if alpha == "1node":
        return set(top_adv_order[:1])
    k = max(1, int(round(alpha * n)))
    return set(top_adv_order[:k])


def make_sources(seed):
    rstate = np.random.default_rng(seed * 7919 + 13)
    idx = rstate.choice(len(giant_list), size=min(N_SRC, len(giant_list)), replace=False)
    return [giant_list[i] for i in idx]


def make_dests(src, seed):
    rstate = np.random.default_rng((seed * 104729 + shash(src)) % (2**63))
    pool = [p for p in giant_list if p != src]
    idx = rstate.choice(len(pool), size=min(N_DEST, len(pool)), replace=False)
    return [pool[i] for i in idx]


ALL_EDGES = [frozenset(e) for e in GC.edges()]


def evaluate(alpha, linkfail, churn_scale, learn_loss, seeds):
    """Vergleicht je (src,dst) Baseline gegen das Mode des Knotens (B falls Adopter, sonst
    Baseline) auf IDENTISCHER Stoerung (Common Random Numbers). Aggregiert ueber Seeds."""
    per_seed = []
    for si, seed in enumerate(seeds):
        adopters = select_adopters(alpha, seed)
        sources = make_sources(seed)
        agg = collections.defaultdict(float)
        npair = 0
        # getrennte Akkus fuer Netto-Vergleich (alle Paare, egal ob Adopter):
        base_air = 0.0; mode_air = 0.0
        base_deliv = 0.0; mode_deliv = 0.0
        base_reflood = 0.0; mode_reflood = 0.0
        base_reflood_n = 0; mode_reflood_n = 0
        switches = 0; subopt = 0; backup_deliv = 0; has_backup = 0
        bk_extra_hops = []; bk_rel_drop = []
        n_deliv_ticks_total = 0
        for src in sources:
            for dst in make_dests(src, seed):
                use_B = src in adopters
                # Common Random Numbers: gleiche Stoer-Sequenz fuer beide Modi.
                crn = (seed * 2654435761 + si * 40503 + shash((src, dst))) % (2**63)
                rb = np.random.default_rng(crn)
                base = run_pair(src, dst, "baseline", rb, linkfail, churn_scale,
                                ALL_EDGES, learn_loss)
                rm = np.random.default_rng(crn)   # gleicher Startzustand -> gepaart
                mres = run_pair(src, dst, "B" if use_B else "baseline", rm,
                                linkfail, churn_scale, ALL_EDGES, learn_loss)
                if base is None or mres is None:
                    continue
                npair += 1
                base_air += base["total_air"]; mode_air += mres["total_air"]
                base_deliv += base["deliv_rate"]; mode_deliv += mres["deliv_rate"]
                base_reflood += base["reflood_air"]; mode_reflood += mres["reflood_air"]
                base_reflood_n += base["refloods"]; mode_reflood_n += mres["refloods"]
                switches += mres["switches"]; subopt += mres["suboptimal_deliv"]
                backup_deliv += mres["backup_used_deliv"]
                has_backup += mres["has_backup"]
                n_deliv_ticks_total += N_TICKS
                if use_B and mres["has_backup"]:
                    bk_extra_hops.append(mres["backup_extra_hops"])
                    bk_rel_drop.append(mres["backup_rel_drop"])
        if npair == 0:
            continue
        per_seed.append({
            "base_air": base_air / npair,
            "mode_air": mode_air / npair,
            "base_deliv": base_deliv / npair,
            "mode_deliv": mode_deliv / npair,
            "base_reflood_air": base_reflood / npair,
            "mode_reflood_air": mode_reflood / npair,
            "base_refloods": base_reflood_n / npair,
            "mode_refloods": mode_reflood_n / npair,
            "switches_per_pair": switches / npair,
            "subopt_per_pair": subopt / npair,
            "backup_deliv_per_pair": backup_deliv / npair,
            "switch_rate_per_tick": switches / max(n_deliv_ticks_total, 1),
            "backup_share": has_backup / npair,
            "backup_extra_hops": float(np.mean(bk_extra_hops)) if bk_extra_hops else 0.0,
            "backup_rel_drop": float(np.mean(bk_rel_drop)) if bk_rel_drop else 0.0,
            "npair": npair,
        })

    def agm(k):
        vals = [d[k] for d in per_seed]
        return float(np.mean(vals)) if vals else 0.0

    def asem(k):
        vals = [d[k] for d in per_seed]
        return (float(np.std(vals, ddof=1) / math.sqrt(len(vals))) if len(vals) >= 2 else 0.0)

    out = {"alpha": alpha, "linkfail": linkfail, "churn_scale": churn_scale,
           "learn_loss": learn_loss, "n_seeds_used": len(per_seed)}
    for k in ("base_air", "mode_air", "base_deliv", "mode_deliv", "base_reflood_air",
              "mode_reflood_air", "base_refloods", "mode_refloods", "switches_per_pair",
              "subopt_per_pair", "backup_deliv_per_pair", "switch_rate_per_tick",
              "backup_share", "backup_extra_hops", "backup_rel_drop"):
        out[k] = agm(k)
    out["deliv_sem"] = asem("mode_deliv")
    out["base_deliv_sem"] = asem("base_deliv")
    out["air_net_pct"] = (100 * (out["mode_air"] - out["base_air"]) / out["base_air"]
                          if out["base_air"] > 0 else 0.0)
    out["reflood_air_saved_pct"] = (100 * (out["base_reflood_air"] - out["mode_reflood_air"])
                                    / out["base_reflood_air"] if out["base_reflood_air"] > 0 else 0.0)
    out["deliv_delta"] = out["mode_deliv"] - out["base_deliv"]
    return out


# ======================================================================================
# 6) HAUPT-SWEEPS
# ======================================================================================
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
LEARN_LOSS = 0.30   # 30% der Faelle: passiv kein Backup gelernt (Realismus-Default)

results = {
    "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_src": N_SRC, "n_dest": N_DEST,
    "n_ticks": N_TICKS, "fast": FAST,
    "params": {"ewma_alpha": EWMA_ALPHA, "switch_thr": SWITCH_THR,
               "hard_fail_limit": HARD_FAIL_LIMIT, "learn_loss_default": LEARN_LOSS,
               "linkfail_levels": LINKFAIL_LEVELS},
    "topology": {"giant_nodes": GC.number_of_nodes(), "giant_edges": GC.number_of_edges(),
                 "avg_degree": float(statistics.mean(degs))},
    "sweeps": {},
}

# --- 6a) Adoptions-Sweep je Stoer-Niveau (Linkausfall), volle Adoption + Teilgrade ---
log("\n=== 6a) Adoptions-Sweep ueber Linkausfall-Niveaus ===")
adoption = []
for lf in LINKFAIL_LEVELS:
    for alpha in ALPHAS:
        r = evaluate(alpha, lf, 0.0, LEARN_LOSS, seeds)
        adoption.append(r)
        if alpha in (1.0, "1node"):
            log(f"  lf={lf:.0%} a={str(alpha):6s}: "
                f"deliv {r['base_deliv']:.3f}->{r['mode_deliv']:.3f} ({r['deliv_delta']:+.4f})  "
                f"air {r['base_air']:.2f}->{r['mode_air']:.2f} ({r['air_net_pct']:+.1f}%)  "
                f"reflood-air saved {r['reflood_air_saved_pct']:+.1f}%  "
                f"switch/tick {r['switch_rate_per_tick']:.4f}")
results["sweeps"]["adoption_linkfail"] = adoption

# --- 6b) Knoten-Churn (advert_count) bei voller Adoption ---
log("\n=== 6b) Knoten-Churn (advert_count) bei voller Adoption ===")
churn = []
for cs in [0.05, 0.10, 0.20]:
    rb = evaluate(0.0, 0.10, cs, LEARN_LOSS, seeds)   # alle Baseline (Referenz)
    rB = evaluate(1.0, 0.10, cs, LEARN_LOSS, seeds)   # alle B
    rB["base_air_ref"] = rb["base_air"]; rB["base_deliv_ref"] = rb["base_deliv"]
    churn.append(rB)
    log(f"  churn={cs:.0%}: deliv {rB['base_deliv']:.3f}->{rB['mode_deliv']:.3f} "
        f"({rB['deliv_delta']:+.4f})  air net {rB['air_net_pct']:+.1f}%  "
        f"refloods/pair {rB['base_refloods']:.2f}->{rB['mode_refloods']:.2f}")
results["sweeps"]["churn"] = churn

# --- 6c) Sensitivitaet Backup-Lernqualitaet (learn_loss) bei lf=20%, voller Adoption ---
log("\n=== 6c) Sensitivitaet: Backup-Lernqualitaet (learn_loss) ===")
learnsweep = []
for ll in [0.0, 0.30, 0.60, 1.0]:
    r = evaluate(1.0, 0.20, 0.0, ll, seeds)
    learnsweep.append(r)
    log(f"  learn_loss={ll:.0%}: backup_share {r['backup_share']:.2f}  "
        f"deliv {r['mode_deliv']:.3f} ({r['deliv_delta']:+.4f})  air net {r['air_net_pct']:+.1f}%  "
        f"reflood saved {r['reflood_air_saved_pct']:+.1f}%")
results["sweeps"]["learn_loss"] = learnsweep

# --- 6d) Flatter-Test: kein realer Linkausfall (nur PREL-Rauschen) -> schaltet B grundlos? ---
log("\n=== 6d) Flatter-Test (kein Linkausfall, nur PREL-Rauschen) ===")
flap = evaluate(1.0, 0.0, 0.0, LEARN_LOSS, seeds)
results["sweeps"]["flap_test"] = flap
log(f"  ohne Stoerung: switch/tick {flap['switch_rate_per_tick']:.4f}  "
    f"switches/pair {flap['switches_per_pair']:.2f}  "
    f"deliv {flap['base_deliv']:.3f}->{flap['mode_deliv']:.3f}  air net {flap['air_net_pct']:+.1f}%")

# ======================================================================================
# 7) ON-NODE-SPEICHER pro Ziel
# ======================================================================================
# Pro Ziel-Eintrag im Reinforcement-Cache:
#   - dest hash            : 2 Bytes (Netz nutzt real 2-Byte-Hashes, s. CLAUDE.md)
#   - primary path         : MeshCore cached den Pfad ohnehin (kein Mehraufwand fuer B).
#   - EWMA success s        : 1 Byte (uint8, s*255)
#   - consec_fail counter   : 1 Byte
#   - backup path           : bis flood.max(15) Hops * 1 Byte hash-Glied = bis 15 B;
#                             realistisch passiv gelernt 4-6 Hops -> ~5 B; +1 B Laenge
# B-Mehraufwand/Ziel (ueber Baseline-Cache hinaus): s(1) + counter(1) + backup(~6) = ~8 B.
ONNODE_BYTES_PER_DEST_MIN = 1 + 1 + (1 + 4)      # s + counter + (len + 4-hop backup) = 7 B
ONNODE_BYTES_PER_DEST_TYP = 1 + 1 + (1 + 6)      # ~9 B typisch
ONNODE_BYTES_PER_DEST_MAX = 1 + 1 + (1 + FLOOD_MAX)  # 18 B worst-case (15-Hop backup)
results["on_node"] = {
    "extra_bytes_per_dest_min": ONNODE_BYTES_PER_DEST_MIN,
    "extra_bytes_per_dest_typical": ONNODE_BYTES_PER_DEST_TYP,
    "extra_bytes_per_dest_max": ONNODE_BYTES_PER_DEST_MAX,
    "note": ("Primaerpfad cached MeshCore bereits (kein Mehraufwand). B-Mehraufwand je "
             "Ziel = EWMA-Erfolg(1B) + Fail-Counter(1B) + Backup-Pfad(Laenge+Hashes, "
             "~5-7B typ). Fixe Tabelle, keine dyn. Allokation. Bei 64 Zielen ~0.6 KB."),
    "table_64_dests_kb": round(64 * ONNODE_BYTES_PER_DEST_TYP / 1024.0, 2),
}

# ======================================================================================
# 8) GO / NO-GO
# ======================================================================================
log("\n=== 8) GO/NO-GO-Bewertung ===")
# Nutzen: bei den Stoer-Niveaus mit voller Adoption Netto-Airtime-Ersparnis (negativ = gut)
# und Lieferquoten-Gewinn; Safety: deliv >= baseline (Toleranz 2*SEM bzw. 0.005).
full_pts = [r for r in adoption if r["alpha"] == 1.0]
net_air = [r["air_net_pct"] for r in full_pts]
deliv_gain = [r["deliv_delta"] for r in full_pts]
reflood_saved = [r["reflood_air_saved_pct"] for r in full_pts]
mean_net_air = float(np.mean(net_air)) if net_air else 0.0
mean_deliv_gain = float(np.mean(deliv_gain)) if deliv_gain else 0.0
mean_reflood_saved = float(np.mean(reflood_saved)) if reflood_saved else 0.0

# Safety: ueber ALLE Sweep-Punkte (Adoption + Churn + learn_loss + Flatter) deliv >= base - tol
safety_ok = True
worst_deliv = 99.0
worst_where = None
for grp, rows in (("adoption", adoption), ("churn", churn), ("learn_loss", learnsweep),
                  ("flap", [flap])):
    for r in rows:
        tol = max(2 * r.get("base_deliv_sem", 0.0), 0.005)
        d = r["deliv_delta"]
        if d < worst_deliv:
            worst_deliv = d
            worst_where = f"{grp} a={r.get('alpha')} lf={r.get('linkfail')} cs={r.get('churn_scale')} ll={r.get('learn_loss')}"
        if d < -tol:
            safety_ok = False

# FLATTERN ehrlich pruefen: nicht "schaltet B ueberhaupt" (jedes Umschalten bei realem
# Link-Loss ist legitim), sondern "schadet das Umschalten im stoerungsfreien Fall?".
# Schaedliches Flattern = im Flatter-Test (kein Link/Knoten-Ausfall) sinkt die Lieferquote
# ODER steigt die Netto-Airtime. Beides waere ein Zeichen von grundlosem Pfad-Hin-und-Her.
flap_deliv_delta = flap["deliv_delta"]          # >=0 -> kein Liefer-Schaden
flap_air_net = flap["air_net_pct"]              # <=0 -> kein Airtime-Schaden
flap_harms = (flap_deliv_delta < -max(2 * flap.get("base_deliv_sem", 0.0), 0.005)) or \
             (flap_air_net > 0.5)
no_harmful_flapping = not flap_harms
flap_switch_rate = flap["switch_rate_per_tick"]

USEFUL_AIR_PP = 2.0     # mind. 2% Netto-Airtime-Ersparnis ODER spuerbarer Liefergewinn
useful = (mean_net_air <= -USEFUL_AIR_PP) or (mean_deliv_gain >= 0.01)
go = bool(safety_ok and no_harmful_flapping and useful)

results["decision"] = {
    "mean_net_air_pct_full_adoption": mean_net_air,
    "mean_deliv_gain_full_adoption": mean_deliv_gain,
    "mean_reflood_air_saved_pct": mean_reflood_saved,
    "safety_ok": safety_ok,
    "worst_deliv_delta": worst_deliv,
    "worst_deliv_where": worst_where,
    "no_harmful_flapping": no_harmful_flapping,
    "flap_switch_per_tick": flap_switch_rate,
    "flap_deliv_delta_no_disturbance": flap_deliv_delta,
    "flap_air_net_pct_no_disturbance": flap_air_net,
    "useful": useful,
    "useful_threshold_air_pp": USEFUL_AIR_PP,
    "GO": go,
}
log(f"  Netto-Airtime (voll, Ø ueber lf): {mean_net_air:+.2f}%  "
    f"(Re-Discovery-Airtime gespart Ø {mean_reflood_saved:+.1f}%)")
log(f"  Liefergewinn (voll, Ø): {mean_deliv_gain:+.4f}")
log(f"  Safety (deliv>=base ueberall): {safety_ok} (worst Δ={worst_deliv:+.4f} @ {worst_where})")
log(f"  Kein SCHAEDLICHES Flattern: {no_harmful_flapping} "
    f"(stoerungsfrei: switch/tick={flap_switch_rate:.4f}, deliv Δ={flap_deliv_delta:+.4f}, "
    f"air net={flap_air_net:+.1f}%)")
log(f"  ==> ENTSCHEIDUNG Reinforcement (B): {'GO' if go else 'NO-GO'}")

# ======================================================================================
# 9) JSON SCHREIBEN (frueh/inkrementell)
# ======================================================================================
json.dump(results, open(os.path.join(HERE, "reinforce_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("\n  reinforce_results.json geschrieben.")

# ======================================================================================
# 10) PLOTS
# ======================================================================================
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})

# fig_rf_airtime: Re-Discovery- & Netto-Airtime ueber Stoerung (voll adoptiert)
fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
lfs = LINKFAIL_LEVELS
base_rf = [next(r for r in full_pts if r["linkfail"] == lf)["base_reflood_air"] for lf in lfs]
mode_rf = [next(r for r in full_pts if r["linkfail"] == lf)["mode_reflood_air"] for lf in lfs]
x = np.arange(len(lfs)); w = 0.35
axs[0].bar(x - w/2, base_rf, w, label="Baseline", color="#c0392b")
axs[0].bar(x + w/2, mode_rf, w, label="B (Reinforce)", color="#2e7d32")
axs[0].set_xticks(x); axs[0].set_xticklabels([f"{int(lf*100)}%" for lf in lfs])
axs[0].set_xlabel("transienter Linkausfall"); axs[0].set_ylabel("Re-Discovery-Airtime / Paar")
axs[0].set_title("Re-Discovery-Airtime (Floods)"); axs[0].grid(axis="y", alpha=0.25); axs[0].legend()
net = [next(r for r in full_pts if r["linkfail"] == lf)["air_net_pct"] for lf in lfs]
rfs = [next(r for r in full_pts if r["linkfail"] == lf)["reflood_air_saved_pct"] for lf in lfs]
axs[1].plot([f"{int(lf*100)}%" for lf in lfs], net, "o-", color="#2e7d32",
            label="Netto-Airtime Δ% (B vs Base)")
axs[1].plot([f"{int(lf*100)}%" for lf in lfs], rfs, "s--", color="#2980b9",
            label="Re-Discovery-Airtime gespart %")
axs[1].axhline(0, color="#888", lw=0.8)
axs[1].set_xlabel("transienter Linkausfall"); axs[1].set_ylabel("%")
axs[1].set_title("Netto-Airtime & gesparte Re-Discovery"); axs[1].grid(alpha=0.25); axs[1].legend(fontsize=8)
fig.suptitle("fig_rf_airtime — Reinforcement: Re-Discovery vs. Netto-Airtime ueber Stoerung")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_rf_airtime.png")); plt.close(fig)

# fig_rf_delivery_stability: Lieferquote + Routen-Stabilitaet (switch/tick) ueber Stoerung
fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
bd = [next(r for r in full_pts if r["linkfail"] == lf)["base_deliv"] for lf in lfs]
md = [next(r for r in full_pts if r["linkfail"] == lf)["mode_deliv"] for lf in lfs]
axs[0].plot([f"{int(lf*100)}%" for lf in lfs], bd, "o-", color="#c0392b", label="Baseline")
axs[0].plot([f"{int(lf*100)}%" for lf in lfs], md, "s-", color="#2e7d32", label="B (Reinforce)")
axs[0].set_xlabel("transienter Linkausfall"); axs[0].set_ylabel("Lieferquote")
axs[0].set_title("Lieferquote (Safety: B >= Baseline)"); axs[0].grid(alpha=0.25); axs[0].legend()
spt = [next(r for r in full_pts if r["linkfail"] == lf)["switch_rate_per_tick"] for lf in lfs]
axs[1].bar([f"{int(lf*100)}%" for lf in lfs], spt, color="#8e44ad")
axs[1].axhline(flap["switch_rate_per_tick"], color="#888", ls="--",
               label=f"stoerungsfrei {flap['switch_rate_per_tick']:.4f}")
axs[1].set_xlabel("transienter Linkausfall"); axs[1].set_ylabel("Pfad-Wechsel je Tick")
axs[1].set_title("Routen-Stabilitaet (Flattern?)"); axs[1].grid(axis="y", alpha=0.25); axs[1].legend(fontsize=8)
fig.suptitle("fig_rf_delivery_stability — Lieferquote & Routen-Stabilitaet")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_rf_delivery_stability.png")); plt.close(fig)

# fig_rf_adoption: Netto-Airtime & Liefergewinn ueber Adoption (lf=20%)
fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
ad20 = [r for r in adoption if r["linkfail"] == 0.20]
order = [0.0, "1node", 0.01, 0.05, 0.10, 0.25, 0.50, 1.0]
def keyf(a): return next((r for r in ad20 if str(r["alpha"]) == str(a)), None)
xl = [str(a) for a in order]
netA = [keyf(a)["air_net_pct"] if keyf(a) else 0 for a in order]
delA = [keyf(a)["deliv_delta"] if keyf(a) else 0 for a in order]
axs[0].plot(xl, netA, "o-", color="#2e7d32"); axs[0].axhline(0, color="#888", lw=0.8)
axs[0].set_xlabel("Adoptionsanteil α"); axs[0].set_ylabel("Netto-Airtime Δ% vs Baseline")
axs[0].set_title("Netto-Airtime ueber Adoption (lf=20%)"); axs[0].grid(alpha=0.25)
axs[0].tick_params(axis="x", rotation=45)
axs[1].plot(xl, delA, "s-", color="#2980b9"); axs[1].axhline(0, color="#888", lw=0.8)
axs[1].set_xlabel("Adoptionsanteil α"); axs[1].set_ylabel("Δ Lieferquote vs Baseline")
axs[1].set_title("Liefergewinn ueber Adoption (lf=20%)"); axs[1].grid(alpha=0.25)
axs[1].tick_params(axis="x", rotation=45)
fig.suptitle("fig_rf_adoption — Reinforcement ueber Adoptionsgrad")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_rf_adoption.png")); plt.close(fig)

log("  Plots: fig_rf_airtime.png, fig_rf_delivery_stability.png, fig_rf_adoption.png")

log("\n=== KENNZAHLEN-DUMP ===")
log(json.dumps({
    "decision": results["decision"],
    "on_node": results["on_node"],
    "full_adoption_by_linkfail": [
        {"lf": r["linkfail"], "deliv_delta": round(r["deliv_delta"], 4),
         "air_net_pct": round(r["air_net_pct"], 2),
         "reflood_saved_pct": round(r["reflood_air_saved_pct"], 1),
         "switch_per_tick": round(r["switch_rate_per_tick"], 4),
         "subopt_per_pair": round(r["subopt_per_pair"], 3)}
        for r in full_pts],
}, ensure_ascii=False, indent=2))
log("\n=== FERTIG ===")
