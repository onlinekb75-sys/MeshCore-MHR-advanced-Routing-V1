#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MECHANISMUS A — Proaktiver Regions-/Backbone-Control-Plane (MHR Phase 2)
========================================================================
Ehrliche Nutzen-vs-Verlust-vs-NETTO-Airtime-Bilanz auf der ECHTEN Topologie.

Frage (Kernzahl): Spart ein proaktiver Backbone (Distance-Vector unter stabilen
Repeatern, der DATA-Unicast statt Flood erlaubt) NETTO Airtime — NACH Abzug der
periodischen Kontroll-Updates, die er selbst kostet? Und falls ja: in welchem
Fenster (Backbone-Größe, DV-Periode, Adoption)?

Methodik-Erbe (bewusst identisch zu mhr_sim_real_v4.py / study_sim.py):
  - ECHTE neighbor-graph-Topologie: echte Kanten + echtes Per-Link-SNR (avg_snr).
  - Flood = timing-getriebene PQ, first-packet-wins, Airtime = sendende Knoten.
  - >=5 Seeds, Seed 42 Master, reproduzierbar.

NEU ggü. v4: Airtime wird in ZWEI Einheiten geführt:
  (1) TX-Ereignisse (wie v4, vergleichbar mit Vorarbeit).
  (2) PHYSISCHE Time-on-Air (ms) per LoRa-ToA-Modell — nötig, weil Daten-Pakete
      und kleine DV-Kontroll-Pakete UNTERSCHIEDLICH groß sind. Nur in ms lassen
      sich Daten-Ersparnis und Kontroll-Kosten EHRLICH gegeneinander aufrechnen.

Honest scope: idealisierter Flood (keine Kollisionen), DV-Kosten als analytisches
Modell (Periode x ToA x sendende Backbone-Knoten), neighbor-graph = nur GENUTZTE
Links. Limitierungen ausführlich im Markdown.
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

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "..", "sim")
DATA = os.path.join(SIM, "data")
NG_F = os.path.join(DATA, "neighbor_graph.json")
NODES_F = os.path.join(DATA, "nodes.json")
CAL_F = os.path.join(DATA, "snr_calibration.json")

MASTER_SEED = 42
FAST = os.environ.get("BB_FAST", "0") == "1"
N_PAIRS = int(os.environ.get("BB_PAIRS", "120" if not FAST else "30"))
N_SEEDS = int(os.environ.get("BB_SEEDS", "6" if not FAST else "2"))

SNR_THR = -12.0
SNR_SCALE = 4.0

# Flood-Timing (identisch zu v4)
FLOOD_MAX_BASE = 64
BASE_AIR = 0.10
PER_HOP_AIR = 0.012
JITTER = 5.0


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------------------
# LoRa Time-on-Air (ms) — Semtech-Formel. EU868, MeshCore-typisch SF11/BW250 (long-fast).
# Wir brauchen ToA, um Daten-Pakete (groß) und DV-Kontroll-Pakete (klein) EHRLICH
# in derselben physischen Einheit (ms Funkbelegung) gegeneinander zu verrechnen.
# --------------------------------------------------------------------------------------
LORA_SF = int(os.environ.get("BB_SF", "11"))
LORA_BW = float(os.environ.get("BB_BW", "250000"))  # Hz
LORA_CR = 1            # coding rate 4/(4+CR) -> CR=1 => 4/5
LORA_PREAMBLE = 8
LORA_HEADER = 1        # explicit header
LORA_LDRO = 1 if (LORA_SF >= 11 and LORA_BW <= 125000) else 0  # low-data-rate-opt
# Bei BW250/SF11 ist LDRO meist aus; Symbolzeit klein -> wir setzen LDRO nach Regel.
if LORA_SF >= 11 and LORA_BW == 125000:
    LORA_LDRO = 1
else:
    LORA_LDRO = 0


def lora_toa_ms(payload_bytes):
    """Time-on-Air (ms) eines LoRa-Frames mit gegebener Payload (Bytes)."""
    Tsym = (2 ** LORA_SF) / LORA_BW * 1000.0   # ms
    n_pre = LORA_PREAMBLE + 4.25
    de = LORA_LDRO
    num = 8 * payload_bytes - 4 * LORA_SF + 28 + 16 - 20 * (1 - LORA_HEADER)
    den = 4 * (LORA_SF - 2 * de)
    n_payload = 8 + max(math.ceil(num / den) * (LORA_CR + 4), 0)
    return (n_pre + n_payload) * Tsym


# Paketgrößen (Bytes, inkl. Header). MeshCore-Pakete sind klein.
DATA_PKT_BYTES = int(os.environ.get("BB_DATA_BYTES", "40"))   # typ. Chat/Discovery-Frame
# DV-Kontroll-Paket: Header + k Einträge a (hash 2B + cost 1B + seqno 1B) ~ 4B/Eintrag.
DV_HEADER_BYTES = 8
DV_ENTRY_BYTES = 4
DATA_TOA = lora_toa_ms(DATA_PKT_BYTES)


def haversine(la1, lo1, la2, lo2):
    R = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


# ======================================================================================
# 1) REALE TOPOLOGIE (wie v4)
# ======================================================================================
log("=== 1) Reale Topologie aus neighbor_graph.json ===")
log(f"  LoRa-ToA-Modell: SF{LORA_SF}/BW{int(LORA_BW/1000)}k -> "
    f"Daten-Paket {DATA_PKT_BYTES}B = {DATA_TOA:.1f} ms ToA")
ng = json.load(open(NG_F))
nodes_full = json.load(open(NODES_F))["nodes"]
cal = json.load(open(CAL_F))
SNR_THR = float(cal.get("snr_threshold_db", SNR_THR))

