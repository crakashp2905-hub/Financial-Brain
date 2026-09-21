---
type: component
phase: 2
status: done
depends-on:
  - "[[C16 Investment Committee]]"
  - "[[C23 Strategy registry and paper trading]]"
tags:
  - component
  - phase/2
  - validation
---

# Replay harness

**Phase 2** · Make decisions that can be scored, without waiting a year

> [!success] Built
> `src/financial_brain/evaluation/replay.py` (`fb replay`), with
> `decisions/promote.py` walking each draft to paper.

Builds the world state **as of a past session**, runs the [[C16 Investment Committee]] on
companies that filed something material that day, walks the draft to paper and lets it
close at its horizon against prices already held.

What keeps it from being fiction:
- the world state carries that session's `as_of`, so only filings published by then are in
  it;
- `record.advance` refuses evidence published after that `as_of` - the no-hindsight rule
  lives in the decision contract, not in the harness;
- entry is the first adjusted close **on or after** the decision; exit is the close at the
  horizon. The same rule a live trade gets.

What it is **not**: a strategy backtest. The universe is "companies that filed something
material that day", the model is today's model reading old text, and the sample is small.
It measures whether this machinery makes money, not whether a strategy does.

A session with no trading (2026-01-15, Makar Sankranti) yields no candidates rather than
a trade at a price that never existed.

Related: [[First measured record]] · [[Own-record scorecard]] · [[Learning from losses]]
