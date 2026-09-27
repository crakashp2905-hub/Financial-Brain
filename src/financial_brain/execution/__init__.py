"""What an order would actually have got, walked through the volume that actually traded.

Every backtest in this project so far has assumed a fill. The cross-sectional tests fill an entire
quintile at the adjusted close; the intraday systems fill at `max(trigger, bar_open)`, which is
conservative about gaps and still assumes the whole size arrives at one price. Neither is a lie -
they are stated - but both hide the same thing: **an order competes with the volume that was there**,
and at size the price you get is not the price you saw.

That gap is where a good backtest goes to die, and it is normally modelled with an assumed spread
and an assumed impact coefficient. This project has something better: **18.45M minute bars for 100
names**, which means the fill can be *measured*. To buy a given rupee amount, walk forward through
the minute bars consuming a bounded share of each minute's real traded volume, and report the
volume-weighted price that actually accumulated.

No spread model, no impact coefficient, no calibration. The only parameter is the **participation
rate** - what share of a minute's volume an order may take - and that is a policy choice a trader
makes, not a market constant to be fitted.

## What it produces that an assumed fill cannot

``shortfall_bps``   the implementation shortfall: the decision price against the achieved average.
                    This is the number that decides whether an edge survives, and it is the one
                    no assumed fill can produce.
``minutes``         how long the order took. A three-minute fill and a ninety-minute fill are
                    different trades even at the same average price, because the second one is
                    exposed to news for an hour and a half.
``unfilled``        what could not be bought before the session ended. This is capacity measured
                    rather than estimated, and it is the honest answer to "how much can this
                    strategy hold".

## What it still does not model, stated plainly

**The spread.** These are traded prices, so the volume-weighted average of the minutes an order
walked through includes whatever the trades printed at - but a *marketable* order pays the far side,
and a minute bar does not say where the bid and offer were. The one attempt to estimate spreads
directly on this data (Corwin-Schultz 2012) produced numbers tenfold too large and was declared
unusable, so this is missing rather than approximated, and every number below is optimistic by that
unmeasured amount.

**Own impact.** Walking through historical volume assumes the order did not move the price it was
walking through. For small participation rates that is roughly true and for large ones it is not,
which is why the participation rate is reported with every fill rather than buried in a default.

**The auction.** The opening and closing auctions are separate mechanisms with their own liquidity,
and minute bars do not distinguish them.
"""