pk_to_full = {}
for i, n in enumerate(nodes_full):
    pk = n.get("public_key")
    if pk:
        pk_to_full[pk.lower()] = i
_full_keys = list(pk_to_full.keys())


def resolve_node(pk):
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


NODE = {}
for x in ng["nodes"]:
    pk = x["pubkey"].lower()
    meta = {"name": x.get("name"), "role": x.get("role"),
            "neighbor_count": x.get("neighbor_count", 0),
            "lat": None, "lon": None, "relay24": 0, "traffic": 0.0,
            "useful": 0.0, "advert": 0}
    idx = resolve_node(pk)
    if idx is not None:
        n = nodes_full[idx]
        if valid_geo(n.get("lat"), n.get("lon")):
            meta["lat"], meta["lon"] = n["lat"], n["lon"]
        meta["relay24"] = n.get("relay_count_24h", 0) or 0
        meta["traffic"] = n.get("traffic_share_score", 0) or 0.0
        meta["useful"] = n.get("usefulness_score", 0) or 0.0
        meta["advert"] = n.get("advert_count", 0) or 0
        if not meta["role"]:
            meta["role"] = n.get("role")
    NODE[pk] = meta


def snr_reliability(snr_db):
    return float(np.clip(1.0 / (1.0 + math.exp(-(snr_db - SNR_THR) / SNR_SCALE)), 0.02, 0.995))


G = nx.Graph()
SNR, PREL, WEIGHT = {}, {}, {}
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
    etx = 1.0 / max(pr * pr, 1e-4)
    G.add_edge(u, v, snr=float(s), prel=pr, etx=etx, weight=w)
    SNR[(u, v)] = SNR[(v, u)] = float(s)
    PREL[(u, v)] = PREL[(v, u)] = pr
    WEIGHT[(u, v)] = WEIGHT[(v, u)] = w
    snr_used.append(float(s))
for x in ng["nodes"]:
    G.add_node(x["pubkey"].lower())

degs = [d for _, d in G.degree()]
comps = sorted(nx.connected_components(G), key=len, reverse=True)
giant = comps[0] if comps else set()
GC = G.subgraph(giant).copy()
giant_list = sorted(giant)
ADJ = {u: [(v, PREL[(u, v)]) for v in GC.neighbors(u)] for u in GC.nodes()}
deg_gc = dict(GC.degree())

log(f"  Kern: {G.number_of_nodes()} Knoten / {G.number_of_edges()} Kanten "
    f"(ambiguous verworfen: {n_ambig})")
log(f"  Riesenkomponente: {GC.number_of_nodes()} Knoten, {GC.number_of_edges()} Kanten, "
    f"Ø-Grad {2*GC.number_of_edges()/GC.number_of_nodes():.2f}, max {max(deg_gc.values())}")


# ======================================================================================
# 2) FLOOD-MODELL (wie v4) — Baseline-DATA-Airtime
# ======================================================================================
def rebroadcast_delay(hops, rstate):
    air = BASE_AIR + PER_HOP_AIR * hops
    return air + rstate.uniform(0.0, JITTER * air)


def reconstruct_path(accepted, node, src):
    path = [node]; cur = node; guard = 0
    while cur != src:
        info = accepted.get(cur)
        if info is None or info[1] is None:
            break
        cur = info[1]; path.append(cur); guard += 1
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


def run_flood(src, dst, rstate, flood_max, dead_nodes=None, dead_edges=None):
    """Idealisierter first-wins-Flood. Rückgabe (delivered, used_path, n_tx)."""
    dead_nodes = dead_nodes or set()
    dead_edges = dead_edges or set()
    has_dead = bool(dead_nodes) or bool(dead_edges)

    def link_ok(u, v):
        if v in dead_nodes:
            return False
        if frozenset((u, v)) in dead_edges:
            return False
        return True

    accepted = {}; acc_hops = {}; sent = set()
    seq = 0; pq = []

    def schedule_send(u, t_send, hops, prev):
        nonlocal seq
        heapq.heappush(pq, (t_send, seq, u, hops, prev)); seq += 1

    schedule_send(src, 0.0, 0, None)
    accepted[src] = (0, None); acc_hops[src] = 0
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
            if v not in accepted:
                accepted[v] = (out_hops, u); acc_hops[v] = out_hops
                d = rebroadcast_delay(out_hops, rstate)
                schedule_send(v, t_send + d, out_hops, u)
    delivered = dst in accepted
    if not delivered:
        return False, None, len(sent)
    return True, reconstruct_path(accepted, dst, src), len(sent)


# ======================================================================================
# 3) BACKBONE-AUSWAHL (stabile Repeater) + Unicast entlang proaktivem Pfad
# ======================================================================================
def stability_score(pk):
    """Stabilität für Backbone-Eignung: relay_count_24h dominiert (= aktiver Transit),
    Grad als Tie-Break (Hub/Border-Eigenschaft). Companions sind per Definition nicht
    backbone-fähig (leiten nicht weiter)."""
    m = NODE.get(pk, {})
    role = m.get("role")
    if role == "companion":
        return (-1, -1, -1)
    return (m.get("relay24", 0), deg_gc.get(pk, 0), m.get("traffic", 0.0))


backbone_order = sorted(giant_list, key=stability_score, reverse=True)
# nur backbone-fähige Knoten (keine Companions, müssen Repeater/room/observer sein)
backbone_candidates = [pk for pk in backbone_order
                       if NODE.get(pk, {}).get("role") != "companion"]
log(f"\n=== 3) Backbone-Auswahl: {len(backbone_candidates)} backbone-fähige Knoten "
    f"(Nicht-Companions) in der Riesenkomponente ===")


