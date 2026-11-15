# PE-0072 market-exposure ceiling and correlated books

Added `aggregate_correlated_books()` to sum signed factor exposures across all named strategy books before applying account-level limits. It checks absolute aggregate market-factor exposure and portfolio volatility from the combined exposure vector and the aligned positive-semidefinite covariance matrix. Correlated books therefore contribute together to total modeled risk; an individually acceptable book can still be rejected when the combined portfolio breaches a ceiling.

The task remains `PARTIAL`: the aggregator and deterministic correlated-book tests exist, but the paper session/OMS does not yet require all strategy adapters to register their factor exposures in this common account-level gate. No real covariance or portfolio risk result is claimed.
