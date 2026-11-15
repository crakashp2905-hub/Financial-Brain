# Failed parameter reuse guard (PE-0046)

`StrategySpec.parameter_sha256` records a canonical hash of the strategy's parameter object. `Registry.register()` refuses that hash when it already belongs to a retained FAILED strategy, including a failure later retired through the append-only status history. Renaming the strategy ID or family does not bypass the guard. New parameter content receives a distinct digest.

The existing 13-trial historical ledger remains preserved and maps to FAILED registry entries. Those rows do not contain canonical parameter objects or parameter hashes, so the new duplicate guard only applies where exact parameter identity has been recorded. No hashes are reverse-engineered from names or outcome summaries.
