"""Trial 11 run: reads data/minute_history.duckdb (read-only), F&O names only."""
import json, time, warnings; warnings.simplefilter("ignore")
import duckdb, numpy as np, pandas as pd
from financial_brain.evaluation import intraday_family as IF, standard as S, family_validation as FV
t0=time.time()
basis=pd.read_parquet("data/basis_contracts.parquet")
fno={}
for sym,s,e in zip(basis["symbol"],pd.to_datetime(basis["start"]),pd.to_datetime(basis["expiry"])):
    fno.setdefault(sym,set()).update(pd.bdate_range(s,e))
con=duckdb.connect("data/minute_history.duckdb",read_only=True)
have={r[0] for r in con.execute("SELECT DISTINCT tradingsymbol FROM minute_bars").fetchall()}
syms=sorted(have & set(fno)); print("F&O symbols with minute history:",len(syms),"of",len(set(fno)),flush=True)
scans={}
for i,s in enumerate(syms):
    m=con.execute("SELECT ts, open, high, low, close, volume FROM minute_bars WHERE tradingsymbol=? AND ts < '2026-10-09' ORDER BY ts",[s]).df()
    if len(m)<1000: continue
    scans[s]=IF.scan_symbol(s,m,fno[s])
    if i%20==0: print(i,s,round(time.time()-t0),"s",flush=True)
con.close()
ev=IF.assemble(scans,fno); out={}; fam={}
for rule,e in ev.items():
    mo=IF.monthly(e); g3=S.replication(mo); gg=S.replication(IF.monthly(e,"gross")) if len(e) else {}
    g4=IF.null(rule,e,scans,fno,draws=200 if g3.get("pass") else 50) if len(e) else {"pass":False}
    out[rule]={"events":len(e),"g3":g3,"gross":(gg.get("mean_a"),gg.get("mean_b")),"g4":g4}; fam[rule]=mo
    print(f"{rule:28s} n={len(e):6d} gross {gg.get('mean_a',np.nan)*1e4:+.1f}/{gg.get('mean_b',np.nan)*1e4:+.1f} bp  net {g3['mean_a']*1e4:+.1f}/{g3['mean_b']*1e4:+.1f} bp  t {g3['t_a']:+.2f}/{g3['t_b']:+.2f}  G4 {g4.get('stat',np.nan):+.2f} vs {g4.get('null_q95',np.nan):+.2f} {'G3 PASS' if g3['pass'] else ''}{' G4 PASS' if g4.get('pass') else ''} [{round(time.time()-t0)}s]",flush=True)
X=pd.DataFrame(fam).dropna()
fv={"reality_check":FV.reality_check(X,reps=2000),"pbo":FV.pbo(X,blocks=16 if len(X)>=64 else 8),"months":len(X)}
out["_family"]=fv; print("family",fv,flush=True)
for r,e in ev.items(): e.to_parquet(f"data/edges/t11_{r}.parquet")
json.dump(out,open("data/edges/t11_result.json","w"),default=str,indent=1)
print("done",round(time.time()-t0),"s")
