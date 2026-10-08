---
type: research
tags:
  - research
  - pre-registration
  - edges
  - news
date: 2026-10-08
status: pre-registered
---

# PRE-REGISTERED bad news keeps going

Written on 2026-10-08 after seeing two information-only lines in the run of [[PRE-REGISTERED edges for every mode]],
before anything below is computed. In the 500 most liquid names:

* losers *with* material news (bottom decile of 5-session market-adjusted return, news in those sessions) went on
  to lose -0.67% a week net against the 500 (t -5.0 in 2016-21, -5.2 in 2022-26);
* names with the worst reaction to their results (bottom quintile) lagged by -1.8% / -1.4% over the next 20 sessions
  net (t -2.9 / -2.6).

Both were seen, so they prove nothing on that sample. This tests whether they are a phenomenon (Chan 2003: news
drives continuation, no-news moves reverse) or a fit.

## Test on names this study never touched

The names ranked **501-1000** by 20-day turnover each day. Same definitions, same news types, same windows:

* **N1** Weekly news losers: their 5-session **gross** return in excess of the 501-1000 mean (gross, because the use
  is avoiding them, not trading them).
* **N2** Bottom-quintile results reaction: the 20-session gross excess from the close after the reaction.

**Pass**: mean excess negative with t <= -2 in **both** 2016-2021 and 2022-2026, for each of N1 and N2 separately.

## Use, if it passes

* A pre-trade **avoid filter** for the long books (learned, momentum, ranking v2, swing): a name flagged by N1 in the
  last 5 sessions or by N2 in the last 20 is skipped. Applied to new entries only, from the next evening.
* A **news-loser short** paper strategy in the zoo on F&O names through stock futures (10 bp round trip), judged on
  its forward record only (its in-sample numbers are contaminated).

If either fails it is recorded and nothing is used.

## Predictions

* **B1** N1 passes (news continuation is documented across markets).
* **B2** N2 passes in the earlier period but not with t <= -2 in 2022-2026.

Information, not advice.
