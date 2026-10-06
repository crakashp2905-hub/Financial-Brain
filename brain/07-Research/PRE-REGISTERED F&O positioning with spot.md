---
type: research
tags:
  - research
  - pre-registration
  - fno
date: 2026-10-06
status: pre-registered
---

# PRE-REGISTERED F&O positioning with spot

Written before any data exists to test it: nightly F&O snapshots began on 2026-10-05 (`fb fno-snapshot`), so the
test runs once **60 sessions** of snapshots have accumulated (around January 2027), on exactly the data collected
after this note.

## Question

Does derivatives positioning add to the spot ranking? `ingest/kite_fno.combined` joins, per F&O name and session,
the futures build-up (long build-up, short covering, short build-up, long unwinding, neutral), the annualised basis
and the implied-vol percentile with the spot features.

## Test

For F&O names only (about 200), on each session: the forward 5- and 20-session return in excess of the F&O universe
mean, regressed cross-sectionally on (a) the composite alone, (b) the composite plus the positioning variables.
Statistic: the mean improvement in rank IC from (a) to (b), one value per session, t across sessions; and the mean
excess of long build-up names against short build-up names.

## Predictions

* **P1** Positioning adds less than 0.01 to the rank IC at 5 sessions and nothing at 20: price-and-OI build-up is
  largely a restatement of the day's price move.
* **P2** Long build-up minus short build-up is positive at 1 session and under 0.3% at 5, not clearing the
  Bonferroni bar of the time.
* **P3** Basis and IV percentile are null.

Until this is run, positioning appears on the trader view as context labelled "untested as a signal".
