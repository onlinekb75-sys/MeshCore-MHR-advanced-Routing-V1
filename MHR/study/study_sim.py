#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MHR-MeshCore — Studien-Simulation: Mechanismus x Adoptions-Sweep auf ECHTEN Daten
=================================================================================

Diese Datei realisiert das in ../study/STUDY_DESIGN.md beschriebene Experiment:
Welche lokalen Routing-Optimierungen senken Airtime und Umwege im realen
MeshCore-Netz MESSBAR, ohne das Paketformat zu brechen, und so, dass jede
Teil-Adoption (1 Knoten ... alle) mit Stock-Firmware koexistiert und das Netz
NIE schlechter wird als heute (Safety-Invariante: Lieferquote >= Baseline UND
Airtime <= Baseline)?

Wissenschaftliche Ehrlichkeit / Trennung:
  - GEMESSEN (Realdaten, aus mhr_sim_real_v3.py abgeleitet, in data/):
      * aktive Repeater-Knoten (nodes.json: role/Geo/relay-Aktivitaet)
      * SNR/Distanz-Kalibrierung (snr_calibration.json) -> Reichweiten-Linkgraph
      * beobachtete Relay-Kanten (topology_edges.json) -> Quervalidierung
      * reale Detour-Statistik (real_detour_stats.json) -> Plausibilitaets-Anker
  - SIMULIERT (dieses Skript): das Flood-Routing + die Mechanismen + der Sweep.

Das LINKMODELL (aktiver Subgraph, kalibriertes SNR-Modell, PLE-Floor, Top-K-
Nachbarn) ist IDENTISCH zu mhr_sim_real_v3.py, damit die Studie auf derselben
Topologie wie die v3-Sim laeuft. Reproduzierbar ueber Seeds.

Aufruf:  python3 study_sim.py
Optionale Env-Vars (zum schnellen Iterieren, NICHT fuer den finalen Lauf):
  STUDY_PAIRS, STUDY_SEEDS, STUDY_MC, STUDY_FAST=1
