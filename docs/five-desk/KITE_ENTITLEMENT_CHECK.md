# Kite access and options-quote entitlement (FB-1404), 2026-11-18 19:2x IST

I ran a read-only probe with the owner's daily session token through `providers.kite_data`. No
order endpoint was called.

| Check | Result |
|---|---|
| `quote(full)` for NSE index and equity | OK, 2 instruments in 0.61 s |
| NFO instrument list | OK, 2100 NIFTY CE/PE contracts listed |
| `quote(full)` for 200 nearest-expiry NIFTY options | OK in 0.14 s; OI and 5-level depth present |
| Max symbols per quote call (client limit) | 500 |
| 5 consecutive `ltp` calls of 50 options | 1.6 s total (client throttle, about 3 calls/s) |

Conclusion: the account is entitled to options-chain quotes with OI and depth. The figures above
are measured, not assumed. Re-run this check after any plan or subscription change. Rate limits
are enforced by `_throttle()` and are not hard-coded anywhere else.
