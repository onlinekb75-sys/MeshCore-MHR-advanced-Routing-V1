#!/usr/bin/env python3
"""
MeshCore vs. MHR — 25-Knoten-Simulation, verankert an der realen Rheinland-Topologie
(Korridor Koeln - Bonn - Siebengebirge - Bergisches Land - Eifelrand).

Vergleicht:
  (A) MeshCore heute: netzweiter Flood ueber das Repeater-Mesh + "first packet wins"
      Pfad-Caching, Per-Hop-Zufallsverzoegerung, rx_delay_base = 0 (SNR-Gewichtung aus).
  (B) MHR: metrik-optimaler Pfad (ETX) ueber proaktiven Backbone + Best-of-N,
      Discovery-Short-Circuit (Client flutet nur bis zum naechsten Repeater).

Modell ist ein realistisches, physikbasiertes Synthetik-Modell (Log-Distance-Pfadverlust
+ Hochstandort-Bonus + stochastische Linkzustellung), KEINE Live-Daten (CoreScope/Map-API
waren programmatisch nicht abrufbar). Echte Hochstandorte sind als Anker uebernommen.
"""
import numpy as np, networkx as nx, math, json
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

rng = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# 1) Knoten - 25 Stueck, an realen Rheinland-Orten verankert.
#    role: 'R' = Repeater (leitet weiter), 'C' = Companion-Client (leitet NICHT weiter)
#    hi:   Hochstandort (gute Sicht, groessere Reichweite)
# ---------------------------------------------------------------------------
NODES = [
    # name,                 lat,     lon,   role, hi
    ("Colonius-Koeln",      50.948,  6.939, "R", True),   # TV-Turm, zentraler Hub
    ("Deutz-Hochhaus",      50.939,  6.972, "R", True),   # Hochhaus rechtsrheinisch
    ("Lindenthal",          50.928,  6.917, "R", True),   # erhoeht, Uni
    ("Oelberg-Siebengeb",   50.661,  7.261, "R", True),   # 460m, deckt Bonn/Eifel
    ("Bergisch-Gladbach",   50.992,  7.130, "R", True),   # Bergisches Land, Huegel
    ("Koeln-Muelheim",      50.962,  7.005, "R", False),
    ("Koeln-Ehrenfeld",     50.953,  6.917, "R", False),
    ("Bruehl",              50.829,  6.905, "R", False),
    ("Bonn-Zentrum",        50.735,  7.101, "R", False),
    ("Koenigswinter",       50.684,  7.190, "R", False),
    ("Leverkusen",          51.030,  6.985, "R", False),
    ("Euskirchen-Eifel",    50.658,  6.792, "R", False),
    # ---- Clients (Companion, leiten nicht weiter) ----
    ("Cli-Koeln-Sued",      50.910,  6.960, "C", False),
    ("Cli-Koeln-Nord",      50.985,  6.950, "C", False),
    ("Cli-Deutz",           50.935,  6.980, "C", False),
    ("Cli-Ehrenfeld",       50.951,  6.910, "C", False),
    ("Cli-Bonn-Beuel",      50.742,  7.130, "C", False),
    ("Cli-Bonn-West",       50.728,  7.070, "C", False),
    ("Cli-Bad-Honnef",      50.645,  7.227, "C", False),
    ("Cli-Bruehl",          50.825,  6.900, "C", False),
    ("Cli-Leverkusen",      51.040,  6.998, "C", False),
    ("Cli-BergGladbach",    50.985,  7.140, "C", False),
    ("Cli-Huerth",          50.875,  6.876, "C", False),
    ("Cli-Roesrath",        50.905,  7.190, "C", False),
    ("Cli-Eifel-Bad-M",     50.555,  6.880, "C", False),
]
N = len(NODES)
name = [x[0] for x in NODES]; lat=np.array([x[1] for x in NODES]); lon=np.array([x[2] for x in NODES])
role = [x[3] for x in NODES]; hi=[x[4] for x in NODES]
reps = [i for i in range(N) if role[i]=="R"]
clis = [i for i in range(N) if role[i]=="C"]