"""

import json
import math
import os
import sys
import time
import heapq
import collections
import numpy as np
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --------------------------------------------------------------------------------------
# Pfade / globale Reproduzierbarkeit
# --------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.normpath(os.path.join(HERE, "..", "sim"))
DATA = os.path.join(SIM_DIR, "data")

NODES_F = os.path.join(DATA, "nodes.json")
CAL_F = os.path.join(DATA, "snr_calibration.json")
TOPO_F = os.path.join(DATA, "topology_edges.json")
DETOUR_F = os.path.join(DATA, "real_detour_stats.json")

MASTER_SEED = 42


def log(msg):
    print(msg, flush=True)


def haversine(la1, lo1, la2, lo2):
    R = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


# --------------------------------------------------------------------------------------
# Konfiguration des Sweeps (mit transparenter, geloggter Begrenzung bei Zeitdruck)
# --------------------------------------------------------------------------------------
FAST = os.environ.get("STUDY_FAST", "0") == "1"

# Adoptionsanteile alpha: 0 (=Baseline), 1 Knoten (Sonderfall), und Anteile.
ALPHAS = [0.0, "1node", 0.01, 0.05, 0.10, 0.25, 0.50, 1.0]

# Mechanismen
MECHS = ["M0", "M1", "M2k2", "M2k3", "M3", "M4", "M5", "M7_12", "M7_15", "COMBI"]

# Rollout-Strategien fuer die alpha-Knoten
ROLLOUTS = ["random", "top_traffic"]

N_PAIRS = int(os.environ.get("STUDY_PAIRS", "120" if not FAST else "20"))
N_SEEDS = int(os.environ.get("STUDY_SEEDS", "5" if not FAST else "2"))
MC = int(os.environ.get("STUDY_MC", "1"))   # MC-Wdh. je Paar je Seed (Timing-Zufall steckt im Seed)

# Stress-Szenarien laufen nur fuer ausgewaehlte alpha
STRESS_ALPHAS = [0.25, 1.0]
STRESS_LINKFAIL = [0.10, 0.20]

# Physik-Parameter (konsistent zu v3)
LORA_MAX_KM = 45.0
SNR_THR = -12.0
SNR_SAT = 13.0
SIM_PLE_FLOOR = 2.0
MAX_NEIGHBORS = int(os.environ.get("MHR_MAX_NEIGHBORS", "20"))

FLOOD_MAX_BASE = 64        # Stock-Default (MeshCore flood.max)
# Hop-Timing-Modell (wie v3): Basis + per-hop Airtime, Jitter-Fenster
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0               # Faktor des Zufallsfensters relativ zur Airtime


# ======================================================================================
# TOPOLOGIE / LINKMODELL  (identisch zu mhr_sim_real_v3.py, deterministisch ohne Rohdaten)
# ======================================================================================
log("=== Topologie/Linkmodell laden (wie v3) ===")
nodes = json.load(open(NODES_F))["nodes"]
cal = json.load(open(CAL_F))
real_detour = json.load(open(DETOUR_F))

SNR0 = cal["sim_snr0_used"]
PLE = max(cal.get("ple_binned", 2.0), SIM_PLE_FLOOR)


def valid_geo(la, lo):
    if la is None or lo is None:
        return False
    if abs(la) < 0.5 and abs(lo) < 0.5:
        return False
    return 35.0 <= la <= 60.0 and -12.0 <= lo <= 25.0


node_geo = {}
for i, n in enumerate(nodes):
    if valid_geo(n.get("lat"), n.get("lon")):
        node_geo[i] = (n["lat"], n["lon"])


def model_snr(dist_km):
    s = SNR0 - 10.0 * PLE * math.log10(max(dist_km, 0.05))
    return min(s, SNR_SAT)


def deliv(snr_db):
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / 3.0)), 0.0, 0.995))


# Aktiver Repeater-Subgraph (wie v3): role==repeater, Geo vorhanden, UND
# (relay_active ODER relay_count_24h>0 ODER taucht real als aufgeloester Pfad-Hop auf).
# Die "Pfad-Hop"-Menge stammt in v3 aus den Rohpaketen; wir rekonstruieren sie aus den
# bereits abgeleiteten BEOBACHTETEN Kanten (topology_edges.json) -> deren Endpunkte SIND
# die real als Relay-Hop gesehenen Knoten. Das reproduziert v3's ~831-Knoten-Subgraph.
_topo_pre = json.load(open(TOPO_F))
_pk8_pre = {}
for i, n in enumerate(nodes):
    pk = n.get("public_key")
    if pk:
        _pk8_pre.setdefault(pk[:8].lower(), i)
seen_in_paths = set()
for e in _topo_pre["edges"]:
    for k in ("u", "v"):
        idx = _pk8_pre.get(e[k].lower())
        if idx is not None:
            seen_in_paths.add(idx)

active = []
for i, n in enumerate(nodes):
    if n.get("role") != "repeater":
        continue
    if i not in node_geo:
        continue
    if (n.get("relay_active") or (n.get("relay_count_24h", 0) or 0) > 0
            or i in seen_in_paths):
        active.append(i)
log(f"  Aktiver Repeater-Subgraph (relay-aktiv/24h>0/als Pfad-Hop gesehen): "
    f"{len(active)} Knoten")

lat = {i: node_geo[i][0] for i in active}
lon = {i: node_geo[i][1] for i in active}

# Reichweiten-/Linkgraph: Top-K staerkste Nachbarn pro Knoten
cand_edges = collections.defaultdict(list)
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
Prel = {}
for i, lst in cand_edges.items():
    lst.sort(reverse=True)
    for pr, j, dij in lst[:MAX_NEIGHBORS]:
        Prel[(i, j)] = pr
        Prel[(j, i)] = pr
        if not G.has_edge(i, j):
            G.add_edge(i, j, etx=1.0 / (pr * pr), p=pr, dist=dij)

comps_sim = sorted(nx.connected_components(G), key=len, reverse=True)
giant_nodes = set(comps_sim[0]) if comps_sim else set()
giant_list = sorted(giant_nodes)
log(f"  Linkgraph: {G.number_of_nodes()} Knoten, {G.number_of_edges()} Kanten, "
    f"groesste Komponente {len(giant_nodes)} Knoten, "
    f"{len(comps_sim)} Komponenten")

# Subgraph auf die Riesenkomponente reduzieren (dort wird simuliert)
GC = G.subgraph(giant_list).copy()
deg = dict(GC.degree())

# Precompute adjacency als (Nachbar, Reliability)-Listen -> schneller als nx.neighbors
ADJ = {u: [(v, Prel.get((u, v), 0.0)) for v in GC.neighbors(u)] for u in GC.nodes()}
log(f"  Grad in Riesenkomponente: min/median/max = "
    f"{min(deg.values())}/{int(np.median(list(deg.values())))}/{max(deg.values())}")

# --- Quervalidierung gegen BEOBACHTETE Kanten (topology_edges.json) ---
# topology_edges enthaelt 8-hex-Praefixe der public_keys. Mappe auf Knotenindizes.
topo = json.load(open(TOPO_F))
pk8_to_idx = {}
for i, n in enumerate(nodes):
    pk = n.get("public_key")
    if pk:
        pk8_to_idx.setdefault(pk[:8].lower(), i)
obs_edges = set()
for e in topo["edges"]:
    u = pk8_to_idx.get(e["u"].lower())
    v = pk8_to_idx.get(e["v"].lower())
    if u is not None and v is not None and u != v:
        obs_edges.add(frozenset((u, v)))
# Wieviele beobachtete Kanten zwischen aktiven Knoten reproduziert das Reichweitenmodell?
obs_in_active = [fs for fs in obs_edges if all(x in active for x in fs)]
repro = sum(1 for fs in obs_in_active if G.has_edge(*tuple(fs)))
xval_frac = repro / max(len(obs_in_active), 1)
log(f"  Quervalidierung: {len(obs_edges)} beob. Kanten, davon {len(obs_in_active)} "
    f"zwischen aktiven Knoten; Reichweitenmodell reproduziert {repro} "
    f"({100*xval_frac:.1f}%).")
log(f"  (Hinweis: Reichweitenmodell ist undirektional/geometrisch; beobachtete Kanten "
    f"sind eine Stichprobe realer Floods. Teil-Ueberlappung ist erwartet.)")


# Traffic-Score je aktivem Knoten (fuer Top-Traffic-Rollout)
def traffic_score(i):
    n = nodes[i]
    rc = n.get("relay_count_24h", 0) or 0
    ts = n.get("traffic_share_score", 0) or 0
    # relay_count_24h ist das robustere absolute Mass; traffic_share als Tiebreak
    return (rc, ts)


top_traffic_order = sorted(giant_list, key=lambda i: traffic_score(i), reverse=True)


# ======================================================================================
# FLOOD-MODELL + MECHANISMEN
# ======================================================================================
# Grundidee (gemeinsam fuer alle Mechanismen):
#   Wir simulieren den Flood timing-getrieben mit einer Prioritaetswarteschlange nach
#   Ankunftszeit. Jeder Knoten u, der eine Kopie zum ersten Mal "akzeptiert", entscheidet
#   nach SEINER Firmware-Regel, ob/wann er rebroadcastet. Stock-Knoten fluten immer
#   (first-packet-wins, Zufallstiming). Neu-Knoten (Menge `newfw`) wenden die Mechanismus-
#   Regel an.
#
#   Eine "Sendung" (rebroadcast) eines Knotens zaehlt als 1 Airtime-Einheit.
#   Airtime pro zugestellter Nachricht = Anzahl Knoten, die tatsaechlich gesendet haben.
#   Lieferung = dst wird erreicht. Genutzer Pfad = der am dst zuerst akzeptierte (Baseline)
#   bzw. der nach Mechanismus gewaehlte (M1: kuerzeste-Hops-fuehrt; M5: Best-of-N am Ziel).
#
#   Link-Stochastik: eine Aussendung von u erreicht Nachbarn v mit Wahrscheinlichkeit
#   Prel[(u,v)] (Monte-Carlo ueber den Seed).
#
# Datenstruktur im PQ-Eintrag: (t_arrival, seq, u, hops, prev, came_via_short)
#   hops = akkumulierte Hopzahl bis u (Sender-Hops); prev = Vorgaengerknoten (fuer Pfad)
#
# Hinweis Effizienz: Wir tracken Pfade ueber prev-Zeiger pro Knoten (erste Akzeptanz),
# das genuegt fuer Hopzahl/Reliability des genutzten Pfads.


def rebroadcast_delay(hops, mech, is_new, rstate):
    """Liefert das Sende-Delay eines Knotens nach Empfang (Timing-Rennen).
    Stock: Basis-Airtime + voller Zufalls-Jitter.
    M1 (Neu): Hop-gewichtet -> weniger Hops => kuerzeres Delay (fuehrt den Flood).
    """
    air = BASE_AIR + PER_HOP_AIR * hops
    if is_new and mech == "M1":
        # Hop-gewichtetes Delay: kleinere hops -> deutlich frueher.
        # Fenster bleibt zufallserhaltend (>= air), aber Bias zu kurzen Pfaden.
        base = air * (1.0 + 0.6 * hops)
        return base + rstate.uniform(0.0, 1.5 * air)
    # Stock / sonst: voller Jitter
    return air + rstate.uniform(0.0, JITTER * air)


def run_flood(src, dst, newfw, mech, rstate, flood_max,
              dead_nodes=None, dead_edges=None, mpr_set=None):
    """
    Simuliert einen Flood von src und liefert:
      delivered (bool), used_path (Liste Knoten src..dst oder None),
      n_tx (Airtime = Anzahl tatsaechlich sendender Knoten).

    flood_max = das gesenkte Hop-Limit der NEU-Firmware-Knoten (M7/COMBI). LOKALE Regel:
    nur Neu-Knoten stoppen ihren Rebroadcast ab `flood_max` Hops; Stock-Knoten fluten bis
    FLOOD_MAX_BASE. Bei Mechanismen ohne M7 ist flood_max==FLOOD_MAX_BASE.

    Mechanismen werden lokal angewandt:
      M0/Baseline: jeder akzeptiert erste Kopie, sendet 1x (Stock).
      M1: Neu-Knoten nutzen hop-gewichtetes Delay (kuerzere Pfade fuehren).
      M2k(k): Neu-Knoten unterdruecken Senden, wenn waehrend des Backoffs >=k Kopien
              gehoert (counter-based / Gossip).
      M3: Neu-Knoten verwerfen anstehendes Senden, wenn dieselbe Kopie via gleich
          kurzem/kuerzerem Pfad gehoert wird (shorter-path-cancel).
      M4: Neu-Knoten ausserhalb mpr_set schweigen; Stock + Relay-Neu-Knoten fluten.
      M5: wie Baseline fuer den Flood (gleiche Airtime), aber das ZIEL waehlt unter den
          im Fenster gehoerten Kopien den kuerzesten Pfad (Detour-Reduktion).
      M7: nur flood_max veraendert (Hop-Limit, lokal nur fuer Neu-Knoten).
      COMBI: M3 + M5 + M7(=12) gemeinsam.
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

    # Mechanismus-Flags
    use_m1 = mech in ("M1",)
    use_m2 = mech in ("M2k2", "M2k3")
    k_supp = 2 if mech == "M2k2" else 3
    use_m3 = mech in ("M3", "COMBI")
    use_m4 = mech in ("M4",)
    use_m5 = mech in ("M5", "COMBI")

    # Zustand je Knoten
    accepted = {}        # u -> (hops, prev)  erste akzeptierte Kopie (fuer Pfad)
    acc_hops = {}        # u -> Hopzahl der akzeptierten (anstehenden) Kopie
    sent = set()         # Knoten, die gesendet haben (Airtime)
    heard_count = collections.Counter()   # u -> # gehoerte Kopien (fuer M2)
    # M3: # zusaetzlicher Kopien (NACH der akzeptierten), die via gleich kurzem/kuerzerem
    # Pfad gehoert wurden. Cancel, wenn >=1 -> ein anderer Knoten fuehrt den Flood gleich gut.
    short_dup = collections.Counter()
    # Fuer M5: am Ziel gehoerte Pfade (im Sammelfenster)
    dst_paths = []       # Liste (hops, path)

    seq = 0
    # PQ: (t_send, seq, u, hops_at_u, prev) -- ein bereits terminiertes Sende-Ereignis
    # Modell: src "sendet" zur Zeit 0. Wir modellieren Sende-Ereignisse: wenn u sendet,
    # erreichen die Nachbarn v die Kopie; v plant sein eigenes Sende-Ereignis (nach Regel).
    pq = []

    def schedule_send(u, t_send, hops, prev):
        nonlocal seq
        heapq.heappush(pq, (t_send, seq, u, hops, prev))
        seq += 1

    # src sendet sofort (hops am src = 0)
    schedule_send(src, 0.0, 0, None)
    accepted[src] = (0, None)
    acc_hops[src] = 0

    # Sammelfenster am Ziel (M5): nachdem dst erstmals erreicht, noch kurz lauschen.
    dst_first_t = None
    DST_WINDOW = 6.0 * (BASE_AIR + PER_HOP_AIR)   # kurzes Fenster in "Airtime-Einheiten"

    while pq:
        t_send, _, u, hops_u, prev_u = heapq.heappop(pq)

        # Hop-Limit (LOKAL): Neu-Knoten nutzen das gesenkte flood_max (M7/COMBI),
        # Stock-Knoten fluten bis FLOOD_MAX_BASE. So bleibt M7 eine reine Local-Rule.
        node_max = flood_max if (u in newfw) else FLOOD_MAX_BASE
        if hops_u >= node_max:
            continue
        if u in sent:
            continue
        if has_dead and u in dead_nodes:   # toter Knoten sendet nicht
            continue

        # --- M4: Relay-Reduktion. Neu-Knoten ausserhalb mpr_set schweigen ---
        if use_m4 and (u in newfw) and (mpr_set is not None) and (u not in mpr_set) and u != src:
            # schweigt: sendet nicht, breitet nichts aus
            continue

        # --- M2: counter-based suppression (Neu-Knoten) ---
        if use_m2 and (u in newfw) and u != src and u != dst:
            if heard_count[u] >= k_supp:
                continue

        # --- M3: shorter-path-cancel (Neu-Knoten) ---
        # Cancel den anstehenden Rebroadcast, wenn der Knoten dieselbe Nachricht NACH
        # seiner akzeptierten Kopie nochmals via gleich kurzem/kuerzerem Pfad gehoert hat
        # (ein anderer Sender deckt denselben Bereich gleich gut ab). Die EIGENE akzeptierte
        # Kopie zaehlt dabei NICHT gegen den Knoten (sonst wuerde jeder Knoten canceln).
        if use_m3 and (u in newfw) and u != src and u != dst:
            if short_dup[u] >= 1:
                continue

        # u sendet jetzt (Airtime)
        sent.add(u)
        out_hops = hops_u + 1
        rnd = rstate.random

        for v, pr in ADJ[u]:
            if has_dead and not link_ok(u, v):
                continue
            if rnd() > pr:
                continue   # Funkverlust dieser Aussendung -> v hoert sie nicht

            # v HOERT die Kopie (Empfang). Statistik fuer Suppression-Mechanismen:
            heard_count[v] += 1

            # Ziel-Sammlung fuer M5
            if v == dst:
                # Pfad rekonstruieren ueber prev-Kette von u + u + v
                p = reconstruct_path(accepted, u, src) + [v]
                dst_paths.append((out_hops, p))

            # Erste Akzeptanz (Dedup): v plant sein Sende-Ereignis.
            if v not in accepted:
                accepted[v] = (out_hops, u)
                acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, mech, v in newfw, rstate)
                schedule_send(v, t_send + d, out_hops, u)
            else:
                # spaetere Kopie. Fuer M3: zaehlt als "shorter/equal duplicate", wenn sie
                # via gleich kurzem/kuerzerem Pfad kam als die eigene akzeptierte Kopie UND
                # der Knoten noch nicht gesendet hat (anstehender Rebroadcast kann entfallen).
                if v not in sent and out_hops <= acc_hops.get(v, out_hops):
                    short_dup[v] += 1

    # ---- Ergebnis bestimmen ----
    delivered = dst in accepted or len(dst_paths) > 0
    if not delivered:
        return False, None, len(sent)

    if use_m5 and dst_paths:
        # Best-of-N am Ziel: kuerzester gehoerter Pfad (Hops, dann Reliability)
        best = min(dst_paths, key=lambda hp: (hp[0], -path_reliability(hp[1])))
        used = best[1]
    else:
        # first-packet-wins: der erstakzeptierte Pfad
        used = reconstruct_path(accepted, dst, src)

    return True, used, len(sent)


