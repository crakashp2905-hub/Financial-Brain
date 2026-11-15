# PE-0063 turnover-aware no-trade band

The target portfolio optimizer now applies a per-name weight band after its scheduled, risk- and turnover-cost-aware optimization. Proposed changes at or below the configured band are snapped back to current weights, leaving the difference in explicit cash. A material rebalance remains bounded by the existing one-way turnover cap. `TargetPortfolio.rebalance_triggered` distinguishes a scheduled evaluation from an actual target change.

The default band is 0.005 of NAV weight (50 basis points per name) and is configurable through the target planner and shared signal allocator. This prevents small target drift from causing orders solely to restore exact weights after fees or marks move NAV.

The default is an engineering guardrail, not an empirically optimized parameter. A production choice should be frozen using development-only observations and assessed on untouched validation sessions with actual costs and portfolio marks.