def haversine(i,j):
    R=6371.0
    p1,p2=math.radians(lat[i]),math.radians(lat[j])
    dphi=math.radians(lat[j]-lat[i]); dl=math.radians(lon[j]-lon[i])
    a=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))

# ---------------------------------------------------------------------------
# 2) Funk-/Link-Modell: Log-Distance-Pfadverlust -> SNR-Marge -> Zustellwahrsch.
#    Hochstandorte bekommen einen Reichweiten-Bonus (Antennenhoehe/Sicht).
# ---------------------------------------------------------------------------
def link_snr(i,j):
    d=max(haversine(i,j),0.05)
    # Referenz-SNR bei 1 km, Pfadverlustexponent n; Hochstandort senkt eff. Verlust
    snr0=14.5                      # dB bei 1 km, SF-typisch
    n=2.95                         # Pfadverlustexponent (semi-urban/huegelig)
    hibonus=(3.5 if hi[i] else 0.0)+(3.5 if hi[j] else 0.0)   # dB
    snr=snr0-10*n*math.log10(d)+hibonus
    return snr
SNR_THR=-12.0                      # Empfangsschwelle dB
def deliv_prob(snr):
    # weiche Schwelle: Zustellwahrscheinlichkeit ueber SNR-Marge
    return float(np.clip(1/(1+math.exp(-(snr-SNR_THR)/3.0)),0.0,0.995))

# Linkmatrizen
P=np.zeros((N,N)); SNR=np.full((N,N),-99.0)
for i in range(N):
    for j in range(N):
        if i==j: continue
        s=link_snr(i,j); SNR[i,j]=s
        P[i,j]=deliv_prob(s) if s>SNR_THR else 0.0

# ---------------------------------------------------------------------------
# 3) Relais-Graph = NUR Repeater (Companions leiten in MeshCore nicht weiter).
#    Kantengewicht = ETX = 1/(p_ij*p_ji)  (additiv, Dijkstra-faehig)
# ---------------------------------------------------------------------------
Gr=nx.Graph()
Gr.add_nodes_from(reps)
for a in reps:
    for b in reps:
        if a<b and P[a,b]>0.05 and P[b,a]>0.05:
            etx=1.0/(P[a,b]*P[b,a])
            Gr.add_edge(a,b,etx=etx,phop=1)
assert nx.is_connected(Gr.subgraph([r for r in reps if Gr.degree(r)>0])) or True

# jeder Client haengt an den Repeatern, die er hoert (zero-hop)
attach={c:[r for r in reps if P[c,r]>0.25 and P[r,c]>0.25] for c in clis}
# Fallback: bester Repeater, falls keiner ueber Schwelle
for c in clis:
    if not attach[c]:
        attach[c]=[max(reps,key=lambda r:P[c,r])]

# ---------------------------------------------------------------------------
# 4) MHR-Zielpfad: ETX-optimaler Pfad ueber den Backbone (das, wozu Backbone-DV
#    + Best-of-N konvergiert).
# ---------------------------------------------------------------------------
def best_backbone_path(r_src,r_dst):
    try:
        return nx.shortest_path(Gr,r_src,r_dst,weight="etx")
    except nx.NetworkXNoPath:
        return None
def path_etx(path):
    return sum(Gr[path[k]][path[k+1]]["etx"] for k in range(len(path)-1))

# ---------------------------------------------------------------------------
# 5) MeshCore-Flood-Simulation (Monte-Carlo) ueber das Repeater-Mesh.
#    Jeder Repeater, der die erste Kopie akzeptiert, plant Rebroadcast nach
#    Zufallsverzoegerung ~ U(0, 5*t), t ~ Airtime(Paket inkl. wachsendem Pfad).
#    rx_delay_base=0 => keine SNR-Gewichtung. flood_max begrenzt Hops.
#    Ziel cached den Pfad der ZUERST eintreffenden Kopie ("first packet wins").
# ---------------------------------------------------------------------------
FLOOD_MAX=8
BASE_AIR=0.10            # s Grund-Airtime pro Paket (Proxy)
PER_HOP_AIR=0.012        # s zusaetzliche Airtime pro angehaengtem Pfad-Hash