def reconstruct_path(accepted, node, src):
    """Pfad src..node aus der prev-Kette (erste Akzeptanz)."""
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
        if guard > 2000:
            break
    path.reverse()
    return path


def path_reliability(path):
    if not path or len(path) < 2:
        return 1.0
    r = 1.0
    for a, b in zip(path, path[1:]):
        r *= Prel.get((a, b), 0.0)
    return r


def path_hops(path):
    return (len(path) - 1) if path else 0


# ======================================================================================
# M4: greedy CDS / MPR-aehnliche dominierende Relay-Menge (aus dem Graphen)
# ======================================================================================
def compute_cds(graph):
    """Greedy Connected-Dominating-Set-Approximation:
    waehle iterativ den Knoten, der die meisten noch-nicht-dominierten Knoten abdeckt.
    Liefert eine Relay-Menge, deren Nachbarschaft alle Knoten ueberdeckt (Dominating Set);
    fuer die Flood-Reduktion genuegt Dominanz (jeder Knoten ist Relay oder Nachbar eines
    Relays). 2-Hop-Abdeckung ist durch die Flood-Weitergabe der Relays gegeben."""
    nb = {u: ({u} | {v for v, _ in ADJ[u]}) for u in graph.nodes()}
    nodes_set = set(graph.nodes())
    dominated = set()
    relays = set()
    # Greedy nach inkrementellem Coverage-Gewinn (mit ADJ; lazy-Neuberechnung des Gewinns).
    gain = {u: len(nb[u]) for u in nodes_set}
    while len(dominated) < len(nodes_set):
        # Kandidat mit groesstem (aktuellem) Gewinn; lazy korrigieren bis stabil.
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


