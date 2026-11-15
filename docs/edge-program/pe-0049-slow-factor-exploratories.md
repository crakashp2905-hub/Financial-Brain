# PE-0049 slow-factor exploratory specs

The codebase already computes `mom_12_1` (12-month adjusted return skipping the latest 21 sessions) and `dist_52w_high` (distance to the trailing 250-session high). PE-0049 now gives these two hypotheses distinct exploratory strategy identities: `PE49-CS-MOM-12-1` and `PE49-CS-52W-HIGH`.

Both are NSE cash, daily-close, 40-session, long-short top/bottom-decile declarations with explicit parameter digests and source lineage. They register as `EXPLORATORY`, have no paper eligibility, and do not rewrite or inherit the failed verdict for the distinct cost-aware M1 book. The strategy code digest is the content hash of this declaration module; the parameter digest separately identifies each design.

No return, edge, or profitability is asserted. A complete trial still needs the point-in-time universe/data lineage, a frozen charter/benchmark/cost model, overlap and multiplicity controls, and prospective evidence. The failed M1 row remains unchanged.