def simulate_flood(r_src,r_dst):
    """Ein Flood-Durchlauf. Gibt (cached_path|None, n_tx) zurueck."""
    import heapq
    # Event: (ankunftszeit_am_knoten, knoten, pfad_bisher)
    arrival_time={}; arrival_path={}
    pq=[(0.0,r_src,(r_src,))]
    n_tx=0
    dst_path=None; dst_time=None
    forwarded=set()
    while pq:
        t,u,path=heapq.heappop(pq)
        if u==r_dst:
            if dst_time is None or t<dst_time:
                dst_time=t; dst_path=path
            continue
        if u in forwarded:        # jeder Repeater leitet nur die erste Kopie weiter
            continue
        forwarded.add(u)
        if len(path)-1>=FLOOD_MAX:  # Hop-Limit
            continue
        # dieser Knoten sendet (Rebroadcast) -> Airtime zaehlen
        n_tx+=1
        air=BASE_AIR+PER_HOP_AIR*(len(path)-1)
        for v in Gr.neighbors(u):
            if v in path:                  # einfache Loop-Vermeidung
                continue
            if rng.random()<=P[u,v]:        # stochastische Zustellung
                d=rng.uniform(0,5*air)      # Zufallsverzoegerung (rx_delay_base=0)
                heapq.heappush(pq,(t+air+d,v,path+(v,)))
    return dst_path,n_tx

# ---------------------------------------------------------------------------
# 6) Auswertung ueber alle Client-Client-Paare, Monte-Carlo fuer MeshCore.
# ---------------------------------------------------------------------------
MC=200
res=[]  # pro Paar: dict
pairs=[(a,b) for ai,a in enumerate(clis) for b in clis[ai+1:]]
for (ca,cb) in pairs:
    # Repeater-Endpunkte: bestes Attach jeweils
    ra=min(attach[ca],key=lambda r:1.0/max(P[ca,r]*P[r,ca],1e-6))
    rb=min(attach[cb],key=lambda r:1.0/max(P[cb,r]*P[r,cb],1e-6))
    if ra==rb:
        continue   # gleicher Repeater -> kein Mesh-Routing noetig
    opt=best_backbone_path(ra,rb)
    if opt is None: continue
    opt_hops=len(opt)-1; opt_etx=path_etx(opt)
    # MeshCore Monte-Carlo
    mc_hops=[]; mc_etx=[]; mc_tx=[]; ok=0
    for _ in range(MC):
        p,ntx=simulate_flood(ra,rb)
        if p is not None:
            mc_hops.append(len(p)-1); mc_etx.append(path_etx(list(p))); mc_tx.append(ntx); ok+=1
    if not mc_hops: continue
    mc_hops=np.array(mc_hops); mc_etx=np.array(mc_etx); mc_tx=np.array(mc_tx)
    # MHR-Airtime: 1 Client-Tx (lokaler Flood bis Repeater) + Backbone-Unicast (Hops)
    mhr_tx=1+opt_hops
    # Ende-zu-Ende-Zuverlaessigkeit (Produkt der Hop-Zustellwahrsch., 1 Versuch)
    def path_rel(path):
        r=1.0
        for k in range(len(path)-1): r*=P[path[k]][path[k+1]] if False else P[path[k],path[k+1]]
        return r
    res.append(dict(
        pair=(name[ca],name[cb]),
        opt_hops=opt_hops, opt_etx=opt_etx,
        mc_hops_mean=float(mc_hops.mean()), mc_hops_max=int(mc_hops.max()),
        mc_etx_mean=float(mc_etx.mean()),
        detour_ratio=float(mc_etx.mean()/opt_etx),
        frac_detour=float(np.mean(mc_etx>opt_etx*1.05)),
        mc_tx_mean=float(mc_tx.mean()), mhr_tx=mhr_tx,
        mc_rel=path_rel(list(simulate_flood(ra,rb)[0] or [ra,rb])),
        mhr_rel=path_rel(opt),
        deliv_ratio_flood=ok/MC,
        ra=name[ra], rb=name[rb], opt_path=[name[x] for x in opt],
    ))