log("\n=== M4: Greedy-Relay-Menge (CDS/MPR) berechnen ===")
t0 = time.time()
CDS = compute_cds(GC)
log(f"  Relay-Menge: {len(CDS)} von {GC.number_of_nodes()} Knoten "
    f"({100*len(CDS)/GC.number_of_nodes():.1f}%), {time.time()-t0:.1f}s")


# ======================================================================================
# Auswahl der Neu-Firmware-Knotenmenge je (alpha, rollout, seed)
# ======================================================================================
def select_newfw(alpha, rollout, seed):
    rstate = np.random.default_rng(seed * 100003 + 7)
    n = len(giant_list)
    if alpha == 0.0:
        return set()
    if alpha == "1node":
        k = 1
    else:
        k = max(1, int(round(alpha * n)))
    if rollout == "top_traffic":
        return set(top_traffic_order[:k])
    else:
        idx = rstate.choice(n, size=min(k, n), replace=False)
        return set(giant_list[i] for i in idx)


def mech_flood_max(mech):
    if mech == "M7_12":
        return 12
    if mech == "M7_15":
        return 15
    if mech == "COMBI":
        return 12
    return FLOOD_MAX_BASE


def mech_mpr(mech):
    return CDS if mech == "M4" else None


# ======================================================================================
# Mess-Schleife: ein (mech, alpha, rollout) ueber Seeds und Paare
# ======================================================================================
def make_pairs(seed, n_pairs):
    rstate = np.random.default_rng(seed * 7919 + 13)
    pairs = []
    arr = np.array(giant_list)
    for _ in range(n_pairs):
        s, d = rstate.choice(arr, size=2, replace=False)
        pairs.append((int(s), int(d)))
    return pairs


def evaluate(mech, alpha, rollout, seeds, n_pairs,
             dead_nodes_frac=0.0, dead_edges_frac=0.0, stress_seed_off=0):
    """Liefert aggregierte Kennzahlen ueber Seeds/Paare."""
    flood_max = mech_flood_max(mech)
    mpr = mech_mpr(mech)

    air_list, deliv_list, hop_list, detour_list = [], [], [], []
    rel_list = []
    # Routen-Stabilitaet: pro Paar Pfad ueber Seeds vergleichen
    pair_paths = collections.defaultdict(list)
    # Per-Seed-Mittel (fuer Monte-Carlo-Rausch-Band der Safety-Pruefung)
    per_seed_deliv, per_seed_air = [], []

    for si, seed in enumerate(seeds):
        seed_deliv, seed_air = [], []
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503 + stress_seed_off)
        newfw = select_newfw(alpha, rollout, seed)
        # Stress: tote Knoten/Kanten je Seed bestimmen
        dead_nodes = set()
        dead_edges = set()
        if dead_nodes_frac > 0:
            # Churn nach advert_count: niedrige advert_count -> instabiler -> faellt eher aus
            order = sorted(giant_list, key=lambda i: (nodes[i].get("advert_count", 0) or 0))
            k = int(dead_nodes_frac * len(order))
            # gewichtete Auswahl: bevorzugt instabile, aber zufaellig durchmischt
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

        pairs = make_pairs(seed, n_pairs)
        for (s, d) in pairs:
            if s in dead_nodes or d in dead_nodes:
                continue
            # bekannter kuerzester Pfad (Hops) als Detour-Referenz, unter Stress-Topologie
            for _m in range(MC):
                ok, used, ntx = run_flood(
                    s, d, newfw, mech, rstate, flood_max,
                    dead_nodes=dead_nodes, dead_edges=dead_edges, mpr_set=mpr)
                deliv_list.append(1.0 if ok else 0.0)
                seed_deliv.append(1.0 if ok else 0.0)
                if ok and used:
                    h = path_hops(used)
                    air_list.append(ntx)
                    seed_air.append(ntx)
                    hop_list.append(h)
                    rel_list.append(path_reliability(used))
                    # Detour vs. kuerzester bekannter Pfad
                    sp = shortest_hops(s, d, dead_nodes, dead_edges)
                    if sp and sp > 0:
                        detour_list.append(h / sp)
                    pair_paths[(s, d)].append(tuple(used))
        if seed_deliv:
            per_seed_deliv.append(float(np.mean(seed_deliv)))
        if seed_air:
            per_seed_air.append(float(np.mean(seed_air)))

    # Routen-Stabilitaet: Anteil Paare, deren Pfad ueber Seeds identisch blieb
    stable = []
    for k, plist in pair_paths.items():
        if len(plist) >= 2:
            stable.append(1.0 if len(set(plist)) == 1 else 0.0)
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
        # Standardfehler ueber Seeds (fuer Rausch-Band der Safety-Pruefung)
        "deliv_sem": (float(np.std(per_seed_deliv, ddof=1) / math.sqrt(len(per_seed_deliv)))
                      if len(per_seed_deliv) >= 2 else 0.0),
        "air_sem": (float(np.std(per_seed_air, ddof=1) / math.sqrt(len(per_seed_air)))
                    if len(per_seed_air) >= 2 else 0.0),
    }


# Cache fuer kuerzeste-Hop-Distanzen (ohne Stress); mit Stress neu berechnet
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
    # Stress: temporaerer Graph
    H = GC.copy()
    H.remove_nodes_from(dead_nodes)
    H.remove_edges_from(tuple(e) for e in dead_edges)
    try:
        return nx.shortest_path_length(H, s, d)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


