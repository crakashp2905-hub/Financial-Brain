import json, warnings; warnings.simplefilter("ignore")
import duckdb, pandas as pd, numpy as np
from financial_brain.evaluation import batch_three as B3, delivery_share as DS, standard as S
basis=pd.read_parquet("data/basis_contracts.parquet")
con=duckdb.connect("data/financial_brain.duckdb",read_only=True)
pan=B3.panels(con,basis)
D=DS.delivery_panel(con,{s:pan["t2l"][s] for s in pan["C"].columns},pan["C"].index); con.close()
print("delivery panel",D.shape,"coverage",round(float(D.notna().mean().mean()),3),flush=True)
r=DS.run(pan,D); r.to_parquet("data/edges/d1_net.parquet")
g3=S.replication(r["net"]); print("G3",json.dumps(g3,default=float),flush=True)
gg=S.replication(r["gross"]); print("gross",gg["mean_a"],gg["mean_b"],"cost/period",r["cost"].mean(),flush=True)
g4=DS.null(pan,D,r["net"]); print("G4",g4,flush=True)
json.dump({"g3":g3,"g4":g4,"gross":(gg["mean_a"],gg["mean_b"]),"cost":float(r["cost"].mean())},open("data/edges/d1_result.json","w"),default=float,indent=1)
