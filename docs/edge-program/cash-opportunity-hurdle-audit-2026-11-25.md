# Cash return hurdle in next-capital allocation

Date: 2026-11-25

`portfolio.next_capital.allocate` and `compare` now require an explicit `cash_return`
for the same decision horizon as candidate expected net returns. Each allocation step
compares candidate net return above that cash baseline with its marginal portfolio risk
penalty; if no candidate's marginal utility is positive, the amount remains in cash. The
allocation result reports the cash counterfactual return, retained-cash return, opportunity
cost of invested capital, expected excess return versus all cash, risk penalty, and resulting
portfolio utility versus all cash. The trader terminal now shows the cash hurdle and net
return over it beside candidate comparison and the next-capital proposal.

Verification: `tests/test_phase5_trader_core.py` reports 9 passed, including zero-return
control compatibility, a higher cash hurdle retaining the full budget, and opportunity-cost
accounting. Ruff and targeted mypy pass.

**Status: partial.** The terminal explicitly uses a zero-return cash control because it has
no source-backed horizon-matched cash yield wired in. Integrating permission-cleared liquid
cash alternatives, taxes, payout timing, and forward-paper evidence remains outstanding.