def select_backbone(size_frac, seed=MASTER_SEED, rollout="top_stable"):
    """Wähle Backbone-Teilmenge. size_frac = Anteil der backbone-fähigen Knoten."""
    n = len(backbone_candidates)
    k = max(1, int(round(size_frac * n)))
    if rollout == "top_stable":
        return set(backbone_candidates[:k])
    rstate = np.random.default_rng(seed * 100003 + 7)
    idx = rstate.choice(n, size=min(k, n), replace=False)
    return set(backbone_candidates[i] for i in idx)


def build_backbone_graph(bb_set, adoption=1.0, seed=MASTER_SEED):
    """Backbone-Routing-Graph: Subgraph auf bb-Knoten, die backbone-FÄHIG sind
    UND die Firmware adoptiert haben (adoption). Kanten = reale Links zwischen
    zwei aktiven Backbone-Knoten. ETX-Gewicht für proaktive Pfadberechnung.
    Liefert (BBG, active_bb_set)."""
    rstate = np.random.default_rng(seed * 911 + 17)
    bb_list = sorted(bb_set)
    if adoption >= 1.0:
        active = set(bb_list)
    else:
        k = int(round(adoption * len(bb_list)))
        sel = rstate.choice(len(bb_list), size=min(k, len(bb_list)), replace=False) \
            if bb_list else []
        active = set(bb_list[i] for i in sel)
    BBG = nx.Graph()
    BBG.add_nodes_from(active)
    for u in active:
        for v in GC.neighbors(u):
            if v in active:
                BBG.add_edge(u, v, etx=GC[u][v]["etx"])
    return BBG, active


def attach_repeater(node, active_bb, max_attach_hops=2):
    """Finde den nächsten aktiven Backbone-Knoten (Ingress/Egress) für einen
    Nicht-Backbone-Endpunkt via lokalem BFS (<= max_attach_hops Hops).
    Rückgabe (bb_node, hops) oder (None, None)."""
    if node in active_bb:
        return node, 0
    # lokaler BFS
    frontier = {node}
    visited = {node}
    for h in range(1, max_attach_hops + 1):
        nxt = set()
        for u in frontier:
            for v in GC.neighbors(u):
                if v in visited:
                    continue
                if v in active_bb:
                    return v, h
                visited.add(v); nxt.add(v)
        frontier = nxt
        if not frontier:
            break
    return None, None


def backbone_unicast(src, dst, BBG, active_bb, rstate):
    """Versuche, src->dst über den proaktiven Backbone zu routen.
    Modell:
      - src klebt an nächstem aktiven Backbone-Knoten (lokaler Flood, <=2 Hops).
      - dst ebenso.
      - Backbone berechnet ETX-kürzesten Pfad zwischen Ingress/Egress (proaktiv,
        keine Discovery-Airtime — der Pfad ist schon bekannt).
      - Unicast hop-für-hop: Airtime = Anzahl sendender Knoten = Hops gesamt.
    Rückgabe dict mit success/path/tx_events/data_toa_ms ODER None (-> Fallback Flood).
    """
    in_bb, in_h = attach_repeater(src, active_bb)
    out_bb, out_h = attach_repeater(dst, active_bb)
    if in_bb is None or out_bb is None:
        return None
    if in_bb == out_bb:
        bb_path = [in_bb]
    else:
        try:
            bb_path = nx.shortest_path(BBG, in_bb, out_bb, weight="etx")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
    # voller Pfad src ... in_bb (in_h Hops) ... bb_path ... out_bb (out_h Hops) ... dst
    # TX-Ereignisse (Unicast): jeder Hop = 1 sendender Knoten.
    # Ingress-Attach: in_h Hops lokaler Unicast (entlang gelernter Kette), gleiches egress.
    bb_hops = len(bb_path) - 1
    total_hops = in_h + bb_hops + out_h
    tx_events = total_hops  # Unicast: 1 TX pro Hop
    # Pfad-Reliability grob: Produkt entlang Backbone-Pfad (Attach-Hops konservativ ~0.9)
    rel = path_reliability(bb_path) * (0.9 ** (in_h + out_h))
    return {
        "success": True,
        "tx_events": tx_events,
        "data_toa_ms": tx_events * DATA_TOA,
        "hops": total_hops,
        "bb_hops": bb_hops,
        "reliability": rel,
        "in_h": in_h, "out_h": out_h,
    }


