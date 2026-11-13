# PE-0070 inventory-aware sell rules

The shared signal allocator now treats `HOLD` and `NO_SIGNAL` as zero-score observations, so an optimistic forecast by itself cannot open new inventory. A `SELL` signal on an unheld instrument leaves it flat; a sell against a held position reduces the target through the existing long-only, turnover-constrained optimizer. The target planner preserves incumbent weights inside the frozen no-trade band and computes turnover costs from actual target-weight changes. The account fee schedule posts charges only for completed fills.

Acceptance is covered with deterministic fixtures, including a held position under `HOLD`, an unheld `SELL`, zero turnover cost for unchanged inventory, and the existing paper-account test for effective-dated fill fees. This validates the rule mechanics; it does not establish that the signals are profitable or that live execution will match the simulator.
