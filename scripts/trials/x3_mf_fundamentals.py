import json, pickle, time
import duckdb, pandas as pd, numpy as np
from financial_brain.evaluation import edges as E, fundamental_tests as FT, standard as S
t0 = time.time()
c = duckdb.connect("data/financial_brain.duckdb", read_only=True)
P = E.load(c, start="2018-06-01")
st = FT.statements(c)
dates = [d for d in E.month_ends(P["C"].index, start="2019-06-01")]
raw = FT.raw_closes(c, list(P["C"].columns), dates)
c.close()
print("loaded", round(time.time() - t0), "s", len(dates), "month ends", flush=True)
sue = FT.sue_table(st)
print("sue rows", len(sue), "after 2025:", int((sue.filed_at >= FT.X3_FROM).sum()), flush=True)
x3 = FT.x3_cohorts(P, sue)
print("X3", {k: v for k, v in x3.items() if k != "series"}, flush=True)
for r in x3["series"]:
    print("  ", r)
sig = FT.mf_signals(P, st, raw, dates)
mf = FT.mf_book(P, sig)
net = mf["net"]
rep = S.replication(net)
print("MF n", len(net), "first", net.index.min(), "last", net.index.max(), "names/book", np.mean([len(v) for v in mf["books"].values()]),
      "eligible", np.mean([len(v) for v in mf["eligible"].values()]), "turnover", mf["turnover"], flush=True)
print("MF rep", rep, "full t", S.tstat(net), "mean", net.mean(), "ann sharpe", net.mean() / net.std() * np.sqrt(12), flush=True)
for k, s in mf["parts"].items():
    print("  part", k, S.replication(s), "full t", S.tstat(s))
nul = S.null_separation(net, mf["F"], mf["cost"], mf["bench"], mf["books"], mf["eligible"])
print("MF null", nul, flush=True)
bench = mf["bench"]
print("EW eligible bench mean monthly", bench.mean(), "gross book", (net + bench).mean())
pickle.dump({"x3": x3, "net": net, "bench": bench, "parts": mf["parts"], "rep": rep, "null": nul, "turnover": mf["turnover"],
             "books": mf["books"]}, open("data/edges/fundamental_trials.pkl", "wb"))
print("done", round(time.time() - t0), "s")