# ======================================================================================
# 4) DV-KONTROLL-KOSTEN-MODELL (der PREIS)
# ======================================================================================
def dv_control_cost(active_bb, BBG, dv_period_s, horizon_s, region_cap=None):
    """Kontroll-Airtime des proaktiven Backbones über einen Zeit-Horizont.

    Modell (konservativ, an MHR-v2-Design angelehnt):
      - Jeder aktive Backbone-Knoten sendet periodisch (dv_period_s) EINEN
        Zero-Hop-DV-Broadcast an seine Backbone-Nachbarn.
      - Paketgröße = Header + (Anzahl DV-Einträge) * Eintragsgröße.
        Anzahl Einträge ~ Anzahl bekannter Ziele.
      - Updates sind Zero-Hop (kein Flood) -> 1 TX pro Knoten pro Periode.

    region_cap = None  -> FLACHES DV: jeder Knoten trägt ALLE Ziele seiner
        Backbone-Komponente (skaliert O(N) -> pessimistisch, aber ehrlich für
        einen naiven Backbone ohne Hierarchie).
    region_cap = int   -> REGIONS-HIERARCHIE (H1): intra-Region DV ist auf
        region_cap Ziele gedeckelt; Inter-Region nur aggregierte Region-Einträge
        (Anzahl Regionen). Modelliert die design-intendierte Skalierung
        O(Regionsgröße) statt O(Netz).

    Rückgabe dict: tx_events, control_toa_ms (über horizon_s), avg_entries, ...
    """
    n_active = len(active_bb)
    if n_active == 0:
        return {"tx_events": 0.0, "control_toa_ms": 0.0,
                "avg_entries": 0.0, "n_active": 0, "n_periods": 0.0}
    # Anzahl Ziele je Knoten: pro Backbone-Komponente, in der der Knoten liegt.
    comp_size = {}
    for comp in nx.connected_components(BBG):
        cs = len(comp)
        for u in comp:
            comp_size[u] = cs
    n_periods = horizon_s / dv_period_s
    total_tx = 0.0
    total_toa = 0.0
    entries_acc = 0.0
    for u in active_bb:
        cs = comp_size.get(u, 1)
        if region_cap is None:
            # Flaches DV: alle Ziele der Komponente außer sich selbst.
            n_entries = max(cs - 1, 1)
        else:
            # Regions-Hierarchie: intra-Region (<= region_cap Ziele) +
            # aggregierte Inter-Region-Einträge (#Regionen in der Komponente).
            intra = min(max(cs - 1, 1), region_cap - 1) if region_cap > 1 else 1
            n_regions = max(1, math.ceil(cs / region_cap))
            n_entries = max(intra + n_regions, 1)
        pkt_bytes = DV_HEADER_BYTES + n_entries * DV_ENTRY_BYTES
        # Pakete sind durch Funk-MTU begrenzt -> ggf. mehrere Frames.
        MAX_PAYLOAD = 200
        n_frames = max(1, math.ceil(pkt_bytes / MAX_PAYLOAD))
        toa_per_period = 0.0
        rem = pkt_bytes
        for _ in range(n_frames):
            b = min(rem, MAX_PAYLOAD)
            toa_per_period += lora_toa_ms(max(b, 1))
            rem -= b
        total_tx += n_frames * n_periods
        total_toa += toa_per_period * n_periods
        entries_acc += n_entries
    return {
        "tx_events": total_tx,
        "control_toa_ms": total_toa,
        "avg_entries": entries_acc / n_active,
        "n_active": n_active,
        "n_periods": n_periods,
    }


# ======================================================================================
# 5) HAUPT-EXPERIMENT: Daten- / Kontroll- / NETTO-Airtime
# ======================================================================================
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


# Baseline-Flood-Kennzahlen (DATA only, kein Backbone)
log("\n=== 5) Baseline-Flood (reines Flood-and-cache) ===")
seeds = list(range(MASTER_SEED, MASTER_SEED + N_SEEDS))


def eval_baseline(seeds, n_pairs, flood_max=FLOOD_MAX_BASE):
    air, deliv, hops, rel = [], [], [], []
    for si, seed in enumerate(seeds):
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503)
        for (s, d) in make_pairs(seed, n_pairs):
            ok, used, ntx = run_flood(s, d, rstate, flood_max)
            deliv.append(1.0 if ok else 0.0)
            if ok and used:
                air.append(ntx); hops.append(path_hops(used))
                rel.append(path_reliability(used))
    return {
        "delivery": float(np.mean(deliv)),
        "airtime_tx_mean": float(np.mean(air)) if air else 0.0,
        "data_toa_ms_mean": float(np.mean(air)) * DATA_TOA if air else 0.0,
        "hops_mean": float(np.mean(hops)) if hops else 0.0,
        "reliability_mean": float(np.mean(rel)) if rel else 0.0,
        "n": len(deliv),
    }


base = eval_baseline(seeds, N_PAIRS)
BASE_DELIV = base["delivery"]
BASE_AIR_TX = base["airtime_tx_mean"]
BASE_TOA = base["data_toa_ms_mean"]
log(f"  Baseline: delivery={BASE_DELIV:.3f}  airtime={BASE_AIR_TX:.1f} TX  "
    f"(= {BASE_TOA:.0f} ms ToA/Zustellung)  hops={base['hops_mean']:.2f}  "
    f"rel={base['reliability_mean']:.3f}")


