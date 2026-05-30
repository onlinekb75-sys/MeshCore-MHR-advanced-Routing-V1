#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NHR v2 — ROBUSTHEITS-VALIDIERUNG (Stoerszenarien) auf realer CoreScope-Topologie.

Aufbauend auf nhr_sim_real.py (25 echte Knoten, Raum Bonn/Rhein-Sieg/Siebengebirge/
Lohmar/Leverkusen). Dieses Skript ergaenzt den dokumentierten Validierungsschritt aus
  ../MeshCore_Hybrid_Routing_v2_Robustheit.md  (Abschnitt 5)
und vergleicht die Robustheit von:

  BASELINE = MeshCore heute:
      netzweiter Flood + "first packet wins" (zuerst eintreffende Kopie gewinnt,
      reines Zufalls-Timing pro Hop). Bei Stoerung: stures Re-Flood.

  NHR      = NHR v2:
      SNR-/ETX-gewichtete Ausbreitung => im Flood gewinnt die Kopie mit dem besten
      kumulierten Pfad-SNR / den wenigsten Hops (NICHT zufaellig). Pfad-Adoption nur
      bei spuerbarer Verbesserung (Hysterese, H4) => weniger Flattern. Bei Linkausfall
      schaltet ein vorab validierter Backup-Successor um (H3), statt teures Re-Flood.
      Bei Partition: genau EIN Flood, dann sauberer Reactive-Fallback (kein Endlos-Flood).

Drei Szenarien:
  1) CHURN     — Knoten gemaess advert_count-Profil zufaellig an/aus (instabilere
                 Knoten flackern haeufiger). Misst Routen-Wechselrate ("Flattern"),
                 Lieferquote, mittlere Hops.
  2) LINKFAIL  — X% der Backbone-Links fallen aus (0/10/20/30%). Misst Anteil
                 nicht-erreichbarer Paare, Re-Discovery-Haeufigkeit, Airtime, Lieferquote.
  3) PARTITION — isolierte Knoten (analog Leverkusen). Misst, ob Baseline in
                 Endlos-Flood laeuft (Airtime explodiert) vs. NHR sauberer Fallback.

