# PE-0064 position and sector diversification

The scheduled target optimizer now accepts an explicit maximum number of positions and an optional frozen instrument-to-sector mapping with a maximum sector weight. Names are ranked deterministically by forecast net alpha (ties break by symbol); existing holdings occupy position slots, and additional slots go to the highest-ranked names. Any concentration removed by name or sector limits remains in explicit `CASH` rather than being redistributed into another exposure.

The shared signal allocator forwards the same controls. Missing, extra, or blank sector labels are rejected when a sector map is supplied. A prior book that already violates the requested position or sector limits fails closed at a scheduled rebalance. Turnover-constrained rebalance interpolation retains existing names in the position budget, so it cannot reintroduce disallowed positions.

The defaults (`max_positions=20`, sector cap 0.30) are configurable engineering safeguards, not India-market calibrated values. Sector quality depends on the supplied mapping. Synthetic tests verify concentration limits and explicit cash; they do not establish prospective performance, optimal limits, or profitability.
