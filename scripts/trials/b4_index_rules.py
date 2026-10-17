import json, warnings; warnings.simplefilter("ignore")
import numpy as np, pandas as pd
from financial_brain.evaluation import index_rules as IR, standard as S
m=pd.read_parquet("data/edges/nifty50_minute.parquet")
m=m[pd.to_datetime(m["ts"]).dt.tz_localize(None) < pd.Timestamp("2026-10-09")]       # data as of registration
p=IR.at_1515(m); print("sessions",len(p),p.index.min().date(),p.index.max().date(),flush=True)
out={}
for k,w in IR.positions(p).items():
    o=IR.monthly(IR.timing_outcome(p,w)); g3=S.replication(o)
    g4=IR.shift_null(p,w) if g3["pass"] else {"pass":False,"why":"G3 failed"}
    out[k]={"exposure":float(w.abs().mean()),"turnover_per_yr":float(w.diff().abs().sum()/len(w)*252),"g3":g3,"g4":g4}
    print(f"{k:22s} in {w.abs().mean():.0%}  mean/mo {g3['mean_a']*100:+.2f}% / {g3['mean_b']*100:+.2f}%  t {g3['t_a']:+.2f} / {g3['t_b']:+.2f}  {'G3 PASS' if g3['pass'] else ''} {'G4 PASS' if g4.get('pass') else ''}",flush=True)
t=IR.vwap_trades(m); v=(t["side"]*t["move"]-IR.INTRADAY_RT); v.index=pd.to_datetime(v.index)
vm=v.groupby(v.index.to_period("M")).sum(); vm.index=vm.index.to_timestamp()
g3=S.replication(vm)
g4={"pass":False,"why":"G3 failed"}
if g3["pass"]:
    rng=np.random.default_rng(20261014); sims=[]
    for _ in range(200):
        sh=np.roll(t["side"].to_numpy(), int(rng.integers(20,len(t)-20)))
        s=pd.Series(sh*t["move"].to_numpy()-IR.INTRADAY_RT,index=v.index); sm=s.groupby(s.index.to_period("M")).sum(); sm.index=sm.index.to_timestamp()
        sims.append(S.replication(sm)["stat"])
    q=float(np.nanquantile(sims,0.95)); g4={"stat":g3["stat"],"null_q95":q,"pass":g3["stat"]>q}
out["v1_vwap_bounce"]={"trades":len(t),"long_share":float((t.side>0).mean()),"mean_trade_bp":float(v.mean()*1e4),"g3":g3,"g4":g4}
print(f"v1_vwap_bounce  trades {len(t)} ({(t.side>0).mean():.0%} long)  mean/trade {v.mean()*1e4:+.1f} bp  mean/mo {g3['mean_a']*100:+.2f}% / {g3['mean_b']*100:+.2f}%  t {g3['t_a']:+.2f} / {g3['t_b']:+.2f} {'G3 PASS' if g3['pass'] else ''} {'G4 PASS' if g4.get('pass') else ''}")
json.dump(out,open("data/edges/b4_result.json","w"),default=float,indent=1)