def eval_backbone_scenario(bb_set, adoption, seeds, n_pairs, dv_period_s,
                           horizon_s, traffic_msgs_per_horizon, region_cap=None):
    """Bewerte ein Backbone-Szenario über mehrere Seeds.
    NUTZEN (DATA): für Paare, die über den Backbone routbar sind -> Unicast,
      sonst Fallback Flood. Daten-Airtime = gewichteter Mix.
    VERLUST (Kontroll): DV-Updates über horizon_s.
    NETTO = (Baseline-Daten-Airtime - Backbone-Daten-Airtime) * #Nachrichten
            - Kontroll-Airtime.
    """
    seed_rows = []
    for si, seed in enumerate(seeds):
        BBG, active_bb = build_backbone_graph(bb_set, adoption, seed)
        rstate = np.random.default_rng(seed * 2654435761 + si * 40503 + 991)
        data_tx, data_toa, deliv, hops, rel = [], [], [], [], []
        n_via_bb = 0; n_fallback = 0
        detour_vs_sp = []
        for (s, d) in make_pairs(seed, n_pairs):
            res = backbone_unicast(s, d, BBG, active_bb, rstate)
            if res is not None:
                n_via_bb += 1
                data_tx.append(res["tx_events"])
                data_toa.append(res["data_toa_ms"])
                deliv.append(1.0)  # Unicast über bekannten Pfad; Reliability separat
                hops.append(res["hops"])
                rel.append(res["reliability"])
                sp = shortest_hops(s, d)
                if sp and sp > 0:
                    detour_vs_sp.append(res["hops"] / sp)
            else:
                n_fallback += 1
                ok, used, ntx = run_flood(s, d, rstate, FLOOD_MAX_BASE)
                deliv.append(1.0 if ok else 0.0)
                if ok and used:
                    data_tx.append(ntx); data_toa.append(ntx * DATA_TOA)
                    hops.append(path_hops(used)); rel.append(path_reliability(used))
                    sp = shortest_hops(s, d)
                    if sp and sp > 0:
                        detour_vs_sp.append(path_hops(used) / sp)
        # Kontroll-Kosten dieses Szenarios
        dv = dv_control_cost(active_bb, BBG, dv_period_s, horizon_s, region_cap=region_cap)
        # Daten-Airtime pro Nachricht (gemittelt über die Paar-Stichprobe)
        data_toa_per_msg = float(np.mean(data_toa)) if data_toa else BASE_TOA
        data_tx_per_msg = float(np.mean(data_tx)) if data_tx else BASE_AIR_TX
        # Über den Horizont: traffic_msgs Nachrichten
        total_data_toa = data_toa_per_msg * traffic_msgs_per_horizon
        base_total_data_toa = BASE_TOA * traffic_msgs_per_horizon
        data_saving_toa = base_total_data_toa - total_data_toa
        net_toa = data_saving_toa - dv["control_toa_ms"]
        seed_rows.append({
            "delivery": float(np.mean(deliv)) if deliv else 0.0,
            "frac_via_bb": n_via_bb / max(n_via_bb + n_fallback, 1),
            "data_toa_per_msg": data_toa_per_msg,
            "data_tx_per_msg": data_tx_per_msg,
            "data_saving_toa": data_saving_toa,
            "control_toa_ms": dv["control_toa_ms"],
            "net_toa": net_toa,
            "hops_mean": float(np.mean(hops)) if hops else 0.0,
            "rel_mean": float(np.mean(rel)) if rel else 0.0,
            "detour_mean": float(np.mean(detour_vs_sp)) if detour_vs_sp else 0.0,
            "avg_dv_entries": dv["avg_entries"],
            "n_active_bb": dv["n_active"],
            "base_total_data_toa": base_total_data_toa,
            "total_data_toa": total_data_toa,
        })

    def m(k):
        return float(np.mean([r[k] for r in seed_rows]))

    def sem(k):
        v = [r[k] for r in seed_rows]
        return float(np.std(v, ddof=1) / math.sqrt(len(v))) if len(v) >= 2 else 0.0

    return {
        "delivery": m("delivery"), "delivery_sem": sem("delivery"),
        "frac_via_bb": m("frac_via_bb"),
        "data_toa_per_msg": m("data_toa_per_msg"),
        "data_tx_per_msg": m("data_tx_per_msg"),
        "data_saving_toa": m("data_saving_toa"),
        "control_toa_ms": m("control_toa_ms"),
        "net_toa": m("net_toa"), "net_toa_sem": sem("net_toa"),
        "hops_mean": m("hops_mean"), "rel_mean": m("rel_mean"),
        "detour_mean": m("detour_mean"),
        "avg_dv_entries": m("avg_dv_entries"),
        "n_active_bb": m("n_active_bb"),
        "base_total_data_toa": m("base_total_data_toa"),
        "total_data_toa": m("total_data_toa"),
    }


# Traffic-Annahme über Horizont: wie viele Discovery/Nachrichten-Aufbauten in 24h.
# Wir parametrisieren das, weil NETTO direkt davon abhängt (mehr DATA-Traffic ->
# mehr Flood-Ersparnis amortisiert die fixen Kontroll-Kosten).
HORIZON_S = 24 * 3600.0
# Realdaten-Anker: relay_count_24h Top ~2900/24h. Netzweit Discovery-Floods sind seltener.
# Wir nehmen einen mittleren Wert und sweepen ihn separat.
TRAFFIC_MSGS = int(os.environ.get("BB_TRAFFIC", "2000"))  # Discovery-Floods / 24h netzweit


# ---- 5a) Backbone-Größen-Sweep ----
log("\n=== 5a) Backbone-Größen-Sweep (DV-Periode 300s, Adoption 100%) ===")
log(f"  Traffic-Annahme: {TRAFFIC_MSGS} netzweite Discovery-Floods / 24h")
BB_SIZES = [0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 1.0]
DV_PERIOD_DEFAULT = 300.0
size_sweep = []
for sz in BB_SIZES:
    bb = select_backbone(sz)
    r = eval_backbone_scenario(bb, 1.0, seeds, N_PAIRS, DV_PERIOD_DEFAULT,
                               HORIZON_S, TRAFFIC_MSGS)
    r["bb_size_frac"] = sz
    r["bb_size_nodes"] = len(bb)
    size_sweep.append(r)
    net_kor = "NETTO+" if r["net_toa"] > 0 else "NETTO-"
    log(f"  bb={sz*100:5.1f}% ({len(bb):3d} Knoten) via_bb={r['frac_via_bb']*100:4.0f}% "
        f"data_save={r['data_saving_toa']/1000:8.1f}s ctrl={r['control_toa_ms']/1000:8.1f}s "
        f"NET={r['net_toa']/1000:+9.1f}s [{net_kor}] deliv={r['delivery']:.3f}")


