"""Why did this move? Decomposed into named parts that add up, with the residual shown.

"XYZ fell 7% today" is an observation. "XYZ fell 7%: the market took 1.2, its peers took 2.1,
and 3.7 was the company" is an explanation, and it is the difference between a terminal and a
price feed.

Everything here is a **decomposition of a single move into components that sum to it**. There is
always a residual and it is always reported, because a decomposition that explains exactly 100%
of every move has a free parameter somewhere.
"""
