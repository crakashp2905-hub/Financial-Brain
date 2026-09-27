"""Risk as a computed constraint, separate from whether an opportunity is any good.

Two questions that get confused constantly:

    Is this opportunity attractive?        -> decisions/expected_value.py
    Can the portfolio afford it?           -> here

The second is not a weaker version of the first. A theoretically excellent trade must still be
refused when the book already carries the same bet, cannot exit the size, or has no risk budget
left. ``decisions/safety.py`` already answers the *per-name* half of this - staleness, crisis
regime, liquidity against ADV, promoter-group concentration, drawdown - and those checks stay
where they are. What this package adds is the part that needs the **rest of the book** to
answer: correlation with what is already held, factor and group exposure in aggregate, what a
shock does to the whole portfolio, and a switch that stops everything.

Every limit here is a module constant, versioned with the code. Changing one is a code change
with a reason in the commit, never a parameter tuned until a trade fits.
"""