# ---- 5b) DV-Perioden-Sweep (bei bester/mittlerer Backbone-Größe) ----
log("\n=== 5b) DV-Perioden-Sweep (Backbone 35%, Adoption 100%) ===")
DV_PERIODS = [30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0]
bb_mid = select_backbone(0.35)
dv_sweep = []
for p in DV_PERIODS:
    r = eval_backbone_scenario(bb_mid, 1.0, seeds, N_PAIRS, p, HORIZON_S, TRAFFIC_MSGS)
    r["dv_period_s"] = p
    dv_sweep.append(r)
    net_kor = "NETTO+" if r["net_toa"] > 0 else "NETTO-"
    log(f"  DV-Periode={p:6.0f}s ctrl={r['control_toa_ms']/1000:9.1f}s "
        f"NET={r['net_toa']/1000:+9.1f}s [{net_kor}]")


# ---- 5c) Adoptions-Sweep (Mixed-Firmware) ----
log("\n=== 5c) Adoptions-Sweep (Backbone-Größe 35%, DV 300s) ===")
ADOPTIONS = [0.10, 0.25, 0.50, 0.75, 1.0]
adopt_sweep = []
for a in ADOPTIONS:
    r = eval_backbone_scenario(bb_mid, a, seeds, N_PAIRS, DV_PERIOD_DEFAULT,
                               HORIZON_S, TRAFFIC_MSGS)
    r["adoption"] = a
    adopt_sweep.append(r)
    net_kor = "NETTO+" if r["net_toa"] > 0 else "NETTO-"
    log(f"  Adoption={a*100:5.0f}% active_bb={r['n_active_bb']:.0f} "
        f"via_bb={r['frac_via_bb']*100:4.0f}% data_save={r['data_saving_toa']/1000:8.1f}s "
        f"ctrl={r['control_toa_ms']/1000:7.1f}s NET={r['net_toa']/1000:+9.1f}s [{net_kor}] "
        f"deliv={r['delivery']:.3f}")


# ---- 5d) Traffic-Sensitivität (wann amortisiert sich der Backbone?) ----
log("\n=== 5d) Traffic-Sensitivität (Backbone 35%, DV 300s, Adoption 100%) ===")
TRAFFIC_LEVELS = [100, 250, 500, 1000, 2000, 4000, 8000]
traffic_sweep = []
for tm in TRAFFIC_LEVELS:
    r = eval_backbone_scenario(bb_mid, 1.0, seeds, N_PAIRS, DV_PERIOD_DEFAULT,
                               HORIZON_S, tm)
    r["traffic_msgs"] = tm
    traffic_sweep.append(r)
    net_kor = "NETTO+" if r["net_toa"] > 0 else "NETTO-"
    log(f"  Traffic={tm:5d} msg/24h data_save={r['data_saving_toa']/1000:8.1f}s "
        f"ctrl={r['control_toa_ms']/1000:7.1f}s NET={r['net_toa']/1000:+9.1f}s [{net_kor}]")


# ---- 5e) 2D-Grid: Backbone-Größe x DV-Periode (NETTO-Fenster) ----
log("\n=== 5e) 2D-Grid Backbone-Größe x DV-Periode (NETTO-Fenster) ===")
grid_sizes = [0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 1.0]
grid_periods = [60.0, 120.0, 300.0, 600.0, 1800.0]
grid = []
for sz in grid_sizes:
    bb = select_backbone(sz)
    row = []
    for p in grid_periods:
        r = eval_backbone_scenario(bb, 1.0, seeds, N_PAIRS, p, HORIZON_S, TRAFFIC_MSGS)
        row.append(r["net_toa"])
    grid.append(row)
    log(f"  bb={sz*100:5.1f}%: NET(s) over DV-periods = " +
        " ".join(f"{v/1000:+8.1f}" for v in row))


# ---- 5f) Flaches DV vs. Regions-Hierarchie (H1) — Skalierungs-Effekt ----
# Das flache DV (alle Sweeps oben) ist die EHRLICHE pessimistische Annahme: jeder
# Backbone-Knoten trägt alle Ziele -> O(N)-Kontroll-Last. Das Design (H1) deckelt das
# auf Regionsgröße. Hier zeigen wir, ob die Hierarchie das NETTO-Bild bei GROSSEM
# Backbone rettet (wo flaches DV klar kippt).
log("\n=== 5f) Flaches DV vs. Regions-Hierarchie (H1) bei großem Backbone ===")
REGION_CAP = int(os.environ.get("BB_REGION_CAP", "20"))   # ~Repeater je Region (real: bonn/koeln)
hier_compare = []
for sz in [0.35, 0.50, 0.70, 1.0]:
    bb = select_backbone(sz)
    flat = eval_backbone_scenario(bb, 1.0, seeds, N_PAIRS, DV_PERIOD_DEFAULT,
                                  HORIZON_S, TRAFFIC_MSGS, region_cap=None)
    hier = eval_backbone_scenario(bb, 1.0, seeds, N_PAIRS, DV_PERIOD_DEFAULT,
                                  HORIZON_S, TRAFFIC_MSGS, region_cap=REGION_CAP)
    hier_compare.append({
        "bb_size_frac": sz, "bb_nodes": len(bb),
        "flat_ctrl_s": flat["control_toa_ms"] / 1000, "flat_net_s": flat["net_toa"] / 1000,
        "flat_entries": flat["avg_dv_entries"],
        "hier_ctrl_s": hier["control_toa_ms"] / 1000, "hier_net_s": hier["net_toa"] / 1000,
        "hier_entries": hier["avg_dv_entries"],
        "data_save_s": flat["data_saving_toa"] / 1000,
    })
    log(f"  bb={sz*100:5.1f}%: FLACH ctrl={flat['control_toa_ms']/1000:9.1f}s "
        f"NET={flat['net_toa']/1000:+9.1f}s ({flat['avg_dv_entries']:.0f} Einträge)  ||  "
        f"H1 ctrl={hier['control_toa_ms']/1000:8.1f}s NET={hier['net_toa']/1000:+9.1f}s "
        f"({hier['avg_dv_entries']:.0f} Einträge)")