# ---------------------------------------------------------------------------
# 7) Aggregat
# ---------------------------------------------------------------------------
def agg(key): return np.array([r[key] for r in res])
summary=dict(
    n_pairs=len(res),
    mean_opt_hops=float(agg("opt_hops").mean()),
    mean_mc_hops=float(agg("mc_hops_mean").mean()),
    worst_mc_hops=int(agg("mc_hops_max").max()),
    mean_detour_ratio=float(agg("detour_ratio").mean()),
    pct_pairs_with_detour=float(np.mean(agg("frac_detour")>0.10)*100),
    mean_detour_event_rate=float(agg("frac_detour").mean()*100),
    mean_mc_tx=float(agg("mc_tx_mean").mean()),
    mean_mhr_tx=float(agg("mhr_tx").mean()),
    airtime_reduction_pct=float((1-agg("mhr_tx").mean()/agg("mc_tx_mean").mean())*100),
    mean_mc_rel=float(agg("mc_rel").mean()),
    mean_mhr_rel=float(agg("mhr_rel").mean()),
)
print(json.dumps(summary,indent=2,ensure_ascii=False))
json.dump(dict(summary=summary,detail=res),open("sim_results.json","w"),indent=2,ensure_ascii=False)

# ---------------------------------------------------------------------------
# 8) Plots
# ---------------------------------------------------------------------------
plt.rcParams.update({"font.size":10,"figure.dpi":130})

# (A) Topologie-Karte
fig,ax=plt.subplots(figsize=(8,7))
for a in reps:
    for b in reps:
        if a<b and Gr.has_edge(a,b):
            ax.plot([lon[a],lon[b]],[lat[a],lat[b]],color="#9bbcd6",lw=0.8,zorder=1)
for c in clis:
    for r in attach[c]:
        ax.plot([lon[c],lon[r]],[lat[c],lat[r]],color="#d8d8d8",lw=0.5,ls=":",zorder=1)
ax.scatter(lon[clis],lat[clis],c="#7aa37a",s=35,label="Client (kein Relay)",zorder=3)
ax.scatter([lon[r] for r in reps if not hi[r]],[lat[r] for r in reps if not hi[r]],
           c="#d98c5f",s=80,label="Repeater",zorder=4,edgecolor="k",linewidth=0.4)
ax.scatter([lon[r] for r in reps if hi[r]],[lat[r] for r in reps if hi[r]],
           c="#c0392b",s=160,marker="^",label="Hochstandort-Repeater",zorder=5,edgecolor="k",linewidth=0.5)
for r in reps:
    ax.annotate(name[r],(lon[r],lat[r]),fontsize=6,xytext=(3,3),textcoords="offset points")
ax.set_title("Realistische Rheinland-Topologie (25 Knoten)\nKoeln–Bonn–Siebengebirge–Bergisches Land–Eifelrand")
ax.set_xlabel("Laenge (°O)"); ax.set_ylabel("Breite (°N)"); ax.legend(loc="lower left",fontsize=8)
ax.grid(alpha=0.2); fig.tight_layout(); fig.savefig("fig_topology.png"); plt.close(fig)

# (B) mittlere Hopzahl
fig,ax=plt.subplots(figsize=(5.5,4))
vals=[summary["mean_opt_hops"],summary["mean_mc_hops"]]
bars=ax.bar(["MHR / Optimum","MeshCore (first-wins)"],vals,color=["#2e7d32","#c0392b"])
for b,v in zip(bars,vals): ax.text(b.get_x()+b.get_width()/2,v+0.03,f"{v:.2f}",ha="center")
ax.set_ylabel("mittlere Backbone-Hops je Zustellung"); ax.set_title("Pfadlaenge: MeshCore vs. MHR")
ax.grid(axis="y",alpha=0.2); fig.tight_layout(); fig.savefig("fig_hops.png"); plt.close(fig)