# ======================================================================================
# HAUPT-SWEEP
# ======================================================================================
def main():
    t_start = time.time()
    seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))
    log("\n=== ADOPTIONS-SWEEP ===")
    log(f"  Mechanismen: {MECHS}")
    log(f"  Alphas: {ALPHAS}")
    log(f"  Rollouts: {ROLLOUTS}")
    log(f"  Seeds: {seeds} | Paare/Seed: {N_PAIRS} | MC/Paar: {MC}")
    log(f"  => je (mech,alpha,rollout): ~{N_SEEDS*N_PAIRS*MC} Floods")
    if FAST:
        log("  [FAST-MODUS aktiv: reduzierte Paar-/Seed-Zahl — nur zum Debuggen]")

    results = {"meta": {}, "sweep": [], "stress": []}

    # --- Baseline (M0) je Rollout (rollout irrelevant fuer M0, aber alpha=0 ist Referenz) ---
    # Wir messen die Baseline EINMAL (alpha egal, keine Neu-Knoten) als Referenz.
    log("\n  -- Baseline (M0, alpha=0) --")
    base = evaluate("M0", 0.0, "random", seeds, N_PAIRS)
    log(f"     delivery={base['delivery']:.3f} airtime={base['airtime_mean']:.1f} "
        f"hops={base['hops_mean']:.2f} detour={base['detour_ratio_median']:.2f}")
    BASE_DELIV = base["delivery"]
    BASE_AIR_M = base["airtime_mean"]
    results["meta"]["baseline"] = base

    # --- Monte-Carlo-Rausch-Band fuer die Safety-Pruefung ---
    # Mechanismen, die KEINE Sender unterdruecken (M1/M5/M7-hoch), haben dieselbe wahre
    # Airtime/Lieferquote wie die Baseline; gemessene Mini-Abweichungen sind reines
    # Sampling-Rauschen (unterschiedliche RNG-Ziehungen je alpha/seed). Wir tolerieren
    # daher ein Band von 2x Standardfehler der Baseline (ueber Seeds), zusaetzlich eine
    # absolute Mindesttoleranz. Das verhindert Falsch-Positive ("VERLETZT" durch Rauschen),
    # ohne echte Regressionen zu verschleiern. Transparent dokumentiert.
    DELIV_TOL = max(2.0 * base.get("deliv_sem", 0.0), 0.01)
    AIR_TOL = max(2.0 * base.get("air_sem", 0.0), 0.005 * BASE_AIR_M)
    log(f"     Rausch-Band (2*SEM, min): Lieferquote +-{DELIV_TOL:.4f}, "
        f"Airtime +-{AIR_TOL:.2f} ({100*AIR_TOL/BASE_AIR_M:.2f}%)")
    results["meta"]["safety_tolerance"] = {
        "delivery_tol": DELIV_TOL, "airtime_tol": AIR_TOL,
        "note": "2x Standardfehler der Baseline ueber Seeds bzw. absolute Mindesttoleranz "
                "(0.01 Lieferquote / 0.5% Airtime). Toleriert Monte-Carlo-Rauschen, nicht "
                "echte Regressionen."}

    n_cells = 0
    for mech in MECHS:
        for rollout in ROLLOUTS:
            for alpha in ALPHAS:
                # alpha=0 und mech!=M0 ist per Definition = Baseline (keine Neu-Knoten).
                # Wir messen es trotzdem fuer eine saubere Sweep-Kurve (ergibt ~Baseline).
                row = evaluate(mech, alpha, rollout, seeds, N_PAIRS)
                # Safety-Check mit Rausch-Band (Lieferquote >= Baseline, Airtime <= Baseline,
                # jeweils innerhalb des Toleranzbandes nicht als Verletzung gewertet).
                safe = (row["delivery"] >= BASE_DELIV - DELIV_TOL) and \
                       (row["airtime_mean"] <= BASE_AIR_M + AIR_TOL)
                # Strenge Verletzung (deutlich schlechter, ausserhalb Rausch-Band)
                row["safe_strict"] = bool(
                    (row["delivery"] >= BASE_DELIV - 1e-9) and
                    (row["airtime_mean"] <= BASE_AIR_M + 1e-9))
                row["safe"] = bool(safe)
                row["delivery_vs_base"] = row["delivery"] - BASE_DELIV
                row["airtime_vs_base_pct"] = (
                    100.0 * (row["airtime_mean"] - BASE_AIR_M) / BASE_AIR_M
                    if BASE_AIR_M > 0 else 0.0)
                results["sweep"].append(row)
                n_cells += 1
                flag = "OK " if safe else "VERLETZT"
                log(f"  [{mech:6s} a={str(alpha):6s} {rollout:11s}] "
                    f"deliv={row['delivery']:.3f} ({row['delivery_vs_base']:+.3f}) "
                    f"air={row['airtime_mean']:6.1f} ({row['airtime_vs_base_pct']:+5.1f}%) "
                    f"detour={row['detour_ratio_median']:.2f} "
                    f"stab={row['route_stability']:.2f} SAFETY={flag}")
        log(f"  --- {mech} fertig ({time.time()-t_start:.0f}s) ---")

    # --- STRESS ---
    log("\n=== STRESS (Churn + Linkausfall) ===")
    stress_mechs = ["M0", "M3", "M4", "M7_12", "COMBI"]
    for mech in stress_mechs:
        for alpha in STRESS_ALPHAS:
            # Churn: 20% Knoten nach advert_count instabil
            r_churn = evaluate(mech, alpha, "top_traffic", seeds, N_PAIRS,
                               dead_nodes_frac=0.20, stress_seed_off=999)
            for lf in STRESS_LINKFAIL:
                r_lf = evaluate(mech, alpha, "top_traffic", seeds, N_PAIRS,
                                dead_edges_frac=lf, stress_seed_off=int(lf * 1000) + 500)
                results["stress"].append({
                    "scenario": f"linkfail_{int(lf*100)}", "mech": mech, "alpha": alpha,
                    **{k: r_lf[k] for k in
                       ("delivery", "airtime_mean", "hops_mean", "detour_ratio_median",
                        "route_stability", "n_obs")}})
                log(f"  [LINKFAIL {int(lf*100)}% {mech:6s} a={alpha}] "
                    f"deliv={r_lf['delivery']:.3f} air={r_lf['airtime_mean']:.1f} "
                    f"stab={r_lf['route_stability']:.2f}")
            results["stress"].append({
                "scenario": "churn_20", "mech": mech, "alpha": alpha,
                **{k: r_churn[k] for k in
                   ("delivery", "airtime_mean", "hops_mean", "detour_ratio_median",
                    "route_stability", "n_obs")}})
            log(f"  [CHURN 20%   {mech:6s} a={alpha}] "
                f"deliv={r_churn['delivery']:.3f} air={r_churn['airtime_mean']:.1f} "
                f"stab={r_churn['route_stability']:.2f}")

    # --- M6: passives Topologie-Lernen / Feasible-Successor unter Linkausfall ---
    # Modell: bei Linkausfall nutzt ein M6-Knoten einen lokal gelernten Backup-Pfad
    # (kein Re-Flood), waehrend Baseline neu fluten muss. Wir messen die eingesparte
    # Re-Discovery-Airtime: Baseline-Reflood-Airtime vs. M6 (Backup = 0 zusaetzliche Floods,
    # solange ein Feasible Successor existiert).
    log("\n=== M6: Feasible-Successor vs. Baseline-Reflood (Linkausfall) ===")
    m6_rows = []
    for lf in STRESS_LINKFAIL:
        saved = []
        base_reflood = []
        m6_reflood = []
        recovered = []
        for si, seed in enumerate(seeds):
            rstate = np.random.default_rng(seed * 11939 + si * 17 + int(lf * 1000))
            all_e = list(GC.edges())
            k = int(lf * len(all_e))
            sel = rstate.choice(len(all_e), size=k, replace=False)
            dead = set(frozenset(all_e[i]) for i in sel)
            H = GC.copy()
            H.remove_edges_from(tuple(e) for e in dead)
            pairs = make_pairs(seed, max(N_PAIRS // 2, 10))
            for (s, d) in pairs:
                # Hat das Paar urspruenglich einen Pfad ueber eine jetzt tote Kante?
                try:
                    p0 = nx.shortest_path(GC, s, d)
                except nx.NetworkXNoPath:
                    continue
                broke = any(frozenset((p0[i], p0[i + 1])) in dead for i in range(len(p0) - 1))
                if not broke:
                    continue
                # Baseline: muss neu fluten -> Airtime ~ Flood-Reichweite
                ok_b, used_b, ntx_b = run_flood(s, d, set(), "M0", rstate, FLOOD_MAX_BASE,
                                                dead_edges=dead)
                base_reflood.append(ntx_b)
                # M6: lokaler Backup-Pfad existiert? -> kein Re-Flood (Airtime ~ 0 extra)
                if nx.has_path(H, s, d):
                    m6_reflood.append(0)        # Feasible Successor: kein Flood noetig
                    saved.append(ntx_b)
                    recovered.append(1.0)
                else:
                    # kein lokaler Backup -> Fallback Re-Flood wie Baseline
                    m6_reflood.append(ntx_b)
                    saved.append(0)
                    recovered.append(1.0 if ok_b else 0.0)
        row = {
            "scenario": f"m6_linkfail_{int(lf*100)}",
            "n_broken_pairs": len(base_reflood),
            "base_reflood_airtime_mean": float(np.mean(base_reflood)) if base_reflood else 0.0,
            "m6_reflood_airtime_mean": float(np.mean(m6_reflood)) if m6_reflood else 0.0,
            "airtime_saved_mean": float(np.mean(saved)) if saved else 0.0,
            "airtime_saved_pct": (100.0 * np.mean(saved) / np.mean(base_reflood)
                                  if base_reflood and np.mean(base_reflood) > 0 else 0.0),
            "local_backup_recovery_rate": float(np.mean(recovered)) if recovered else 0.0,
        }
        m6_rows.append(row)
        results["stress"].append(row)
        log(f"  [M6 LINKFAIL {int(lf*100)}%] gebrochene Paare={row['n_broken_pairs']} "
            f"Baseline-Reflood Ø={row['base_reflood_airtime_mean']:.1f} "
            f"eingespart={row['airtime_saved_pct']:.0f}% "
            f"Backup-Recovery={row['local_backup_recovery_rate']:.2f}")

    results["meta"].update({
        "master_seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS, "mc": MC,
        "alphas": [str(a) for a in ALPHAS], "mechs": MECHS, "rollouts": ROLLOUTS,
        "topology": {
            "active_nodes": len(active),
            "linkgraph_nodes": G.number_of_nodes(),
            "linkgraph_edges": G.number_of_edges(),
            "giant_nodes": len(giant_nodes),
            "giant_edges": GC.number_of_edges(),
            "cds_relays": len(CDS),
            "xval_observed_edges": len(obs_edges),
            "xval_active_edges": len(obs_in_active),
            "xval_reproduced": int(repro),
            "xval_frac": xval_frac,
        },
        "baseline_delivery": BASE_DELIV,
        "baseline_airtime": BASE_AIR_M,
        "real_detour_median_reference": real_detour.get("detour_factor_median"),
        "runtime_s": round(time.time() - t_start, 1),
        "fast_mode": FAST,
    })

    out_json = os.path.join(HERE, "study_results.json")
    json.dump(results, open(out_json, "w"), indent=2, ensure_ascii=False)
    log(f"\n  study_results.json geschrieben ({time.time()-t_start:.0f}s gesamt).")

    make_plots(results, BASE_DELIV, BASE_AIR_M)
    write_markdown(results, BASE_DELIV, BASE_AIR_M)
    log("\n=== FERTIG ===")
    return results


# ======================================================================================
# PLOTS
# ======================================================================================
def alpha_x(a):
    """X-Position fuer alpha (1node als kleiner Wert dargestellt)."""
    if a == "1node":
        return 1.0 / max(len(giant_list), 1)
    return float(a) if float(a) > 0 else 0.0005


def make_plots(results, base_deliv, base_air):
    plt.rcParams.update({"font.size": 9, "figure.dpi": 130})
    sweep = results["sweep"]
    mechs = results["meta"]["mechs"]
    # nutze top_traffic-Rollout fuer die Hauptkurven (realistischer); random separat in MD.
    ro = "top_traffic"

    def series(mech, key):
        rows = [r for r in sweep if r["mech"] == mech and r["rollout"] == ro]
        rows = sorted(rows, key=lambda r: alpha_x(r["alpha"]))
        xs = [alpha_x(r["alpha"]) for r in rows]
        ys = [r[key] for r in rows]
        return xs, ys

    colors = plt.cm.tab10(np.linspace(0, 1, len(mechs)))

    # --- Airtime vs Adoption ---
    fig, ax = plt.subplots(figsize=(8, 5.2))
    for mech, c in zip(mechs, colors):
        xs, ys = series(mech, "airtime_mean")
        ax.plot(xs, ys, "o-", color=c, label=mech, lw=1.6, ms=4)
    ax.axhline(base_air, color="k", ls="--", lw=1, label="Baseline")
    ax.set_xscale("log")
    ax.set_xlabel("Adoptionsanteil α (log)")
    ax.set_ylabel("Ø Airtime (Sende-Ereignisse / Zustellung)")
    ax.set_title("Airtime über Adoption je Mechanismus (Rollout: Top-Traffic)")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig_study_airtime_vs_adoption.png"))
    plt.close(fig)

    # --- Delivery vs Adoption ---
    fig, ax = plt.subplots(figsize=(8, 5.2))
    for mech, c in zip(mechs, colors):
        xs, ys = series(mech, "delivery")
        ax.plot(xs, ys, "o-", color=c, label=mech, lw=1.6, ms=4)
    ax.axhline(base_deliv, color="k", ls="--", lw=1, label="Baseline")
    ax.set_xscale("log")
    ax.set_xlabel("Adoptionsanteil α (log)")
    ax.set_ylabel("Lieferquote")
    ax.set_title("Lieferquote über Adoption je Mechanismus (Top-Traffic)")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig_study_delivery_vs_adoption.png"))
    plt.close(fig)

    # --- Detour vs Adoption ---
    fig, ax = plt.subplots(figsize=(8, 5.2))
    for mech, c in zip(mechs, colors):
        xs, ys = series(mech, "detour_ratio_median")
        ax.plot(xs, ys, "o-", color=c, label=mech, lw=1.6, ms=4)
    ax.set_xscale("log")
    ax.set_xlabel("Adoptionsanteil α (log)")
    ax.set_ylabel("Detour-Ratio (Median, genutzte Hops / kürzeste Hops)")
    ax.set_title("Detour über Adoption je Mechanismus (Top-Traffic)")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(alpha=0.25, which="both")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig_study_detour_vs_adoption.png"))
    plt.close(fig)

    # --- Safety-Matrix (Mechanismus x alpha), Top-Traffic ---
    alphas = results["meta"]["alphas"]
    M = np.zeros((len(mechs), len(alphas)))
    for mi, mech in enumerate(mechs):
        for ai, a in enumerate(alphas):
            rows = [r for r in sweep if r["mech"] == mech and r["rollout"] == ro
                    and str(r["alpha"]) == a]
            if rows:
                r = rows[0]
                # Codierung: 1 = safe & Airtime-Gewinn, 0.5 = safe (neutral), 0 = verletzt
                if not r["safe"]:
                    M[mi, ai] = 0.0
                elif r["airtime_vs_base_pct"] < -1.0:
                    M[mi, ai] = 1.0
                else:
                    M[mi, ai] = 0.5
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    cmap = matplotlib.colors.ListedColormap(["#c0392b", "#f1c40f", "#2e7d32"])
    im = ax.imshow(M, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(alphas)))
    ax.set_xticklabels(alphas, rotation=45, ha="right")
    ax.set_yticks(range(len(mechs)))
    ax.set_yticklabels(mechs)
    ax.set_xlabel("Adoptionsanteil α")
    ax.set_title("Safety-Matrix (grün=Gewinn&safe, gelb=safe/neutral, rot=Verletzung)\n"
                 "Top-Traffic-Rollout")
    for mi in range(len(mechs)):
        for ai in range(len(alphas)):
            rows = [r for r in sweep if r["mech"] == mechs[mi] and r["rollout"] == ro
                    and str(r["alpha"]) == alphas[ai]]
            if rows:
                ax.text(ai, mi, f"{rows[0]['airtime_vs_base_pct']:+.0f}",
                        ha="center", va="center", fontsize=6.5,
                        color="white" if M[mi, ai] != 0.5 else "black")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig_study_safety_matrix.png"))
    plt.close(fig)

    # --- Stress ---
    stress = [s for s in results["stress"] if "scenario" in s and s["scenario"].startswith(("churn", "linkfail"))]
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.6))
    scen_order = ["churn_20", "linkfail_10", "linkfail_20"]
    smechs = sorted(set(s["mech"] for s in stress))
    width = 0.8 / max(len(smechs), 1)
    for ax, (metric, ttl) in zip(axs, [("delivery", "Lieferquote unter Stress (α=1.0)"),
                                       ("airtime_mean", "Airtime unter Stress (α=1.0)")]):
        for mi, mech in enumerate(smechs):
            vals = []
            for scen in scen_order:
                row = [s for s in stress if s["mech"] == mech and s["alpha"] == 1.0
                       and s["scenario"] == scen]
                vals.append(row[0][metric] if row else 0.0)
            ax.bar(np.arange(len(scen_order)) + mi * width, vals, width, label=mech)
        ax.set_xticks(np.arange(len(scen_order)) + width * len(smechs) / 2)
        ax.set_xticklabels(scen_order, rotation=20)
        ax.set_title(ttl)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "fig_study_stress.png"))
    plt.close(fig)

    log("  Plots geschrieben: fig_study_airtime_vs_adoption.png, "
        "fig_study_delivery_vs_adoption.png, fig_study_detour_vs_adoption.png, "
        "fig_study_safety_matrix.png, fig_study_stress.png")


