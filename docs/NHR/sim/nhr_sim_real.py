#!/usr/bin/env python3
"""
MeshCore vs. NHR — 25 ECHTE Knoten aus CoreScope (corescope.meshrheinland.de, live
abgerufen 2026-05-29 via /api/nodes). Position + Rolle sind real (Raum Bonn / Rhein-Sieg
/ Siebengebirge / Lohmar / Leverkusen). Links physikbasiert auf den realen Koordinaten
(keine reale SNR-Adjazenz verfuegbar -> Log-Distance-Modell; Gelaende/Sicht nicht modelliert).
"""
import numpy as np, networkx as nx, math, json
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
rng=np.random.default_rng(42)

# --- 25 reale Knoten: (name, lat, lon, role) ---
NODES=[
 ("Lohmar #27",50.87246,7.22781,"R"),
 ("51143-SOLAR",50.85156,7.0149,"R"),
 ("D-CGN MSE2 Solar",50.91083,6.98953,"R"),
 ("SU-SGB",50.79976,7.21204,"R"),
 ("BN-Ruengsdorf",50.6813,7.17157,"R"),
 ("Oelberg IGFS",50.68226,7.24813,"R"),
 ("53343 Zuelligh.",50.61725,7.1566,"R"),
 ("SU Lichtenberg",50.74055,7.34146,"R"),
 ("LEV-JO31MA",51.03895,7.06893,"R"),
 ("MakiAlfter",50.70918,7.01943,"R"),
 ("Pending-Bonn",50.75502,7.10202,"R"),
 ("Bonn-Nord Solar",50.74622,7.07389,"R"),
 ("Alfter-Oedekoven",50.7205,7.02118,"R"),
 ("Bonn-Oberkassel",50.71133,7.17796,"R"),
 ("Lohmar #17a",50.88992,7.2832,"R"),
 ("Lohmar #17b",50.88991,7.28317,"R"),
 ("CGN1",50.87886,7.12384,"R"),
 ("Bonn-Duisdorf FGZ",50.71399,7.04644,"R"),
 # Companions (leiten nicht weiter)
 ("Cli ZORT",50.6445,7.18808,"C"),
 ("Cli Rheinb-OS",50.87487,7.01809,"C"),
 ("Cli Ulli/p",50.71954,7.05864,"C"),
 ("Cli PXTiny",50.72427,7.10676,"C"),
 ("Cli LordWhopper",50.78657,7.15789,"C"),
 ("Cli DL4FP",50.72032,7.0264,"C"),
 ("Cli Marcus-E",50.82462,7.27349,"C"),
]
N=len(NODES)
name=[x[0] for x in NODES]; lat=np.array([x[1] for x in NODES]); lon=np.array([x[2] for x in NODES])
role=[x[3] for x in NODES]
reps=[i for i in range(N) if role[i]=="R"]; clis=[i for i in range(N) if role[i]=="C"]

def hav(i,j):
    R=6371.0;p1,p2=math.radians(lat[i]),math.radians(lat[j])
    dphi=math.radians(lat[j]-lat[i]);dl=math.radians(lon[j]-lon[i])
    a=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))

# Uniformes Log-Distance-Modell auf realen Koordinaten
SNR0=17.0; PLE=2.55; SNR_THR=-12.0
def link_snr(i,j):
    d=max(hav(i,j),0.05); return SNR0-10*PLE*math.log10(d)
def deliv(snr): return float(np.clip(1/(1+math.exp(-(snr-SNR_THR)/3.0)),0,0.995))
P=np.zeros((N,N))
for i in range(N):
    for j in range(N):
        if i!=j:
            s=link_snr(i,j); P[i,j]=deliv(s) if s>SNR_THR else 0.0

Gr=nx.Graph(); Gr.add_nodes_from(reps)
for a in reps:
    for b in reps:
        if a<b and P[a,b]>0.05 and P[b,a]>0.05:
            Gr.add_edge(a,b,etx=1.0/(P[a,b]*P[b,a]))
# Konnektivitaet pruefen
comp=list(nx.connected_components(Gr.subgraph(reps)))
giant=max(comp,key=len)
print("Repeater:",len(reps),"| groesste Komponente:",len(giant),"| Komponenten:",[len(c) for c in comp])

attach={c:[r for r in reps if P[c,r]>0.25 and P[r,c]>0.25] for c in clis}
for c in clis:
    if not attach[c]: attach[c]=[max(reps,key=lambda r:P[c,r])]

def best_path(a,b):
    try: return nx.shortest_path(Gr,a,b,weight="etx")
    except nx.NetworkXNoPath: return None
