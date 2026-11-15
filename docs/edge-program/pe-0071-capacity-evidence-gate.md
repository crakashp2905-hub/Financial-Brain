# PE-0071 liquidity participation and notional gate

Added `assess_order_capacity()` on top of the existing ADV/depth capacity formula. It requires positive dated ADV with an evidence hash, fresh non-future displayed depth with an evidence hash, an order timestamp, a participation cap, and a safety factor. It computes the lower of the ADV participation amount and displayed depth, then applies the safety factor. Missing or stale evidence and orders above the permitted notional return `NO_TRADE`; the function does not silently resize a strategy order.

The task remains `PARTIAL`: the reusable risk function is tested, but the live-paper session runner and all strategy adapters do not yet obtain and enforce this evidence from an independently configured liquidity provider. No real ADV, depth, or capacity is claimed.
