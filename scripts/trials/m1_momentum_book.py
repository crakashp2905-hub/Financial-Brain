import time, json, warnings; warnings.simplefilter("ignore")
import duckdb, numpy as np, pandas as pd
from financial_brain.evaluation import momentum_book as M, standard as S, edges as E
t0=time.time()
con=duckdb.connect("data/financial_brain.duckdb",read_only=True)
P=E.load(con); print("panel",P["C"].shape, round(time.time()-t0),"s")
ev=M.n2_events(con,P); print("n2 events",len(ev),"flagged",int(ev.flag.sum()), round(time.time()-t0),"s")
con.close()
r=M.run(None,P=P,events=ev)
net=r["net"]
g3=S.replication(net); print("G3",json.dumps(g3,default=float))
print("turnover",r["turnover"], "periods",len(net), "mean",net.mean())
a,b=S.halves(net); 
for nm,x in (("2016-21",a),("2022-26",b)): print(nm, "mean %.4f sd %.4f sharpe/yr %.2f"%(x.mean(),x.std(),x.mean()/x.std()*np.sqrt(252/40)))
r0=M.run(None,P=P,exclude_n2=False)
print("without N2: G3",json.dumps(S.replication(r0["net"]),default=float),"mean",r0["net"].mean(),"diff",net.mean()-r0["net"].mean())
nul=S.null_separation(net,r["F"],r["cost"],r["bench"],r["books"],r["eligible"],weigh=r["weigh"],draws=200)
print("G4",nul, round(time.time()-t0),"s")
pd.DataFrame({"net":net}).to_parquet("data/edges/m1_net.parquet")
pd.DataFrame({"net":r0["net"]}).to_parquet("data/edges/m1_net_no_n2.parquet")
json.dump({"g3":g3,"g4":nul,"turnover":r["turnover"],"no_n2":S.replication(r0["net"]),"n2_diff":float(net.mean()-r0["net"].mean()),
 "n_events":len(ev),"n_flag":int(ev.flag.sum())},open("data/edges/m1_result.json","w"),default=float,indent=1)
