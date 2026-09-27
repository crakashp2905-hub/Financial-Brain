"""Candidate generation that carries its own track record, and refuses to invent one.

The usual opportunity engine scans for "dislocations" and emits candidates. That design has a
defect this project is unusually well placed to see: it produces a stream of confident-looking
candidates whose underlying conditions have **never been measured**, and a downstream committee
then writes a thesis around whichever one it finds most interesting. The engine becomes a
random-number generator with a vocabulary.

So this one inverts the requirement. A condition may only be screened for if the trial ledger has
a measurement of it, and every opportunity it emits **carries that measurement**: the t-statistic,
the Bonferroni bar it was tested against, the verdict, and the separation from its null. An
opportunity is therefore not a suggestion. It is a named condition, a name it currently holds in,
and the honest historical prior for that condition - which for almost everything in this archive
is *rejected*.

That is a useful object even when the prior is bad. It tells a human "this is what is unusual
today, and here is exactly how little that has been worth historically", which is a better input
to judgement than either silence or false enthusiasm.

## Status, and why nothing here reaches a decision

    DISCOVERED -> SCREENED -> PRIORED -> (dropped | forwarded)

``forwarded`` hands the opportunity to ``decisions/compile.py``, which owns every action. Nothing
in this package can produce a BUY, a weight, or a thesis. It finds and ranks; the compiler
decides, and the portfolio gate says whether the book can afford it.
"""
