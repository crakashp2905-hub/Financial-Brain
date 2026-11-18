# PE-0050 low-volatility / ATR family overlap

`compare_rank_families` measures rank correlation and low-tail membership overlap over the names that have finite values for both features on one cross-section. It reports matched count, Spearman rank correlation, selected count, intersection, Jaccard overlap and a conservative `OVERLAPPING` or `DISTINCT_OR_UNPROVEN` label. Missing and non-finite values are excluded; there is no imputation. Both inputs are treated as low-tail risk signals, so direction must be made explicit by the caller.

The diagnostic measures redundancy only. A high overlap does not establish alpha and a low overlap does not establish that either candidate predicts returns. The production question is open until the same-date point-in-time Indian equity panel with eligible-universe and source-quality controls is measured.
