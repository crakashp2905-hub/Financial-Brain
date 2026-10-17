import json, warnings; warnings.simplefilter("ignore")
import duckdb, pandas as pd, numpy as np
from financial_brain.evaluation import batch_three as B3, bab, standard as S
basis=pd.read_parquet("data/basis_contracts.parquet")
con=duckdb.connect("data/financial_brain.duckdb",read_only=True); pan=B3.panels(con,basis); con.close()
cache={}
r=bab.run(pan,cache=cache)
g3=S.replication(r["net"]); print("G3",json.dumps(g3,default=float),flush=True)
a,b=S.halves(r["net"])
for nm,x in (("a",a),("b",b)): print(nm,"mean %.4f sharpe/yr %.2f"%(x.mean(),x.mean()/x.std()*np.sqrt(12)),flush=True)
print("avg beta L/H",r.beta_l.mean(),r.beta_h.mean(),"avg cost",r.cost.mean(),"names/leg",r.n.mean(),flush=True)
g4=bab.null(pan,r["net"],cache=cache); print("G4",g4,flush=True)
r.to_parquet("data/edges/bab_monthly.parquet")
json.dump({"g3":g3,"g4":g4,"beta_l":float(r.beta_l.mean()),"beta_h":float(r.beta_h.mean())},open("data/edges/bab_result.json","w"),default=float,indent=1)