def petx(p): return sum(Gr[p[k]][p[k+1]]["etx"] for k in range(len(p)-1))

FLOOD_MAX=8; BASE_AIR=0.10; PER_HOP_AIR=0.012
def flood(rs,rd):
    import heapq; pq=[(0.0,rs,(rs,))]; fwd=set(); dt=None; dp=None; ntx=0
    while pq:
        t,u,path=heapq.heappop(pq)
        if u==rd:
            if dt is None or t<dt: dt=t; dp=path
            continue
        if u in fwd: continue
        fwd.add(u)
        if len(path)-1>=FLOOD_MAX: continue
        ntx+=1; air=BASE_AIR+PER_HOP_AIR*(len(path)-1)
        for v in Gr.neighbors(u):
            if v in path: continue
            if rng.random()<=P[u,v]:
                pq_=t+air+rng.uniform(0,5*air); heapq.heappush(pq,(pq_,v,path+(v,)))
    return dp,ntx

MC=200; res=[]
for ai,ca in enumerate(clis):
    for cb in clis[ai+1:]:
        ra=min(attach[ca],key=lambda r:1.0/max(P[ca,r]*P[r,ca],1e-6))
        rb=min(attach[cb],key=lambda r:1.0/max(P[cb,r]*P[r,cb],1e-6))
        if ra==rb: continue
        opt=best_path(ra,rb)
        if opt is None: continue
        oh=len(opt)-1; oe=petx(opt)
        hh=[];ee=[];tt=[];ok=0
        for _ in range(MC):
            p,nt=flood(ra,rb)
            if p: hh.append(len(p)-1); ee.append(petx(list(p))); tt.append(nt); ok+=1
        if not hh: continue
        hh=np.array(hh);ee=np.array(ee);tt=np.array(tt)
        def rel(p):
            r=1.0
            for k in range(len(p)-1): r*=P[p[k],p[k+1]]
            return r
        sp=flood(ra,rb)[0]
        res.append(dict(pair=(name[ca],name[cb]),ra=name[ra],rb=name[rb],
            opt_hops=oh,opt_etx=oe,mc_hops_mean=float(hh.mean()),mc_hops_max=int(hh.max()),
            mc_etx_mean=float(ee.mean()),detour_ratio=float(ee.mean()/oe),
            frac_detour=float(np.mean(ee>oe*1.05)),mc_tx_mean=float(tt.mean()),nhr_tx=1+oh,
            mc_rel=rel(list(sp) if sp else [ra,rb]),nhr_rel=rel(opt),
            opt_path=[name[x] for x in opt]))

def agg(k): return np.array([r[k] for r in res])
S=dict(n_pairs=len(res),mean_opt_hops=float(agg("opt_hops").mean()),
 mean_mc_hops=float(agg("mc_hops_mean").mean()),worst_mc_hops=int(agg("mc_hops_max").max()),
 mean_detour_ratio=float(agg("detour_ratio").mean()),
 pct_pairs_detour=float(np.mean(agg("frac_detour")>0.10)*100),
 mean_detour_rate=float(agg("frac_detour").mean()*100),
 mean_mc_tx=float(agg("mc_tx_mean").mean()),mean_nhr_tx=float(agg("nhr_tx").mean()),
 airtime_red_pct=float((1-agg("nhr_tx").mean()/agg("mc_tx_mean").mean())*100),
 mean_mc_rel=float(agg("mc_rel").mean()),mean_nhr_rel=float(agg("nhr_rel").mean()))
print(json.dumps(S,indent=2,ensure_ascii=False))
json.dump(dict(summary=S,detail=res),open("sim_results_real.json","w"),indent=2,ensure_ascii=False)

# ---- Plots ----
plt.rcParams.update({"font.size":10,"figure.dpi":130})
fig,ax=plt.subplots(figsize=(8.2,7.4))
for a in reps:
    for b in reps:
        if a<b and Gr.has_edge(a,b): ax.plot([lon[a],lon[b]],[lat[a],lat[b]],color="#9bbcd6",lw=0.8,zorder=1)
for c in clis:
    for r in attach[c]: ax.plot([lon[c],lon[r]],[lat[c],lat[r]],color="#ddd",lw=0.5,ls=":",zorder=1)