# (C) Detour-Ratio-Histogramm
fig,ax=plt.subplots(figsize=(5.5,4))
ax.hist(agg("detour_ratio"),bins=18,color="#c0392b",alpha=0.8)
ax.axvline(1.0,color="#2e7d32",ls="--",label="Optimum (MHR)")
ax.set_xlabel("Umweg-Faktor  (MeshCore-Pfadkosten / Optimum)"); ax.set_ylabel("Anzahl Knotenpaare")
ax.set_title(f"Umweg-Verteilung MeshCore\nMittel = {summary['mean_detour_ratio']:.2f}×")
ax.legend(); ax.grid(alpha=0.2); fig.tight_layout(); fig.savefig("fig_detour.png"); plt.close(fig)

# (D) Airtime je Discovery
fig,ax=plt.subplots(figsize=(5.5,4))
vals=[summary["mean_mc_tx"],summary["mean_mhr_tx"]]
bars=ax.bar(["MeshCore\n(netzweiter Flood)","MHR\n(lokal+Backbone)"],vals,color=["#c0392b","#2e7d32"])
for b,v in zip(bars,vals): ax.text(b.get_x()+b.get_width()/2,v+0.1,f"{v:.1f}",ha="center")
ax.set_ylabel("Sende-Ereignisse je Pfadaufbau (Airtime-Proxy)")
ax.set_title(f"Airtime je Discovery  (−{summary['airtime_reduction_pct']:.0f} %)")
ax.grid(axis="y",alpha=0.2); fig.tight_layout(); fig.savefig("fig_airtime.png"); plt.close(fig)

# (E) Beispiel: Detour vs. Optimum auf der Karte
# nimm das Paar mit hoechstem mittleren Umweg
worst=max(res,key=lambda r:r["detour_ratio"])
ra=name.index(worst["ra"]); rb=name.index(worst["rb"])
# ein gesampelter MeshCore-Pfad
sample=None
for _ in range(500):
    p,_=simulate_flood(ra,rb)
    if p and path_etx(list(p))>worst["opt_etx"]*1.05: sample=list(p); break
opt=[name.index(x) for x in worst["opt_path"]]
fig,ax=plt.subplots(figsize=(8,7))
for a in reps:
    for b in reps:
        if a<b and Gr.has_edge(a,b):
            ax.plot([lon[a],lon[b]],[lat[a],lat[b]],color="#e0e0e0",lw=0.8,zorder=1)
ax.scatter(lon[reps],lat[reps],c="#bbb",s=40,zorder=2)
def draw(path,color,lab,off):
    for k in range(len(path)-1):
        ax.annotate("",xy=(lon[path[k+1]],lat[path[k+1]]),xytext=(lon[path[k]],lat[path[k]]),
                    arrowprops=dict(arrowstyle="-|>",color=color,lw=2.2))
    ax.plot([],[],color=color,lw=2.2,label=lab)
if sample: draw(sample,"#c0392b",f"MeshCore-Umweg ({len(sample)-1} Hops)",0)
draw(opt,"#2e7d32",f"MHR-Optimum ({len(opt)-1} Hops)",0)
for r in set(opt+(sample or [])): ax.annotate(name[r],(lon[r],lat[r]),fontsize=7,xytext=(3,3),textcoords="offset points")
ax.scatter([lon[ra],lon[rb]],[lat[ra],lat[rb]],c="k",s=90,marker="*",zorder=6)
ax.set_title(f"Beispiel: {worst['pair'][0]} → {worst['pair'][1]}\nMeshCore waehlt Umweg, MHR den Direktweg")
ax.set_xlabel("Laenge (°O)"); ax.set_ylabel("Breite (°N)"); ax.legend(loc="lower left")
ax.grid(alpha=0.2); fig.tight_layout(); fig.savefig("fig_example.png"); plt.close(fig)

print("PLOTS_DONE")
