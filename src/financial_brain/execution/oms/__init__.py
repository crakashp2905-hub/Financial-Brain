"""The order management system (ADR-0005). Paper by default; a real broker only behind five locks.

    model       an order and the only transitions it may take
    journal     exactly-once, hash-chained record of every change, in its own file (data/oms.duckdb)
    broker      the broker interface and PaperBroker
    kite_broker KiteBroker: legacy read-only account adapter; all broker writes are disabled
    risk_gate   pre-trade checks every order must pass
    killswitch  stops a real broker: cancels open orders, blocks new ones; only a human resets it
    rollout     paper/readiness stages OFF -> TICKET -> SMALL_AUTO -> SCALED; never enable broker writes
    reconcile   the journal's positions against the broker's, on a timer; a mismatch halts
    slicer      TWAP / VWAP child schedules under a participation cap
    shortfall   implementation shortfall of real fills against the simulator's prediction
    engine      ties them together
"""
