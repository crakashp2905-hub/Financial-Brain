# PE-0121 — Calendar and instrument clustering

`two_way_cluster_se` implements calendar-date and instrument clustering with the intersection correction. Synthetic tests verify that date-only dependence agrees with the one-way date-cluster estimate when names are unique, and that simultaneous date and name shocks yield substantially larger uncertainty than the iid standard error.

**Status: DONE for the two-way clustered estimator and its failure cases.** Input provenance and policy freeze still apply to any formal strategy claim.

Evidence and source hashes are in `pe-0121-two-way-trade-clustering-evidence.json`.
