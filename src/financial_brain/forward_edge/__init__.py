"""Forward trading-edge service: execution modes, approvals, trial freeze, charters, custody and audit.

This package holds the *policy* layer of the forward-edge program (docs/forward-edge/TRACKER.md). It does not
own economic truth: the paper OMS and ledger live in ``financial_brain.kernel``. Nothing here sends an order.
"""