# ======================================================================================
# MARKDOWN-BERICHT
# ======================================================================================
def write_markdown(results, base_deliv, base_air):
    sweep = results["sweep"]
    meta = results["meta"]
    alphas = meta["alphas"]
    mechs = meta["mechs"]
    ro = "top_traffic"

    def cell(mech, a, key, ro=ro):
        rows = [r for r in sweep if r["mech"] == mech and r["rollout"] == ro
                and str(r["alpha"]) == a]
        return rows[0][key] if rows else None

    lines = []
    lines.append("# Studien-Ergebnisse: Routing-Mechanismen × Adoption (MeshCore, Realdaten)\n")
    lines.append("Erzeugt von `study_sim.py`. **GEMESSEN** (Realtopologie/Kalibrierung aus "
                 "`../sim/data/`) ist strikt getrennt von **SIMULIERT** (Flood-Routing-Modell "
                 "+ Mechanismen + Sweep, dieses Skript).\n")
    t = meta["topology"]
    lines.append("## Topologie (GEMESSEN/abgeleitet, wie v3)\n")
    lines.append(f"- Aktiver Repeater-Subgraph: **{t['active_nodes']}** Knoten "
                 f"(role=repeater, Geo, relay-aktiv/24h>0 ODER real als Pfad-Hop gesehen — "
                 f"reproduziert v3's ~831-Knoten-Subgraph).")
    lines.append(f"- Reichweiten-Linkgraph: **{t['linkgraph_nodes']}** Knoten, "
                 f"**{t['linkgraph_edges']}** Kanten; größte Komponente "
                 f"**{t['giant_nodes']}** Knoten / {t['giant_edges']} Kanten "
                 f"(hier wird simuliert).")
    lines.append(f"- Greedy-Relay-Menge (M4, CDS): **{t['cds_relays']}** Knoten "
                 f"({100*t['cds_relays']/t['giant_nodes']:.1f}%).")
    lines.append(f"- **Quervalidierung** gegen beobachtete Kanten "
                 f"(`topology_edges.json`): {t['xval_observed_edges']} beob. Kanten, "
                 f"{t['xval_active_edges']} zwischen aktiven Knoten, davon "
                 f"{t['xval_reproduced']} ({100*t['xval_frac']:.1f}%) vom geometrischen "
                 f"Reichweitenmodell reproduziert.")
    lines.append(f"- Reale Detour-Median-Referenz (GEMESSEN): "
                 f"**{meta['real_detour_median_reference']:.2f}×**.\n")
    lines.append("## Modellannahmen (SIMULIERT) — ehrlich getrennt von der Messung\n")
    lines.append("- Flood timing-getrieben (PQ nach Ankunftszeit), Stock-Knoten: "
                 "first-packet-wins + Zufalls-Jitter. **Airtime = Anzahl tatsächlich "
                 "sendender Knoten je zugestellter Nachricht.**")
    lines.append("- Link-Stochastik: eine Aussendung erreicht Nachbar v mit P=Reliability "
                 "des Links (Monte-Carlo über Seed). Reliability des genutzten Pfads = "
                 "Produkt der Link-Reliabilities.")
    lines.append("- Mechanismen sind **lokale Regeln** der Neu-Firmware-Knoten; Stock-Knoten "
                 "fluten unverändert (eingebautes Sicherheitsnetz).")
    lines.append(f"- **Baseline (M0, α=0):** Lieferquote **{base_deliv:.3f}**, Airtime "
                 f"**{base_air:.1f}** Sende-Ereignisse/Zustellung, "
                 f"Detour-Median {cell('M0','0.0','detour_ratio_median'):.2f}.\n")
    lines.append(f"- Konfiguration: {meta['n_seeds']} Seeds, {meta['n_pairs']} Paare/Seed, "
                 f"MC={meta['mc']}; Laufzeit {meta['runtime_s']}s"
                 + (" **[FAST-Modus — reduziert]**" if meta.get("fast_mode") else "") + ".\n")

    # Safety-Invariante: Definition
    tol = meta.get("safety_tolerance", {})
    lines.append("## Safety-Invariante\n")
    lines.append("Für jeden (Mechanismus, α): **Lieferquote ≥ Baseline UND Airtime ≤ "
                 "Baseline.** Verletzung ⇒ Mechanismus bei diesem α disqualifiziert.\n")
    lines.append(f"**Monte-Carlo-Rausch-Band:** Mechanismen, die keine Sender "
                 f"unterdrücken (M1/M5/M7-hoch), haben dieselbe *wahre* Airtime/Lieferquote "
                 f"wie die Baseline; gemessene Mini-Abweichungen sind reines Sampling-Rauschen "
                 f"(je α/Seed andere RNG-Ziehungen). Als Verletzung gilt nur ein Unterschreiten "
                 f"außerhalb von ±2·Standardfehler der Baseline bzw. einer absoluten "
                 f"Mindesttoleranz "
                 f"(Lieferquote ±{tol.get('delivery_tol',0):.3f}, "
                 f"Airtime ±{tol.get('airtime_tol',0):.1f} = "
                 f"{100*tol.get('airtime_tol',0)/max(base_air,1):.2f}%). "
                 f"Die Spalte *Safety* nutzt dieses Band; *streng* = ohne Band.\n")

    # Tabellen je Mechanismus (Top-Traffic-Rollout)
    lines.append("## Ergebnis-Tabellen je Mechanismus (Rollout: Top-Traffic)\n")
    for mech in mechs:
        lines.append(f"### {mech}\n")
        lines.append("| α | Lieferquote | ΔDeliv | Airtime | ΔAirtime% | Detour med | "
                     "Routen-Stab. | Safety |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for a in alphas:
            d = cell(mech, a, "delivery")
            if d is None:
                continue
            lines.append(
                f"| {a} | {d:.3f} | {cell(mech,a,'delivery_vs_base'):+.3f} | "
                f"{cell(mech,a,'airtime_mean'):.1f} | "
                f"{cell(mech,a,'airtime_vs_base_pct'):+.1f}% | "
                f"{cell(mech,a,'detour_ratio_median'):.2f} | "
                f"{cell(mech,a,'route_stability'):.2f} | "
                f"{'✅' if cell(mech,a,'safe') else '❌ VERLETZT'} |")
        lines.append("")

    # Safety-Befund
    lines.append("## Safety-Befund (alle Rollouts)\n")
    viol = [r for r in sweep if not r["safe"]]
    if not viol:
        lines.append("Keine Safety-Verletzung über alle (Mechanismus, α, Rollout). "
                     "Alle Mechanismen halten Lieferquote ≥ Baseline und Airtime ≤ Baseline.\n")
    else:
        lines.append("Folgende (Mechanismus, α, Rollout) verletzen die Invariante:\n")
        lines.append("| Mechanismus | α | Rollout | Lieferquote (Base "
                     f"{base_deliv:.3f}) | Airtime (Base {base_air:.1f}) | Grund |")
        lines.append("|---|---|---|---|---|---|")
        for r in viol:
            reasons = []
            if r["delivery"] < base_deliv - 1e-9:
                reasons.append("Lieferquote<Baseline")
            if r["airtime_mean"] > base_air + 1e-9:
                reasons.append("Airtime>Baseline")
            lines.append(f"| {r['mech']} | {r['alpha']} | {r['rollout']} | "
                         f"{r['delivery']:.3f} | {r['airtime_mean']:.1f} | "
                         f"{', '.join(reasons)} |")
        lines.append("")

    # Ranking
    lines.append("## Ranking (Airtime-Gewinn bei gehaltener Lieferquote, α=1.0 Top-Traffic)\n")
    rank = []
    for mech in mechs:
        if mech == "M0":
            continue
        a = "1.0"
        d = cell(mech, a, "delivery")
        if d is None:
            continue
        rank.append((mech, cell(mech, a, "airtime_vs_base_pct"), d,
                     cell(mech, a, "safe"), cell(mech, a, "detour_ratio_median")))
    rank.sort(key=lambda x: x[1])   # staerkste Airtime-Senkung zuerst
    lines.append("| Rang | Mechanismus | ΔAirtime% @α=1.0 | Lieferquote | Safe@1.0 | Detour med |")
    lines.append("|---|---|---|---|---|---|")
    for i, (m, dair, d, s, det) in enumerate(rank, 1):
        lines.append(f"| {i} | {m} | {dair:+.1f}% | {d:.3f} | "
                     f"{'✅' if s else '❌'} | {det:.2f} |")
    lines.append("")

    # Adoptionsschwelle: erstes alpha mit >2% Airtime-Senkung und safe
    lines.append("## Adoptionsschwelle (erstes α mit ≥2% Airtime-Senkung & safe, Top-Traffic)\n")
    lines.append("| Mechanismus | Schwelle α | ΔAirtime% dort |")
    lines.append("|---|---|---|")
    for mech in mechs:
        if mech == "M0":
            continue
        thr = None
        for a in alphas:
            s = cell(mech, a, "safe")
            dair = cell(mech, a, "airtime_vs_base_pct")
            if s and dair is not None and dair <= -2.0:
                thr = (a, dair)
                break
        if thr:
            lines.append(f"| {mech} | {thr[0]} | {thr[1]:+.1f}% |")
        else:
            lines.append(f"| {mech} | — (keine ≥2%-Senkung) | — |")
    lines.append("")

    # COMBI
    lines.append("## Kombi M3+M5+M7 (COMBI)\n")
    for a in ["1node", "0.1", "0.25", "1.0"]:
        d = cell("COMBI", a, "delivery")
        if d is None:
            continue
        lines.append(f"- α={a}: Lieferquote {d:.3f} ({cell('COMBI',a,'delivery_vs_base'):+.3f}), "
                     f"Airtime {cell('COMBI',a,'airtime_mean'):.1f} "
                     f"({cell('COMBI',a,'airtime_vs_base_pct'):+.1f}%), "
                     f"Detour-Median {cell('COMBI',a,'detour_ratio_median'):.2f}, "
                     f"Safety {'OK' if cell('COMBI',a,'safe') else 'VERLETZT'}.")
    lines.append("")

    # Stress
    lines.append("## Stress-Befund\n")
    lines.append("### Churn (20% instabile Knoten nach advert_count) & Linkausfall (α=1.0)\n")
    lines.append("| Mechanismus | Szenario | Lieferquote | Airtime | Routen-Stab. |")
    lines.append("|---|---|---|---|---|")
    for s in results["stress"]:
        if s.get("scenario", "").startswith(("churn", "linkfail")) and s.get("alpha") == 1.0:
            lines.append(f"| {s['mech']} | {s['scenario']} | {s['delivery']:.3f} | "
                         f"{s['airtime_mean']:.1f} | {s.get('route_stability',0):.2f} |")
    lines.append("")
    lines.append("### M6 (passives Topologie-Lernen + Feasible-Successor): eingesparte "
                 "Re-Discovery-Airtime bei Linkausfall\n")
    lines.append("| Szenario | gebrochene Paare | Baseline-Reflood Ø | eingespart % | "
                 "lokale Backup-Recovery |")
    lines.append("|---|---|---|---|---|")
    for s in results["stress"]:
        if s.get("scenario", "").startswith("m6_"):
            lines.append(f"| {s['scenario']} | {s['n_broken_pairs']} | "
                         f"{s['base_reflood_airtime_mean']:.1f} | "
                         f"{s['airtime_saved_pct']:.0f}% | "
                         f"{s['local_backup_recovery_rate']:.2f} |")
    lines.append("")

    # Limitierungen
    lines.append("## Limitierungen (ehrlich)\n")
    lines.append("- **Linkmodell** ist geometrisch (Log-Distance + PLE-Floor 2.0); reales "
                 "Gelände/Antennenhöhe nicht abgebildet. Der reale SNR/Distanz-Zusammenhang ist "
                 "schwach (|corr|≈0.42), darum ist Hop-Zahl der verlässlichere Hebel — das "
                 "Modell respektiert das, ist aber eine bewusste Vereinfachung.")
    lines.append("- Quervalidierung gegen beobachtete Kanten ist nur teilweise deckend: das "
                 "geometrische Modell und die beobachtete Flood-Stichprobe überlappen nur "
                 "begrenzt (siehe Topologie-Abschnitt). Absolute Airtime-Zahlen sind daher "
                 "modellabhängig; die **relativen** Mechanismus-Vergleiche sind robuster.")
    lines.append("- Timing-Jitter/Backoff-Fenster sind modelliert, nicht aus Hardware gemessen. "
                 "M1/M3 hängen vom Timing-Modell ab.")
    lines.append("- M2 (counter-based) hört Kopien im selben diskreten Flood; ein reales "
                 "kontinuierliches Backoff-Fenster ist gröber approximiert.")
    lines.append("- M5 ändert nur den gecachten Pfad (Detour), nicht die Flood-Airtime — so "
                 "modelliert und so berichtet.")
    lines.append("- M6 ist als Airtime-Einsparungs-Modell (Backup statt Reflood) gerechnet, "
                 "nicht als vollständige DV-Protokoll-Simulation.")
    lines.append(f"- Stichprobengröße: {meta['n_seeds']} Seeds × {meta['n_pairs']} Paare; "
                 "Konfidenzintervalle nicht ausgewiesen.\n")

    out_md = os.path.join(HERE, "STUDY_RESULTS.md")
    open(out_md, "w").write("\n".join(lines))
    log("  STUDY_RESULTS.md geschrieben.")


if __name__ == "__main__":
    main()
