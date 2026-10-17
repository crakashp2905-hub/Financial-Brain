import json, warnings; warnings.simplefilter("ignore")
import duckdb, numpy as np, pandas as pd
from financial_brain.evaluation import short_buildup as SB, standard as S
con=duckdb.connect("data/financial_brain.duckdb",read_only=True); fut=duckdb.connect("data/futures_history.duckdb",read_only=True)
r=SB.run(con,fut); con.close(); fut.close()
ex=r["excess"]; print("periods",len(ex),"first",ex.index.min(),"avg flagged",r["n_flagged"])
g3=S.replication(ex,direction=-1); print("G3",json.dumps(g3,default=float))
print("short net (futures, 10bp):",json.dumps(S.replication(r["short_net"]),default=float))
nul=S.null_separation(ex,r["F"],r["cost"],r["bench"],r["books"],r["eligible"],direction=-1,draws=200)
print("G4",nul)
pd.DataFrame({"net":ex}).to_parquet("data/edges/f3_excess.parquet")
pd.DataFrame({"net":r["short_net"]}).to_parquet("data/edges/f3_short_net.parquet")
json.dump({"g3":g3,"g4":nul,"short":S.replication(r["short_net"]),"n_flagged":r["n_flagged"],"periods":len(ex)},open("data/edges/f3_result.json","w"),default=float,indent=1)
