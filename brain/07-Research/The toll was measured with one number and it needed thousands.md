---
type: research
tags:
  - research
  - costs
  - method
date: 2026-09-27
---

# The toll was measured with one number and it needed thousands

Every cost charged in this project came from one line:

```python
cost = CostModel().round_trip(turnover=1_000_000, bucket="mid")["bps"] / 10_000
```

One bucket. One segment. Applied to whatever a strategy happened to hold, for eighty-six
trials. It is wrong in **both directions at once**, and which direction depends on where the
signal's holdings sit in the liquidity ranking — which is a property of the signal, not a
parameter anyone chose.

## The big error: charging a settlement that never happens

Every system in `evaluation/intraday.py` squares off inside the session. In India that is
the **MIS** segment — STT is 2.5 bps on the sell leg only. The code charged **delivery**:
10 bps on *both*. It also charged the `mid` impact bucket (25 bps a leg, names ranked
301–750 by turnover) to a name set that is **62% mega and 38% large** and contains no mid
at all.

Delivery/`mid` is 70.87 bps. MIS at that book's own mix is nearer **17**. h15's headline —
"the round trip is 7x to 25x the edge" — was pricing an instrument the strategies never
traded.

## The small error: every daily book was charged slightly less than it costs

Costed per rebalance on that date's own national turnover rank:

| signal | mega | large | mid | small | micro | its own cost | vs flat 0.709% |
|---|---:|---:|---:|---:|---:|---:|---:|
| `dist_52w_high` | 16.8% | 26.0% | 35.5% | 18.4% | 3.3% | 0.725% | −2% |
| `vol_60` (low vol) | 15.6% | 22.8% | 32.2% | 24.6% | 4.9% | 0.797% | −11% |
| `above_ma50` | 11.2% | 21.0% | 38.4% | 25.5% | 3.9% | 0.808% | −12% |
| `mom_12_1` | 9.9% | 20.5% | 40.0% | 26.5% | 3.0% | 0.816% | −13% |
| `above_ma200` | 11.3% | 20.7% | 37.3% | 26.9% | 3.8% | 0.822% | −14% |
| `adx_14` | 9.7% | 19.1% | 38.4% | 28.3% | 4.4% | 0.847% | −16% |
| `ret_20d` (reversal) | 7.7% | 16.1% | 37.6% | 33.7% | 4.9% | 0.900% | −21% |
| `rsi_14` | 7.6% | 15.7% | 37.0% | 34.5% | 5.2% | 0.915% | −23% |

The stateful time-series books are in the same band, 0.754–0.808%, because their liquidity
filter already keeps them out of the micro tail.

**The flat charge was optimistic for every signal, by 2% to 23%**, and optimistic in the
strategies' favour. `above_ma200`'s cost gate moves from −0.2066% per period at 0.709% to
the same rejection at 0.822%; `bollinger_reversion_20_2` moves from t = −7.26 to −8.16. No
verdict in the ledger changes, and the daily correction makes rejections firmer rather than
reversing any.

## The ordering is the part worth keeping

**Signal families differ systematically in what their books cost**, and the spread is
wide enough to matter: 52-week-high proximity holds a book 30 bps a round trip cheaper than
RSI does. Reversal and oscillator signals concentrate in the illiquid tail — 34% small for
`rsi_14` against 18% for `dist_52w_high` — because that is where short-horizon noise is
largest. Trend and proximity-to-high signals sit toward the liquid end.

That is a cost-side reason to prefer some factor families over others *before* measuring
any edge at all, and it came out of a bug fix rather than a search.

## Two bugs inside the bug fix, both mine

**Ranking inside the backtest's own universe.** The impact tiers in `costs/india.py` are
**absolute national ranks** — top 100 is mega — calibrated against the full ~3,660-name
list. With 1,200 eligible names, 62% fall inside rank 750 and get charged `mid` or better
when nationally most are `small`. The filtered ranking manufactured a liquid book out of an
illiquid one and made the correction look like it changed nothing.

**A single snapshot applied to eleven years.** The first measurement ranked every holding by
turnover on 2026-09-18, then applied that ranking to books held from 2016 onward. A name
liquid in 2018 and illiquid now gets charged as illiquid throughout, and the universe grew
over the period, so everything old sank. That produced costs of 1.05–1.45% and an apparent
33–51% error — roughly **double the truth**. It was look-ahead in the cost model, which is
precisely the failure the fix's own docstring warns about, committed while writing it.

`costs/book.py` now ranks over `universe_snapshots` — the exchange's own list — **per
date**, by the turnover observed on that date, so the charge comes from a ranking the
strategy could have read that morning.

## The sixth measurement artifact, and the first in the cost model

[[Running the strategies on noise]] counted five: a benchmark returning 84% a year, a spread
estimator off tenfold, candlestick patterns at t = 6 that were bid-ask bounce, a session
median nobody could buy, a liquidity ranking putting a two-session IPO above HDFC Bank.
This is the sixth, and it is the first to live in the **cost model** rather than the signal
— which is why four rounds of asking "why is the edge so small?" never found it.

The synthetic null catches a harness that manufactures edges. It does not catch a harness
that prices them wrongly, because the null pays the same wrong price. Nothing in the
firewall was going to find this; it took asking what a *book* pays rather than what a
*strategy* scores.

Related: [[Turnover explains 97% of it]] · [[The cost number that decided everything]] ·
[[Intraday breakouts pay seven times their edge]] · [[Running the strategies on noise]] ·
[[Alpha Validation Firewall]]