ax.scatter(lon[clis],lat[clis],c="#7aa37a",s=40,label="Client (kein Relay)",zorder=3)
ax.scatter(lon[reps],lat[reps],c="#d98c5f",s=85,label="Repeater",zorder=4,edgecolor="k",lw=0.4)
for i in range(N): ax.annotate(name[i],(lon[i],lat[i]),fontsize=5.5,xytext=(2,2),textcoords="offset points")
ax.set_title("ECHTE CoreScope-Topologie (25 Knoten, Raum Bonn–Rhein-Sieg–Lohmar)\nPosition+Rolle live aus corescope.meshrheinland.de")
ax.set_xlabel("Laenge (°O)");ax.set_ylabel("Breite (°N)");ax.legend(loc="lower left",fontsize=8);ax.grid(alpha=0.2)
fig.tight_layout();fig.savefig("fig_real_topology.png");plt.close(fig)

fig,ax=plt.subplots(figsize=(5.5,4))
v=[S["mean_opt_hops"],S["mean_mc_hops"]];b=ax.bar(["NHR / Optimum","MeshCore"],v,color=["#2e7d32","#c0392b"])
for bb,vv in zip(b,v):ax.text(bb.get_x()+bb.get_width()/2,vv+0.02,f"{vv:.2f}",ha="center")
ax.set_ylabel("Ø Backbone-Hops");ax.set_title("Pfadlaenge (echte Topologie)");ax.grid(axis="y",alpha=0.2)
fig.tight_layout();fig.savefig("fig_real_hops.png");plt.close(fig)

fig,ax=plt.subplots(figsize=(5.5,4))
ax.hist(agg("detour_ratio"),bins=16,color="#c0392b",alpha=0.8);ax.axvline(1,color="#2e7d32",ls="--",label="Optimum")
ax.set_xlabel("Umweg-Faktor (MeshCore/Optimum)");ax.set_ylabel("Knotenpaare")
ax.set_title(f"Umweg-Verteilung (Ø {S['mean_detour_ratio']:.2f}×)");ax.legend();ax.grid(alpha=0.2)
fig.tight_layout();fig.savefig("fig_real_detour.png");plt.close(fig)

fig,ax=plt.subplots(figsize=(5.5,4))
v=[S["mean_mc_tx"],S["mean_nhr_tx"]];b=ax.bar(["MeshCore\n(netzweiter Flood)","NHR\n(lokal+Backbone)"],v,color=["#c0392b","#2e7d32"])
for bb,vv in zip(b,v):ax.text(bb.get_x()+bb.get_width()/2,vv+0.1,f"{vv:.1f}",ha="center")
ax.set_ylabel("Sende-Ereignisse je Discovery");ax.set_title(f"Airtime je Discovery (−{S['airtime_red_pct']:.0f} %)");ax.grid(axis="y",alpha=0.2)
fig.tight_layout();fig.savefig("fig_real_airtime.png");plt.close(fig)

# Beispiel groesster Umweg
worst=max(res,key=lambda r:r["detour_ratio"])
ra=name.index(worst["ra"]);rb=name.index(worst["rb"]);sample=None
for _ in range(800):
    p,_=flood(ra,rb)
    if p and petx(list(p))>worst["opt_etx"]*1.05: sample=list(p);break
opt=[name.index(x) for x in worst["opt_path"]]
fig,ax=plt.subplots(figsize=(8.2,7.4))
for a in reps:
    for b in reps:
        if a<b and Gr.has_edge(a,b): ax.plot([lon[a],lon[b]],[lat[a],lat[b]],color="#e3e3e3",lw=0.8)
ax.scatter(lon[reps],lat[reps],c="#bbb",s=45)
def draw(p,c,l):
    for k in range(len(p)-1):
        ax.annotate("",xy=(lon[p[k+1]],lat[p[k+1]]),xytext=(lon[p[k]],lat[p[k]]),arrowprops=dict(arrowstyle="-|>",color=c,lw=2.4))
    ax.plot([],[],color=c,lw=2.4,label=l)
if sample: draw(sample,"#c0392b",f"MeshCore-Umweg ({len(sample)-1} Hops)")
draw(opt,"#2e7d32",f"NHR-Optimum ({len(opt)-1} Hops)")
for r in set(opt+(sample or [])): ax.annotate(name[r],(lon[r],lat[r]),fontsize=7,xytext=(3,3),textcoords="offset points")
ax.scatter([lon[ra],lon[rb]],[lat[ra],lat[rb]],c="k",s=110,marker="*",zorder=6)
ax.set_title(f"Beispiel (echt): {worst['pair'][0]} → {worst['pair'][1]}");ax.set_xlabel("Laenge (°O)");ax.set_ylabel("Breite (°N)")
ax.legend(loc="lower left");ax.grid(alpha=0.2);fig.tight_layout();fig.savefig("fig_real_example.png");plt.close(fig)
print("PLOTS_DONE")
