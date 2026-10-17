import json, time, warnings; warnings.simplefilter("ignore")
import duckdb, pandas as pd
from financial_brain.evaluation import batch_three as B3, chart_setups as K, standard as S
t0=time.time()
basis=pd.read_parquet("data/basis_contracts.parquet")
con=duckdb.connect("data/financial_brain.duckdb",read_only=True)
pan=B3.panels(con,basis)
sig=K.signals(con,{s:pan["t2l"][s] for s in pan["C"].columns})
con.close(); print("signals",len(sig),round(time.time()-t0),"s",flush=True)
ev=K.events(sig,pan["C"],pan["M"]); ev.to_parquet("data/edges/k7_events.parquet")
print("events",len(ev),flush=True)
out={}
for setup,(side,_) in K.SETUPS.items():
    e=ev[ev.setup==setup][["d","out","gross"]]
    g3=S.replication(B3.monthly(e)); g3g=S.replication(B3.monthly(e,"gross"))
    g4=B3.event_null(e,K.pool_for(pan["C"],pan["M"],side),draws=200) if g3["pass"] else {"pass":False,"why":"G3 failed; null not needed"}
    out[setup]={"side":side,"events":len(e),"g3":g3,"gross_t":(g3g["t_a"],g3g["t_b"]),"gross_mean":(g3g["mean_a"],g3g["mean_b"]),"g4":g4}
    print(f"{setup:18s} {side:+d} n={len(e):5d} gross {g3g['mean_a']*100:+.2f}%/{g3g['mean_b']*100:+.2f}%  net {g3['mean_a']*100:+.2f}%/{g3['mean_b']*100:+.2f}%  t {g3['t_a']:+.2f}/{g3['t_b']:+.2f}  {'PASS G3' if g3['pass'] else ''} {'PASS G4' if g4.get('pass') else ''}",flush=True)
json.dump(out,open("data/edges/k7_result.json","w"),default=float,indent=1)
print("done",round(time.time()-t0),"s")
