import json, time, warnings; warnings.simplefilter("ignore")
import duckdb, numpy as np, pandas as pd
from financial_brain.evaluation import anomalies as AN, standard as S, index_rules as IR, batch_three as B3, family_validation as FV
t0=time.time(); out={}; fam={}
con=duckdb.connect("data/financial_brain.duckdb",read_only=True)
P=AN.panel(con); con.close()
dates=AN.month_ends(P); sigs=AN.signals(P,dates)
print("signals",round(time.time()-t0),"s",flush=True)
for name,side in AN.SIDES.items():
    r=AN.run_cross(P,sigs,name,dates); net=r["net"]
    g3=S.replication(net); dp=AN.depth(P,sigs[name],side,r)
    g4=S.null_separation(net,r["F"],r["cost"],r["bench"],r["books"],r["eligible"],draws=200 if g3["pass"] else 100)
    out[name]={"periods":len(net),"g3":g3,"g4":g4,"depth":dp}; fam[name]=net
    print(f"{name:22s} n={len(net):3d} net {g3['mean_a']*100:+.2f}%/{g3['mean_b']*100:+.2f}% t {g3['t_a']:+.2f}/{g3['t_b']:+.2f} | IC {dp['ic'][0]:+.3f} (t {dp['ic'][1]:+.1f}) tiers "
          + " ".join(f"{v[1]:+.1f}" for v in dp['tiers'].values()) + f" | LS {dp['ls_gross'][0]*100:+.2f}% (t {dp['ls_gross'][1]:+.1f}) turn {dp['turnover']:.0%} SR {dp['sharpe_net']:+.2f} | G4 {g4['stat']:+.2f} vs {g4['null_q95']:+.2f} {'G3 PASS' if g3['pass'] else ''}{' G4 PASS' if g4['pass'] else ''} [{round(time.time()-t0)}s]",flush=True)
m=pd.read_parquet("data/edges/nifty50_minute.parquet"); m=m[pd.to_datetime(m["ts"]).dt.tz_localize(None)<pd.Timestamp("2026-10-09")]
p=IR.at_1515(m)
for name,w in AN.calendar_positions(p).items():
    mo=IR.monthly(IR.timing_outcome(p,w)); g3=S.replication(mo); g4=IR.shift_null(p,w,draws=200)
    out[name]={"periods":len(mo),"g3":g3,"g4":g4}; fam[name]=mo
    print(f"{name:22s} timing {g3['mean_a']*100:+.2f}%/{g3['mean_b']*100:+.2f}% a month t {g3['t_a']:+.2f}/{g3['t_b']:+.2f} | G4 {g4['stat']:+.2f} vs {g4['null_q95']:+.2f}",flush=True)
ev,pool=AN.year_end(P); g3=S.replication(ev.set_index("d")["out"]); g4=B3.event_null(ev[["d","out"]],pool,draws=200)
out["A16_india_year_end"]={"years":len(ev),"g3":g3,"g4":g4,"by_year":dict(zip(ev["d"].astype(str),ev["out"]))}
print(f"A16_india_year_end     years {len(ev)} mean {g3['mean_a']*100:+.2f}%/{g3['mean_b']*100:+.2f}% t {g3['t_a']:+.2f}/{g3['t_b']:+.2f} | G4 {g4['stat']:+.2f} vs {g4['null_q95']:+.2f}",flush=True)
X=pd.DataFrame({k:(v.groupby(pd.to_datetime(v.index).to_period("M")).sum()) for k,v in fam.items()}).dropna()
rc=FV.reality_check(X,reps=2000); pb=FV.pbo(X,blocks=16 if len(X)>=64 else 8)
out["_family"]={"aligned_months":len(X),"start":str(X.index.min()),"reality_check":rc,"pbo":pb}
print("family", len(X), "months from", X.index.min(), rc, pb, flush=True)
pd.DataFrame(fam).to_parquet("data/edges/a10_series.parquet")
json.dump(out,open("data/edges/a10_result.json","w"),default=float,indent=1)
print("done",round(time.time()-t0),"s")