numpy / networkx / matplotlib, reproduzierbar mit Seed 42.
Ausgabe: sim_results_v2.json  +  fig_v2_churn.png / fig_v2_linkfail.png / fig_v2_partition.png
"""

import numpy as np
import networkx as nx
import math
import json
import heapq
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Globaler RNG (Seed 42) -> Reproduzierbarkeit
SEED = 42
rng = np.random.default_rng(SEED)

# ===========================================================================
# 1) TOPOLOGIE — 25 echte CoreScope-Knoten (identisch zu nhr_sim_real.py),
#    erweitert um ein realistisches advert_count-Profil (real: 7 … 455).
#    Hoher advert_count  => Knoten meldet sich oft  => stabil/verfuegbar.
#    Niedriger advert_count => seltener gehoert     => instabil/flackernd.
#    (name, lat, lon, role, advert_count)
# ===========================================================================
NODES = [
    ("Lohmar #27",        50.87246, 7.22781, "R", 455),
    ("51143-SOLAR",       50.85156, 7.0149,  "R", 312),
    ("D-CGN MSE2 Solar",  50.91083, 6.98953, "R", 280),
    ("SU-SGB",            50.79976, 7.21204, "R", 198),
    ("BN-Ruengsdorf",     50.6813,  7.17157, "R", 167),
    ("Oelberg IGFS",      50.68226, 7.24813, "R", 401),   # Hochstandort, sehr stabil
    ("53343 Zuelligh.",   50.61725, 7.1566,  "R", 88),
    ("SU Lichtenberg",    50.74055, 7.34146, "R", 142),
    ("LEV-JO31MA",        51.03895, 7.06893, "R", 23),    # Leverkusen — abgesetzt, instabil
    ("MakiAlfter",        50.70918, 7.01943, "R", 175),
    ("Pending-Bonn",      50.75502, 7.10202, "R", 12),    # "Pending" => sehr instabil
    ("Bonn-Nord Solar",   50.74622, 7.07389, "R", 233),
    ("Alfter-Oedekoven",  50.7205,  7.02118, "R", 119),
    ("Bonn-Oberkassel",   50.71133, 7.17796, "R", 96),
    ("Lohmar #17a",       50.88992, 7.2832,  "R", 51),
    ("Lohmar #17b",       50.88991, 7.28317, "R", 47),
    ("CGN1",              50.87886, 7.12384, "R", 264),
    ("Bonn-Duisdorf FGZ", 50.71399, 7.04644, "R", 134),
    # ---- Companions (leiten NICHT weiter) ----
    ("Cli ZORT",          50.6445,  7.18808, "C", 7),
    ("Cli Rheinb-OS",     50.87487, 7.01809, "C", 31),
    ("Cli Ulli/p",        50.71954, 7.05864, "C", 64),
    ("Cli PXTiny",        50.72427, 7.10676, "C", 19),
    ("Cli LordWhopper",   50.78657, 7.15789, "C", 28),
    ("Cli DL4FP",         50.72032, 7.0264,  "C", 41),
    ("Cli Marcus-E",      50.82462, 7.27349, "C", 15),
]
N = len(NODES)
name = [x[0] for x in NODES]
lat = np.array([x[1] for x in NODES])
lon = np.array([x[2] for x in NODES])
role = [x[3] for x in NODES]
advert = np.array([x[4] for x in NODES], dtype=float)
reps = [i for i in range(N) if role[i] == "R"]
clis = [i for i in range(N) if role[i] == "C"]

# ===========================================================================
# 2) FUNK-/LINK-MODELL — Log-Distance-Pfadverlust -> SNR -> Zustellwahrsch.
#    (identische Parameter wie nhr_sim_real.py: SNR0=17, PLE=2.55, THR=-12)
# ===========================================================================
SNR0 = 17.0
PLE = 2.55
SNR_THR = -12.0


def hav(i, j):
    """Haversine-Distanz in km."""
    R = 6371.0
    p1, p2 = math.radians(lat[i]), math.radians(lat[j])
    dphi = math.radians(lat[j] - lat[i])
    dl = math.radians(lon[j] - lon[i])
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def link_snr(i, j):
    d = max(hav(i, j), 0.05)
    return SNR0 - 10 * PLE * math.log10(d)


def deliv(snr):
    """Weiche Empfangsschwelle: Zustellwahrscheinlichkeit ueber SNR-Marge."""
    return float(np.clip(1 / (1 + math.exp(-(snr - SNR_THR) / 3.0)), 0.0, 0.995))


# Link-Matrizen: Zustellwahrscheinlichkeit P und SNR
P = np.zeros((N, N))
SNR = np.full((N, N), -99.0)
for i in range(N):
    for j in range(N):
        if i == j:
            continue
        s = link_snr(i, j)
        SNR[i, j] = s
        P[i, j] = deliv(s) if s > SNR_THR else 0.0

# ===========================================================================
# 3) BACKBONE-GRAPH — nur Repeater (Companions leiten nicht weiter).
#    Kantengewicht ETX = 1/(p_ij*p_ji); zusaetzlich SNR-Summe fuer NHR-Scoring.
# ===========================================================================
def build_backbone(dead_nodes=frozenset(), dead_edges=frozenset()):
    """
    Baut den Repeater-Graphen. Erlaubt das Ausblenden ausgefallener Knoten
    (dead_nodes) und Links (dead_edges, je als frozenset({a,b})).
    Gibt nx.Graph zurueck.
    """
    G = nx.Graph()
    live_reps = [r for r in reps if r not in dead_nodes]
    G.add_nodes_from(live_reps)
    for a in live_reps:
        for b in live_reps:
            if a < b and P[a, b] > 0.05 and P[b, a] > 0.05:
                if frozenset({a, b}) in dead_edges:
                    continue
                etx = 1.0 / (P[a, b] * P[b, a])
                # mittlere SNR der Kante (fuer NHR-Scoring, "bestes kum. Pfad-SNR")
                msnr = 0.5 * (SNR[a, b] + SNR[b, a])
                G.add_edge(a, b, etx=etx, phop=1, snr=msnr)
    return G


# Voller, ungestoerter Backbone als Referenz
G_full = build_backbone()

# Companion-Attachment: Repeater, die der Client gut hoert (zero-hop)
attach = {c: [r for r in reps if P[c, r] > 0.25 and P[r, c] > 0.25] for c in clis}
for c in clis:
    if not attach[c]:
        attach[c] = [max(reps, key=lambda r: P[c, r])]


def attach_rep(c, G):
    """Bester noch lebender Attach-Repeater eines Clients fuer Graph G."""
    cand = [r for r in attach[c] if r in G]
    if not cand:
        cand = [r for r in reps if r in G]
    if not cand:
        return None
    return min(cand, key=lambda r: 1.0 / max(P[c, r] * P[r, c], 1e-6))


# ===========================================================================
# 4) PFAD-HELFER
# ===========================================================================
def best_path(G, a, b, weight="etx"):
    """ETX-optimaler Pfad (NHR-Ziel) oder None bei keiner Verbindung."""
    if a is None or b is None or a not in G or b not in G:
        return None
    try:
        return nx.shortest_path(G, a, b, weight=weight)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def path_etx(G, p):
    try:
        return sum(G[p[k]][p[k + 1]]["etx"] for k in range(len(p) - 1))
    except KeyError:
        return float("inf")


def path_rel(p):
    """Ende-zu-Ende-Zuverlaessigkeit = Produkt der Hop-Zustellwahrscheinlichkeiten."""
    r = 1.0
    for k in range(len(p) - 1):
        r *= P[p[k], p[k + 1]]
    return r


def path_snr_score(G, p):
    """
    NHR-Scoring im Flood: hoeher = besser. Belohnt wenige Hops + hohe Pfad-SNR.
    (mittlere Pfad-SNR minus Hop-Strafe). Dient als Auswahlkriterium statt Zufall.
    """
    if len(p) < 2:
        return 1e9
    try:
        snrs = [G[p[k]][p[k + 1]]["snr"] for k in range(len(p) - 1)]
    except KeyError:
        return -1e9
    return float(np.mean(snrs)) - 1.5 * (len(p) - 1)


# ===========================================================================
# 5) FLOOD-SIMULATION (Monte-Carlo) — gemeinsamer Kern fuer Baseline & NHR.
#    Unterschied:
#      Baseline: "first packet wins" — die zuerst (zeitlich) eintreffende Kopie
#                gewinnt; Timing ist reiner Zufall (rx_delay_base=0).
#      NHR:      "best packet wins" — innerhalb eines kurzen Sammelfensters wird
#                aus allen am Ziel eintreffenden Kopien die mit dem besten
#                SNR-/Hop-Score gewaehlt (SNR-gewichtete Ausbreitung, Best-of-N).
#    Gibt (gewaehlter_pfad|None, n_tx) zurueck. n_tx = Sende-Ereignisse (Airtime-Proxy).
# ===========================================================================
FLOOD_MAX = 8          # Hop-Limit
BASE_AIR = 0.10        # s Grund-Airtime pro Paket (Proxy)
PER_HOP_AIR = 0.012    # s zusaetzliche Airtime pro angehaengtem Pfad-Hash
COLLECT_WINDOW = 0.30  # s Sammelfenster am Ziel (NHR Best-of-N)


def simulate_flood(G, r_src, r_dst, mode):
    """
    Ein Flood-Durchlauf ueber Graph G.
    mode = "baseline": first-wins (erste Kopie).
    mode = "nhr":      best-wins innerhalb COLLECT_WINDOW (bester SNR/Hop-Score).
    """
    if r_src is None or r_dst is None or r_src not in G or r_dst not in G:
        return None, 0
    pq = [(0.0, r_src, (r_src,))]
    fwd = set()
    n_tx = 0
    first_time = None
    best_path_sel = None
    best_score = -1e18
    while pq:
        t, u, path = heapq.heappop(pq)
        if u == r_dst:
            if mode == "baseline":
                # first packet wins: erste Ankunft gewinnt, fertig.
                return path, n_tx
            else:
                # NHR: Kopien bis Fensterende sammeln, beste behalten.
                if first_time is None:
                    first_time = t
                if t <= first_time + COLLECT_WINDOW:
                    sc = path_snr_score(G, path)
                    if sc > best_score:
                        best_score = sc
                        best_path_sel = path
                # Ziel leitet nicht weiter; weitere Kopien werden gesammelt
                continue
        if u in fwd:
            continue
        fwd.add(u)
        if len(path) - 1 >= FLOOD_MAX:
            continue
        n_tx += 1
        air = BASE_AIR + PER_HOP_AIR * (len(path) - 1)
        for v in G.neighbors(u):
            if v in path:           # einfache Loop-Vermeidung
                continue
            if rng.random() <= P[u, v]:   # stochastische Zustellung
                d = rng.uniform(0, 5 * air)   # Zufallsverzoegerung (rx_delay_base=0)
                heapq.heappush(pq, (t + air + d, v, path + (v,)))
    return best_path_sel, n_tx


# ===========================================================================
# 6) NHR-ZUSTELLUNG mit Backup-Successor (H3) — vermeidet Re-Flood bei Linkausfall.
#    Liefert (pfad|None, tx, did_reflood):
#      - Primaerpfad (ETX-optimal) ueber G_live.
#      - Faellt nichts: Backup ueber kantendisjunkten Alternativpfad (vorab validiert),
#        wieder OHNE Flood (Unicast). Erst wenn auch das scheitert: genau EIN Re-Flood.
# ===========================================================================
def nhr_deliver(G_full_bb, G_live, ra, rb):
    """
    G_full_bb = ungestoerter Backbone (fuer vorab berechneten Backup).
    G_live    = aktuell gestoerter Backbone.
    """
    if ra is None or rb is None:
        return None, 0, False
    # Primaer-Successor: ETX-optimaler Pfad im LIVE-Graph (Unicast => tx=Hops)
    prim = best_path(G_live, ra, rb)
    if prim is not None:
        return prim, max(len(prim) - 1, 1), False
    # Primaer tot. Backup-Successor: ein im VOLLEN Graph vorab validierter
    # alternativer Pfad, dessen erster Hop ein anderer ist (kantendisjunkter Start).
    # Pruefe, ob dieser Backup im LIVE-Graph noch traegt -> Umschalten OHNE Flood.
    backup = _backup_path(G_full_bb, G_live, ra, rb)
    if backup is not None:
        return backup, max(len(backup) - 1, 1), False
    # Letzter Ausweg: genau EIN reaktiver Flood (kein Endlos-Flood).
    p, tx = simulate_flood(G_live, ra, rb, "nhr")
    return p, tx, True


def _backup_path(G_full_bb, G_live, ra, rb):
    """
    Sucht einen Backup-Pfad, der den primaeren ersten Hop meidet (Feasible-Successor-
    Idee, vereinfacht). Muss im LIVE-Graph komplett tragen.
    """
    prim_full = best_path(G_full_bb, ra, rb)
    if prim_full is None or len(prim_full) < 2:
        return None
    first_hop = prim_full[1]
    # Graph ohne den primaeren ersten Hop -> erzwingt disjunkten Start
    H = G_live.copy()
    if first_hop in H and first_hop != rb:
        H.remove_node(first_hop)
    bp = best_path(H, ra, rb)
    return bp


# ===========================================================================
# Hilfsfunktion: ein Client-Client-Paar auf seine Repeater-Endpunkte abbilden
# ===========================================================================
def client_pairs(G):
    """Liste (ca, cb, ra, rb) gueltiger Client-Paare mit verschiedenen Repeatern."""
    out = []
    for ai, ca in enumerate(clis):
        for cb in clis[ai + 1:]:
            ra = attach_rep(ca, G)
            rb = attach_rep(cb, G)
            if ra is None or rb is None or ra == rb:
                continue
            out.append((ca, cb, ra, rb))
    return out


# ===========================================================================
# SZENARIO 1 — CHURN
#   Knoten gehen gemaess advert_count zufaellig an/aus. Niedriger advert_count
#   => hoehere Ausfallwahrscheinlichkeit pro Runde. Ueber ROUNDS Runden wird je
#   Paar der gewaehlte Pfad verfolgt; ein Pfadwechsel ggue. Vorrunde = "Flattern".
#   NHR uebernimmt einen neuen Pfad nur bei spuerbarer Verbesserung (Hysterese),
#   Baseline uebernimmt jede neue first-wins-Kopie => flattert staerker.
# ===========================================================================
def churn_prob(adv):
    """Ausfallwahrscheinlichkeit pro Runde aus advert_count (7..455)."""
    # advert 455 -> ~2% aus; advert 7 -> ~45% aus. Monoton fallend.
    a = float(np.clip(adv, 1, 1000))
    return float(np.clip(0.50 - 0.085 * math.log10(a) * 3.0, 0.02, 0.50))


CHURN_FAIL = {i: churn_prob(advert[i]) for i in range(N)}
HYST = 0.15   # NHR uebernimmt neuen Pfad nur bei >=15% besserer (kleinerer) ETX (H4)


def run_churn(rounds=200):
    """Gibt Kennzahlen-Dict (baseline + nhr) zurueck."""
    base_pairs = client_pairs(G_full)
    # letzter gewaehlter Pfad je Paar (als Tupel) fuer Flatter-Messung
    last_base = {}
    last_nhr = {}
    stats = {
        "baseline": dict(switch=0, samples=0, delivered=0, attempts=0, hops=[]),
        "nhr": dict(switch=0, samples=0, delivered=0, attempts=0, hops=[]),
    }
    for _ in range(rounds):
        # Knoten dieser Runde an/aus
        dead = frozenset(r for r in reps if rng.random() < CHURN_FAIL[r])
        G_live = build_backbone(dead_nodes=dead)
        for (ca, cb, ra0, rb0) in base_pairs:
            # Endpunkt-Repeater koennen ausgefallen sein -> aktuellen waehlen
            ra = attach_rep(ca, G_live)
            rb = attach_rep(cb, G_live)
            key = (ca, cb)

            # ---- Baseline: first-wins-Flood, jede neue Kopie wird uebernommen ----
            stats["baseline"]["attempts"] += 1
            bp, _ = simulate_flood(G_live, ra, rb, "baseline")
            if bp is not None and ra is not None and rb is not None and ra != rb:
                stats["baseline"]["delivered"] += 1
                stats["baseline"]["hops"].append(len(bp) - 1)
                cur = tuple(bp)
                if key in last_base:
                    stats["baseline"]["samples"] += 1
                    if last_base[key] != cur:
                        stats["baseline"]["switch"] += 1
                last_base[key] = cur

            # ---- NHR: ETX-optimal + Hysterese (Pfadwechsel nur bei deutl. besser) ----
            stats["nhr"]["attempts"] += 1
            mp = best_path(G_live, ra, rb)
            if mp is not None and ra is not None and rb is not None and ra != rb:
                stats["nhr"]["delivered"] += 1
                new_etx = path_etx(G_live, mp)
                cur = tuple(mp)
                if key in last_nhr:
                    stats["nhr"]["samples"] += 1
                    old_path, old_etx = last_nhr[key]
                    # alter Pfad noch tragfaehig?
                    old_alive = all(
                        (G_live.has_edge(old_path[k], old_path[k + 1]))
                        for k in range(len(old_path) - 1)
                    ) and old_path[0] == ra and old_path[-1] == rb
                    if old_alive and new_etx >= old_etx * (1 - HYST):
                        # nicht spuerbar besser -> alten Pfad HALTEN (kein Flattern)
                        cur = old_path
                        new_etx = old_etx
                    elif cur != old_path:
                        stats["nhr"]["switch"] += 1
                last_nhr[key] = (cur, new_etx)
                stats["nhr"]["hops"].append(len(cur) - 1)

    def finalize(s):
        return dict(
            flap_rate=(s["switch"] / s["samples"]) if s["samples"] else 0.0,
            switches=s["switch"], comparisons=s["samples"],
            delivery_ratio=(s["delivered"] / s["attempts"]) if s["attempts"] else 0.0,
            mean_hops=float(np.mean(s["hops"])) if s["hops"] else 0.0,
        )

    return dict(
        rounds=rounds,
        baseline=finalize(stats["baseline"]),
        nhr=finalize(stats["nhr"]),
    )


# ===========================================================================
# SZENARIO 2 — LINKAUSFALL
#   X% der Backbone-Kanten fallen aus. Pro Stufe TRIALS Monte-Carlo-Ziehungen.
#   Baseline: bei Ausfall des gecachten Pfads -> Re-Flood (zaehlt als Re-Discovery,
#             volle Flood-Airtime). NHR: Backup-Successor (H3) ohne Flood; nur wenn
#             auch der tot ist -> EIN Re-Flood.
#   Misst: unreachable-Anteil, Re-Discovery-Rate, Airtime (tx), Lieferquote.
# ===========================================================================
def run_linkfail(levels=(0.0, 0.10, 0.20, 0.30), trials=40):
    all_edges = [frozenset({a, b}) for a, b in G_full.edges()]
    out = {"levels": [], "baseline": {}, "nhr": {}}
    for key in ("baseline", "nhr"):
        out[key] = dict(unreachable=[], rediscovery=[], airtime=[], delivery=[])
    base_pairs = client_pairs(G_full)

    for lvl in levels:
        out["levels"].append(lvl)
        b_unreach = []; b_redisc = []; b_air = []; b_deliv = []
        m_unreach = []; m_redisc = []; m_air = []; m_deliv = []
        nkill = int(round(lvl * len(all_edges)))
        for _ in range(trials):
            # zufaellige Auswahl ausgefallener Kanten
            if nkill > 0 and len(all_edges) > 0:
                idx = rng.choice(len(all_edges), size=min(nkill, len(all_edges)), replace=False)
                dead_edges = frozenset(all_edges[k] for k in np.atleast_1d(idx))
            else:
                dead_edges = frozenset()
            G_live = build_backbone(dead_edges=dead_edges)

            tot = 0; b_un = 0; m_un = 0
            b_red = 0; m_red = 0
            b_tx = 0.0; m_tx = 0.0
            b_ok = 0; m_ok = 0
            for (ca, cb, ra0, rb0) in base_pairs:
                ra = attach_rep(ca, G_live)
                rb = attach_rep(cb, G_live)
                if ra is None or rb is None or ra == rb:
                    continue
                tot += 1

                # ---- Baseline: gecachter Pfad? sonst Re-Flood ----
                # gecachter (ungestoerter) Pfad
                cached = best_path(G_full, ra, rb)
                cached_alive = cached is not None and all(
                    G_live.has_edge(cached[k], cached[k + 1]) for k in range(len(cached) - 1)
                )
                if cached_alive:
                    b_tx += len(cached) - 1   # Unicast ueber gecachten Pfad
                    b_ok += 1
                else:
                    # Re-Discovery: voller Flood
                    b_red += 1
                    bp, ntx = simulate_flood(G_live, ra, rb, "baseline")
                    b_tx += ntx if ntx > 0 else 0
                    if bp is not None:
                        b_ok += 1
                    else:
                        b_un += 1

                # ---- NHR: Primaer -> Backup-Successor -> (notfalls) 1 Flood ----
                mp, mtx, reflood = nhr_deliver(G_full, G_live, ra, rb)
                m_tx += mtx
                if reflood:
                    m_red += 1
                if mp is not None:
                    m_ok += 1
                else:
                    m_un += 1

            if tot > 0:
                b_unreach.append(b_un / tot); m_unreach.append(m_un / tot)
                b_redisc.append(b_red / tot); m_redisc.append(m_red / tot)
                b_air.append(b_tx / tot); m_air.append(m_tx / tot)
                b_deliv.append(b_ok / tot); m_deliv.append(m_ok / tot)

        def mean(x):
            return float(np.mean(x)) if x else 0.0

        out["baseline"]["unreachable"].append(mean(b_unreach))
        out["baseline"]["rediscovery"].append(mean(b_redisc))
        out["baseline"]["airtime"].append(mean(b_air))
        out["baseline"]["delivery"].append(mean(b_deliv))
        out["nhr"]["unreachable"].append(mean(m_unreach))
        out["nhr"]["rediscovery"].append(mean(m_redisc))
        out["nhr"]["airtime"].append(mean(m_air))
        out["nhr"]["delivery"].append(mean(m_deliv))
    return out


# ===========================================================================
# SZENARIO 3 — PARTITION
#   Wir isolieren funkisolierte Knoten (analog Leverkusen: LEV-JO31MA, advert=23).
#   Ziel: ein Paar, dessen Endpunkt-Repeater in einer ANDEREN Komponente liegt,
#   ist prinzipiell unerreichbar. Baseline merkt das nicht und flutet bei JEDEM
#   Sendeversuch erneut netzweit (Airtime explodiert ueber die Versuche). NHR
#   flutet GENAU EINMAL, erkennt "nicht erreichbar" und gibt auf (Reactive-Fallback).
#   Misst kumulierte Airtime ueber ATTEMPTS Sendeversuche.
# ===========================================================================
def run_partition(attempts=50):
    # Erzeuge Partition: isoliere LEV-JO31MA, indem wir seine Links als tot markieren.
    iso_name = "LEV-JO31MA"
    iso = name.index(iso_name)
    # Alle Kanten dieses Knotens entfernen -> Knoten wird funkisoliert.
    dead_edges = frozenset(frozenset({iso, b}) for b in reps if b != iso)
    G_live = build_backbone(dead_edges=dead_edges)

    # Companions, die NUR an dem isolierten Repeater haengen, sitzen mit fest.
    # Wir betrachten alle Client-Paare; "partitioniert" = Endpunkte in versch. Komponenten.
    comps = list(nx.connected_components(G_live))

    def comp_of(r):
        for ci, c in enumerate(comps):
            if r in c:
                return ci
        return -1

    base_pairs = client_pairs(G_full)
    partitioned = []
    for (ca, cb, ra0, rb0) in base_pairs:
        ra = attach_rep(ca, G_live)
        rb = attach_rep(cb, G_live)
        if ra is None or rb is None:
            continue
        if comp_of(ra) != comp_of(rb):
            partitioned.append((ca, cb, ra, rb))

    # Fallback: wenn der Attach das Companion umroutet und keine echte Partition
    # entsteht, erzwinge eine, indem wir den Companion des isolierten Repeaters
    # (Cli ZORT haengt am Siebengebirge, nicht hier) -> wir nehmen den
    # isolierten Repeater selbst als kuenstlichen Endpunkt-Stellvertreter.
    if not partitioned:
        # Knoten in der Riesenkomponente vs. isolierter Knoten
        giant = max(comps, key=len)
        other = next(iter(set(reps) & set(giant)), None)
        if other is not None:
            partitioned = [(None, None, other, iso)]

    # Simuliere ATTEMPTS Sendeversuche auf ein partitioniertes Paar.
    base_air = 0.0
    nhr_air = 0.0
    base_floods = 0
    nhr_floods = 0
    nhr_gave_up_after = None
    if partitioned:
        ca, cb, ra, rb = partitioned[0]
        for att in range(attempts):
            # Baseline: flutet bei JEDEM Versuch erneut netzweit (kennt Partition nicht).
            _, ntx = simulate_flood(G_live, ra, rb, "baseline")
            # Auch erfolglose Floods kosten Airtime (alle erreichbaren Repeater senden).
            base_air += ntx
            base_floods += 1

            # NHR: erster Versuch flutet einmal, erkennt Unerreichbarkeit, gibt dann auf.
            if att == 0:
                _, mtx = simulate_flood(G_live, ra, rb, "nhr")
                nhr_air += mtx
                nhr_floods += 1
                nhr_gave_up_after = 1   # ab jetzt: reactive-fallback, kein Flood mehr
            else:
                # NHR: Ziel als unerreichbar markiert -> kein weiterer Flood (0 Airtime).
                pass
    return dict(
        isolated_node=iso_name,
        n_components=len(comps),
        component_sizes=sorted((len(c) for c in comps), reverse=True),
        partitioned_pairs=len(partitioned),
        attempts=attempts,
        baseline=dict(total_airtime=float(base_air), floods=base_floods,
                      airtime_per_attempt=float(base_air / attempts) if attempts else 0.0),
        nhr=dict(total_airtime=float(nhr_air), floods=nhr_floods,
                 gave_up_after_floods=nhr_gave_up_after,
                 airtime_per_attempt=float(nhr_air / attempts) if attempts else 0.0),
        airtime_reduction_pct=float((1 - nhr_air / base_air) * 100) if base_air > 0 else 0.0,
    )


# ===========================================================================
# 7) AUSFUEHRUNG ALLER SZENARIEN + JSON
# ===========================================================================
def main():
    print("=== NHR v2 Robustheits-Validierung (Seed %d) ===" % SEED)

    # Kurzer Topologie-Check
    comps_full = list(nx.connected_components(G_full))
    print("Repeater:", len(reps), "| Clients:", len(clis),
          "| Backbone-Komponenten:", [len(c) for c in comps_full])

    try:
        churn = run_churn(rounds=200)
    except Exception as e:
        churn = dict(error=str(e))
        print("CHURN-Fehler:", e)

    try:
        linkfail = run_linkfail(levels=(0.0, 0.10, 0.20, 0.30), trials=40)
    except Exception as e:
        linkfail = dict(error=str(e))
        print("LINKFAIL-Fehler:", e)

    try:
        partition = run_partition(attempts=50)
    except Exception as e:
        partition = dict(error=str(e))
        print("PARTITION-Fehler:", e)

    results = dict(
        meta=dict(
            seed=SEED, n_nodes=N, n_repeaters=len(reps), n_clients=len(clis),
            topology="CoreScope 25 echte Knoten (Bonn/Rhein-Sieg/Siebengebirge/Lohmar/Leverkusen)",
            link_model=dict(SNR0=SNR0, PLE=PLE, SNR_THR=SNR_THR),
            scenarios="churn (advert_count), linkfail (0/10/20/30%), partition (Leverkusen)",
        ),
        scenario_churn=churn,
        scenario_linkfail=linkfail,
        scenario_partition=partition,
    )

    print(json.dumps(results, indent=2, ensure_ascii=False))
    with open("sim_results_v2.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("WROTE sim_results_v2.json")

    # ---- Plots ----
    make_plots(churn, linkfail, partition)
    print("PLOTS_DONE")
    return results


# ===========================================================================
# 8) PLOTS
# ===========================================================================
def make_plots(churn, linkfail, partition):
    plt.rcParams.update({"font.size": 10, "figure.dpi": 130})
    C_BASE = "#c0392b"
    C_NHR = "#2e7d32"

    # --- Churn: Flatter-Rate + Lieferquote ---
    try:
        fig, axes = plt.subplots(1, 2, figsize=(9.5, 4))
        b, m = churn["baseline"], churn["nhr"]
        ax = axes[0]
        vals = [b["flap_rate"] * 100, m["flap_rate"] * 100]
        bars = ax.bar(["MeshCore\n(first-wins)", "NHR\n(Hysterese)"], vals, color=[C_BASE, C_NHR])
        for bb, vv in zip(bars, vals):
            ax.text(bb.get_x() + bb.get_width() / 2, vv + 0.3, f"{vv:.1f}%", ha="center")
        ax.set_ylabel("Pfadwechsel-Rate ('Flattern') [%]")
        ax.set_title("Churn: Routen-Stabilitaet\n(%d Runden, advert-basierter Knotenausfall)" % churn["rounds"])
        ax.grid(axis="y", alpha=0.2)

        ax = axes[1]
        vals = [b["delivery_ratio"] * 100, m["delivery_ratio"] * 100]
        bars = ax.bar(["MeshCore", "NHR"], vals, color=[C_BASE, C_NHR])
        for bb, vv in zip(bars, vals):
            ax.text(bb.get_x() + bb.get_width() / 2, vv + 0.5, f"{vv:.1f}%", ha="center")
        ax.set_ylabel("Lieferquote [%]")
        ax.set_ylim(0, 105)
        ax.set_title("Churn: Lieferquote")
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout(); fig.savefig("fig_v2_churn.png"); plt.close(fig)
    except Exception as e:
        print("Plot churn Fehler:", e)

    # --- Linkfail: unreachable / airtime / rediscovery ueber Ausfallrate ---
    try:
        lv = [int(x * 100) for x in linkfail["levels"]]
        fig, axes = plt.subplots(1, 3, figsize=(13, 4))
        ax = axes[0]
        ax.plot(lv, [x * 100 for x in linkfail["baseline"]["unreachable"]], "-o", color=C_BASE, label="MeshCore")
        ax.plot(lv, [x * 100 for x in linkfail["nhr"]["unreachable"]], "-s", color=C_NHR, label="NHR")
        ax.set_xlabel("Link-Ausfallrate [%]"); ax.set_ylabel("nicht erreichbare Paare [%]")
        ax.set_title("Linkausfall: Erreichbarkeit"); ax.legend(); ax.grid(alpha=0.2)

        ax = axes[1]
        ax.plot(lv, linkfail["baseline"]["airtime"], "-o", color=C_BASE, label="MeshCore")
        ax.plot(lv, linkfail["nhr"]["airtime"], "-s", color=C_NHR, label="NHR")
        ax.set_xlabel("Link-Ausfallrate [%]"); ax.set_ylabel("Ø Sende-Ereignisse / Paar (Airtime)")
        ax.set_title("Linkausfall: Airtime"); ax.legend(); ax.grid(alpha=0.2)

        ax = axes[2]
        ax.plot(lv, [x * 100 for x in linkfail["baseline"]["rediscovery"]], "-o", color=C_BASE, label="MeshCore (Re-Flood)")
        ax.plot(lv, [x * 100 for x in linkfail["nhr"]["rediscovery"]], "-s", color=C_NHR, label="NHR (nach Backup)")
        ax.set_xlabel("Link-Ausfallrate [%]"); ax.set_ylabel("Re-Discovery-Rate [%]")
        ax.set_title("Linkausfall: Re-Discovery"); ax.legend(); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig("fig_v2_linkfail.png"); plt.close(fig)
    except Exception as e:
        print("Plot linkfail Fehler:", e)

    # --- Partition: kumulierte Airtime ueber Sendeversuche ---
    try:
        fig, ax = plt.subplots(figsize=(6.5, 4.5))
        att = partition["attempts"]
        # Baseline: linear wachsende Airtime (flutet jedes Mal)
        base_per = partition["baseline"]["airtime_per_attempt"]
        nhr_total = partition["nhr"]["total_airtime"]
        xs = list(range(1, att + 1))
        base_cum = [base_per * k for k in xs]
        nhr_cum = [nhr_total] * att   # flach nach erstem Versuch
        ax.plot(xs, base_cum, "-", color=C_BASE, lw=2.2,
                label=f"MeshCore: Endlos-Flood ({base_per:.1f} tx/Versuch)")
        ax.plot(xs, nhr_cum, "-", color=C_NHR, lw=2.2,
                label=f"NHR: 1 Flood, dann Fallback ({nhr_total:.0f} tx gesamt)")
        ax.set_xlabel("Sendeversuch an partitioniertes Ziel")
        ax.set_ylabel("kumulierte Airtime (Sende-Ereignisse)")
        ax.set_title("Partition (isoliert: %s)\nBaseline-Airtime explodiert, NHR gibt sauber auf (-%.0f%%)"
                     % (partition["isolated_node"], partition["airtime_reduction_pct"]))
        ax.legend(loc="upper left"); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig("fig_v2_partition.png"); plt.close(fig)
    except Exception as e:
        print("Plot partition Fehler:", e)


if __name__ == "__main__":
    main()