# ======================================================================================
# 6) ERGEBNISSE SCHREIBEN (früh/inkrementell)
# ======================================================================================
log("\n=== 6) Ergebnisse schreiben ===")
results = {
    "config": {
        "seed": MASTER_SEED, "n_seeds": N_SEEDS, "n_pairs": N_PAIRS, "fast": FAST,
        "lora": {"SF": LORA_SF, "BW_hz": LORA_BW, "data_pkt_bytes": DATA_PKT_BYTES,
                 "data_toa_ms": DATA_TOA, "dv_header_bytes": DV_HEADER_BYTES,
                 "dv_entry_bytes": DV_ENTRY_BYTES},
        "horizon_s": HORIZON_S, "traffic_msgs_default": TRAFFIC_MSGS,
        "dv_period_default_s": DV_PERIOD_DEFAULT,
    },
    "topology": {
        "core_nodes": G.number_of_nodes(), "core_edges": G.number_of_edges(),
        "giant_nodes": GC.number_of_nodes(), "giant_edges": GC.number_of_edges(),
        "avg_degree": 2 * GC.number_of_edges() / GC.number_of_nodes(),
        "backbone_candidates": len(backbone_candidates),
    },
    "baseline": base,
    "size_sweep": size_sweep,
    "dv_period_sweep": dv_sweep,
    "adoption_sweep": adopt_sweep,
    "traffic_sweep": traffic_sweep,
    "grid_net_toa": {"sizes": grid_sizes, "periods": grid_periods, "net_toa_ms": grid},
    "dv_mode_note": "Alle Sweeps oben nutzen FLACHES DV (region_cap=None, pessimistisch). "
                    "hierarchy_compare zeigt die H1-Regions-Variante.",
    "hierarchy_compare": {"region_cap": REGION_CAP, "rows": hier_compare},
}
json.dump(results, open(os.path.join(HERE, "backbone_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("  backbone_results.json geschrieben.")


# ======================================================================================
# 7) PLOTS
# ======================================================================================
plt.rcParams.update({"font.size": 9, "figure.dpi": 130})

# fig_bb_net_airtime — Daten/Kontroll/Netto über Backbone-Größe
fig, ax = plt.subplots(figsize=(8.6, 5.4))
xs = [r["bb_size_frac"] * 100 for r in size_sweep]
data_save = [r["data_saving_toa"] / 1000 for r in size_sweep]
ctrl = [-r["control_toa_ms"] / 1000 for r in size_sweep]   # als Verlust (negativ)
net = [r["net_toa"] / 1000 for r in size_sweep]
ax.bar([x - 1.2 for x in xs], data_save, width=2.4, color="#2e7d32",
       label="Daten-Ersparnis (Flood→Unicast)", alpha=0.85)
ax.bar([x + 1.2 for x in xs], ctrl, width=2.4, color="#c0392b",
       label="Kontroll-Kosten (DV-Updates)", alpha=0.85)
ax.plot(xs, net, "o-", color="#1f3a5f", lw=2, label="NETTO-Airtime")
ax.axhline(0, color="k", lw=0.8)
ax.set_xlabel("Backbone-Größe (% der backbone-fähigen Knoten)")
ax.set_ylabel(f"Airtime über 24h (s)  |  {TRAFFIC_MSGS} Discovery-Floods, DV {DV_PERIOD_DEFAULT:.0f}s")
ax.set_title("Phase-2-Backbone: Daten-Ersparnis vs. Kontroll-Kosten vs. NETTO\n"
             f"(reale Topologie, {GC.number_of_nodes()} Knoten, SF{LORA_SF}/BW{int(LORA_BW/1000)}k)")
ax.legend(); ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_bb_net_airtime.png")); plt.close(fig)

# fig_bb_adoption
fig, ax = plt.subplots(figsize=(8.0, 5.0))
xa = [r["adoption"] * 100 for r in adopt_sweep]
ax.plot(xa, [r["data_saving_toa"] / 1000 for r in adopt_sweep], "o-",
        color="#2e7d32", label="Daten-Ersparnis")
ax.plot(xa, [r["control_toa_ms"] / 1000 for r in adopt_sweep], "s--",
        color="#c0392b", label="Kontroll-Kosten")
ax.plot(xa, [r["net_toa"] / 1000 for r in adopt_sweep], "D-",
        color="#1f3a5f", lw=2, label="NETTO")
ax.axhline(0, color="k", lw=0.8)
ax2 = ax.twinx()
ax2.plot(xa, [r["frac_via_bb"] * 100 for r in adopt_sweep], "^:", color="#8e44ad",
         label="% Paare über Backbone")
ax2.set_ylabel("% Paare über Backbone geroutet", color="#8e44ad")
ax2.tick_params(axis="y", labelcolor="#8e44ad")
ax.set_xlabel("Backbone-Firmware-Adoption (%)")
ax.set_ylabel("Airtime über 24h (s)")
ax.set_title("Mixed-Firmware: NETTO-Airtime & Backbone-Abdeckung über Adoption\n"
             f"(Backbone-Größe 35%, DV {DV_PERIOD_DEFAULT:.0f}s)")
lines = ax.get_lines() + ax2.get_lines()
ax.legend(lines, [l.get_label() for l in lines], loc="center right", fontsize=8)
ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_bb_adoption.png")); plt.close(fig)

# fig_bb_dv_period
fig, ax = plt.subplots(figsize=(8.0, 5.0))
xp = [r["dv_period_s"] for r in dv_sweep]
ax.plot(xp, [r["control_toa_ms"] / 1000 for r in dv_sweep], "s--",
        color="#c0392b", label="Kontroll-Kosten")
ax.plot(xp, [r["data_saving_toa"] / 1000 for r in dv_sweep], "o-",
        color="#2e7d32", label="Daten-Ersparnis (const.)")
ax.plot(xp, [r["net_toa"] / 1000 for r in dv_sweep], "D-",
        color="#1f3a5f", lw=2, label="NETTO")
ax.axhline(0, color="k", lw=0.8)
ax.set_xscale("log")
ax.set_xlabel("DV-Update-Periode (s, log)")
ax.set_ylabel("Airtime über 24h (s)")
ax.set_title("DV-Perioden-Sweep: ab welcher Frequenz frisst Kontroll-Traffic den Nutzen?\n"
             f"(Backbone-Größe 35%, {TRAFFIC_MSGS} Floods/24h)")
ax.legend(); ax.grid(alpha=0.25, which="both")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_bb_dv_period.png")); plt.close(fig)

# fig_bb_traffic_sensitivity (Bonus: Amortisation)
fig, ax = plt.subplots(figsize=(8.0, 5.0))
xt = [r["traffic_msgs"] for r in traffic_sweep]
ax.plot(xt, [r["data_saving_toa"] / 1000 for r in traffic_sweep], "o-",
        color="#2e7d32", label="Daten-Ersparnis")
ax.plot(xt, [r["control_toa_ms"] / 1000 for r in traffic_sweep], "s--",
        color="#c0392b", label="Kontroll-Kosten (fix)")
ax.plot(xt, [r["net_toa"] / 1000 for r in traffic_sweep], "D-",
        color="#1f3a5f", lw=2, label="NETTO")
ax.axhline(0, color="k", lw=0.8)
ax.set_xscale("log")
ax.set_xlabel("Netzweite Discovery-Floods / 24h (log)")
ax.set_ylabel("Airtime über 24h (s)")
ax.set_title("Traffic-Sensitivität: ab welchem Daten-Aufkommen amortisiert der Backbone?\n"
             f"(Backbone-Größe 35%, DV {DV_PERIOD_DEFAULT:.0f}s)")
ax.legend(); ax.grid(alpha=0.25, which="both")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_bb_traffic.png")); plt.close(fig)

# fig_bb_grid (2D NETTO-Fenster)
fig, ax = plt.subplots(figsize=(8.0, 5.4))
arr = np.array(grid) / 1000.0
vmax = np.max(np.abs(arr))
im = ax.imshow(arr, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
ax.set_xticks(range(len(grid_periods)))
ax.set_xticklabels([f"{int(p)}" for p in grid_periods])
ax.set_yticks(range(len(grid_sizes)))
ax.set_yticklabels([f"{int(s*100)}%" for s in grid_sizes])
ax.set_xlabel("DV-Periode (s)"); ax.set_ylabel("Backbone-Größe")
for i in range(len(grid_sizes)):
    for j in range(len(grid_periods)):
        v = arr[i, j]
        ax.text(j, i, f"{v:+.0f}", ha="center", va="center", fontsize=7,
                color="black")
fig.colorbar(im, ax=ax, label="NETTO-Airtime über 24h (s)")
ax.set_title("NETTO-Fenster: Backbone-Größe x DV-Periode\n"
             f"grün = NETTO-Gewinn, rot = Kontroll-Traffic frisst Nutzen "
             f"({TRAFFIC_MSGS} Floods/24h)")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "fig_bb_grid.png")); plt.close(fig)

log("  Plots: fig_bb_net_airtime.png, fig_bb_adoption.png, fig_bb_dv_period.png, "
    "fig_bb_traffic.png, fig_bb_grid.png")


# ======================================================================================
# 8) KENNZAHLEN-DUMP für Bericht
# ======================================================================================
# bestes Fenster finden
best_size = max(size_sweep, key=lambda r: r["net_toa"])
best_grid_val = None; best_grid_pos = None
for i, sz in enumerate(grid_sizes):
    for j, p in enumerate(grid_periods):
        v = grid[i][j]
        if best_grid_val is None or v > best_grid_val:
            best_grid_val = v; best_grid_pos = (sz, p)
# Traffic-Break-even (kleinste Traffic-Stufe mit NETTO>0 bei 35%/300s)
breakeven_traffic = None
for r in traffic_sweep:
    if r["net_toa"] > 0:
        breakeven_traffic = r["traffic_msgs"]; break

summary = {
    "base_deliv": round(BASE_DELIV, 3),
    "base_air_tx": round(BASE_AIR_TX, 1),
    "base_toa_ms": round(BASE_TOA, 1),
    "data_toa_per_pkt_ms": round(DATA_TOA, 1),
    "best_size_frac": best_size["bb_size_frac"],
    "best_size_net_s": round(best_size["net_toa"] / 1000, 1),
    "best_size_via_bb_pct": round(best_size["frac_via_bb"] * 100, 1),
    "best_grid_size": best_grid_pos[0], "best_grid_period_s": best_grid_pos[1],
    "best_grid_net_s": round(best_grid_val / 1000, 1),
    "breakeven_traffic_msgs_24h": breakeven_traffic,
    "any_net_positive": bool(any(r["net_toa"] > 0 for r in size_sweep)),
}
log("\n=== KENNZAHLEN-DUMP (für Bericht) ===")
log(json.dumps(summary, ensure_ascii=False, indent=2))
results["summary"] = summary
json.dump(results, open(os.path.join(HERE, "backbone_results.json"), "w"),
          indent=2, ensure_ascii=False)
log("\n=== FERTIG ===")
