# MF terminal holdings panel v1

The MF paper book display now includes NAV availability time, position units and cost
basis, a full-position exit-load estimate when a current NAV and source-pinned lot
schedules are available, pending purchase/allotment orders, and existing pending
redemption/payout states. Unknown lot terms prevent the exit-load estimate; stale NAVs
do not produce a current exit-load estimate.

The table explicitly labels tax estimates unavailable because there is no current India
MF tax classification/rate schedule in the project. The paper model also has no sourced
scheme cutoff feed connected to this terminal; eligible NAV dates and pending order
states are shown, and the panel identifies cutoff as unavailable rather than inventing
one.

This is **PARTIAL** for FB-1107. Remaining work is a permissioned effective-dated
cutoff/terms feed, a reviewed India MF tax estimator, and richer scheme-name/class and
allotment evidence wiring. Focused tests include stale NAV display and the new missing
tax/cutoff state.
