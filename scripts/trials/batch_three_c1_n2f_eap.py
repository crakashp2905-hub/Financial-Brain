import json, time, warnings; warnings.simplefilter("ignore")
import duckdb, pandas as pd
from financial_brain.evaluation import batch_three as B3, standard as S
t0=time.time(); out={}
basis=pd.read_parquet("data/basis_contracts.parquet")
ev,pool=B3.c1(basis)
g3=S.replication(B3.monthly(ev)); g4=B3.event_null(ev,pool)
out["C1"]={"events":len(ev),"months":len(B3.monthly(ev)),"g3":g3,"g4":g4}; print("C1",json.dumps(out["C1"],default=float),flush=True)
ev.to_parquet("data/edges/c1_events.parquet")
con=duckdb.connect("data/financial_brain.duckdb",read_only=True)
pan=B3.panels(con,basis); print("panels",pan["C"].shape,round(time.time()-t0),"s",flush=True)
for name,fn in (("N2F",lambda: B3.n2f(con,pan)),("EAP",lambda: B3.eap(con,pan))):
    ev,pool=fn()
    g3=S.replication(B3.monthly(ev)); g4=B3.event_null(ev,pool)
    out[name]={"events":len(ev),"months":len(B3.monthly(ev)),"mean_event":float(ev["out"].mean()),"g3":g3,"g4":g4}
    print(name,json.dumps(out[name],default=float),round(time.time()-t0),"s",flush=True)
    ev.to_parquet(f"data/edges/{name.lower()}_events.parquet")
json.dump(out,open("data/edges/batch_three_result.json","w"),default=float,indent=1)
