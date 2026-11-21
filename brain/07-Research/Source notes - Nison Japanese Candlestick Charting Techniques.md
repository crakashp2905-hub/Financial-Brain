---
type: research
tags:
  - research
  - source-notes
  - technical
  - candles
date: 2026-11-07
status: source notes (paraphrased; not evidence)
---

# Source notes: Nison, Japanese Candlestick Charting Techniques (1991)

Read in full (331 pages) on 2026-11-07. Paraphrased working notes, not quotations. **Everything here is the author's claim
from hand-picked 1989-90 charts (mostly US futures); the book gives no frequencies, win rates, returns or failure counts.**
Under [[ADR-0004 one evidence standard]] that is the lowest tier, *Historical observation*, and nothing below is a trading
rule here until a pre-registered test says so. The synthesis is in [[Chart patterns and candles - what is testable and how]];
what this project has already measured is in [[Charts, candles and indicators]] and [[PRE-REGISTERED chart and candle setups in futures]].

The author's own caveats worth keeping in mind when testing: definitions are subjective; where sources differed he chose the
variant that made the pattern's forecast *more likely to be right* (selection toward the claim); equities need looser gap rules
than futures; signals exist only after the bar closes; candles give no price targets; a reversal signal is a trend-change
warning to be used only in the direction of the major trend.


## Framing (ch1-3)
- Candle = O,H,L,C. Real body = open..close; white/hollow close>open, black/filled close<open. Shadows = extremes (Nison: "extraneous"). Spinning top = small body. Doji = open ~ close.
- Open and close are treated as the emotionally loaded points; close needed to complete a pattern => signals exist only after the bar closes (execution next bar).
- Candles give signals, NOT price targets. Targets must come from other tools (support/resistance, retracements, swing objectives).
- Author is open that definitions are subjective; where sources disagreed he chose the stricter definition "that increases the probability the pattern is right" (selection toward the claim; a bias to remember when testing).
- "Reversal" = trend-CHANGE warning, not necessarily a reversal. Trade a reversal signal only in the direction of the major trend (bearish signal in an uptrend = exit longs, not short).
- Equities open near prior close, so gap-based definitions need loosening for stocks (author's own caveat). Highly relevant for NSE cash equities vs futures.
- Technical analysis rationale he gives: emotion, discipline, self-fulfilling levels, memory of prices. (Not evidence.)
- History: Homma/Sakata, Dojima rice exchange 1700s; "Sakata's rules". Anecdotal.

## Single-candle lines
- Hammer (after downtrend) / hanging man (after uptrend): small body in the upper part of range, lower shadow >= 2x body, little/no upper shadow; colour minor (white hammer / black hanging man slightly stronger). Hanging man NEEDS bearish confirmation (next open below its body, or a black lower close). Longer lower shadow / smaller body = better. Needs a trend (even minor) before it.
- Shooting star: small body at the low end, long upper shadow, after an uptrend; ideal has body gapping above prior body but gap not required; failure at resistance strengthens. Inverted hammer: same shape after a downtrend; needs bullish confirmation (next open above its body / white higher close). Shape in mid-range with no trend = ignore.
- Belt hold: bullish = long white opening on/near its low (shaven bottom); bearish = long black opening on/near its high. Longer & rarer = stronger. Close beyond it in the opposite direction negates.
- Doji (ch8, to read): treated as the key indecision signal.

## Two-candle reversals
- Engulfing: clear prior trend; 2nd real body engulfs 1st real body (shadows irrelevant); opposite colours (exception: tiny/doji first body). Stronger if: first body tiny and second long; after a long or fast move; heavy volume on second bar; engulfs more than one body. Nison claims it can flag reversals that a Western "reversal day" (needs new high/low) would miss.
- Dark-cloud cover: uptrend; long white, then black that OPENS ABOVE the prior HIGH and closes below the white body's midpoint (some require >50%; deeper penetration = stronger). Stronger if opens above multi-month highs and fails, at resistance, heavy open volume. Variation: opens at/above prior close (weaker). A long white closing above its high negates.
- Piercing pattern: mirror (white opens below prior LOW, closes above the black body midpoint). Stricter than DCC because on-neck / in-neck / thrusting (white closes less than halfway into the black body) are read as bearish continuation in a downtrend.
- Harami: small body inside prior LONG body (opposite of engulfing); colour of 2nd irrelevant; weaker signal ("brake", lull, usually trend -> sideways rather than reversal). Harami cross (2nd is a doji) = stronger, esp. at tops. Western inside day ~ but defined on bodies, not range.
- Tweezers tops/bottoms: two+ bars (consecutive/nearby) with matching highs (top) or lows (bottom); weak alone, useful as confirmation of other patterns; matters more on weekly/monthly.
- Counterattack lines: opposite-colour candles with the same close; 2nd opens sharply in trend direction (gap) then closes back at prior close. Weaker than piercing/DCC (doesn't enter the body).
- Upside-gap two crows: after a long white, small black gaps above, second black opens higher and closes below the first black's close (inside the first black). Bearish; stop = close above the second black's high. Mat-hold = bullish continuation relative (3rd/4th small blacks then a white that gaps up / closes above).
- Three black crows: three consecutive long blacks, each opening within the prior body and closing near its low, after an advance; "identical three crows" (each opens at prior close) = rarer and more bearish.
- Three advancing white soldiers (ch7): bullish counterpart / continuation. (to read)

## Three-candle stars
- Star = small body gapping away from the long body before it (body-to-body gap; shadows may overlap). Morning star (bottom): long black, small star gapping down, white that closes well into the black body. Evening star: mirror. Second gap (star to third) is rare and not required. Depth of third bar's penetration matters. Volume: light on bar 1, heavy on bar 3 = stronger. Differs from a Western island (needs only a BODY gap, not a range gap). Doji star versions (evening/morning doji star) are stronger; confirmation needed because a next-bar gap WITH the trend negates it. Abandoned baby = doji star gapped on both sides with shadows not touching; extremely rare.
- "Three-river" morning/evening star historical naming. Unique three river bottom (rare) and modified three river bottom = inverted head & shoulders.

## Multi-bar tops/bottoms
- Three mountain top / three river bottom = triple top/bottom (needs bearish/bullish candle on the last test; for bottoms, close above the peaks of the troughs confirms). Three Buddha top = head & shoulders (centre highest); inverted three Buddha = inverse H&S. Nison notes Japanese equivalents predate Western H&S, and that Japanese stress triples (number three culture) while a Westerner would accept double or quadruple tests too.
- Dumpling top (rounded top w/ small bodies, confirmed by a DOWN window) and fry-pan bottom (rounded bottom confirmed by an UP window) = rounding tops/bottoms with gap confirmation.
- Tower top/bottom = V/spike reversals: long candles into and out of the turn.

## Windows (gaps) and gap-based continuation
- Window = gap between the prior bar's extreme and the current bar's extreme (range gap). Moves "go in the direction of the window". Windows act as support (up window) / resistance (down window); corrections tend to return to the window; if the window is closed AND pressure persists beyond it, the trend is void. Windows out of congestion or from new highs matter more. Old support -> resistance (polarity) stacks with windows.
- Rule of three: after ~3 windows in one direction a turn is likely with a candle reversal sign. "Record highs/lows": after ~8-10 consecutive new highs/lows without a real correction a significant correction is likely ("stomach 80% full").
- Upside-gap tasuki: white gaps up, black opens in the white body and closes below its body but inside/above the gap; buy at close; invalid if the gap fills with pressure. High-price gapping play: after a sharp 1-2 bar advance, a cluster of small bodies, then an UP gap = buy; low-price gapping play mirror (sell). Gapping side-by-side white lines, rising/falling three methods, separating lines: ch7 (to read).

## Testability notes (for this project)
- Every definition above is OHLC-only and can be made causal. Free parameters Nison leaves open: what counts as "trend", "long/short body", "small body", "twice", "near the low", penetration %. Any test must fix these in advance (ADR-0004), once, as one family.
- Gap-based patterns (windows, stars, DCC/piercing openings) behave differently on NSE cash stocks (small overnight gaps, circuit limits, T+1 open auctions) than on 1990 US futures.
- Nison's own examples are hand-picked charts from 1989-90 US futures/stocks: selection bias, no frequencies, no failure rates. He gives NO statistics anywhere so far.

## Continuation patterns, doji, confluence, trendlines (ch7-11)
### Continuation patterns
- Gapping side-by-side white lines: in an uptrend, an up-gap white followed by a similar-size white with about the same open = bullish continuation (rare); a DOWN-gap pair of whites in a downtrend is read as short covering = bearish continuation (rarer). Down-gapping black followed by a lower-closing black = normal continuation.
- Rising three methods: long white; then 2-4 (ideally 3) small bodies drifting down that stay inside the first bar's whole range (shadows included), any colour; final strong white opens above the prior close and closes above the first bar's close. Resembles a bull flag. Falling three methods = mirror (a bear flag). Volume: expansion on the long bars, contraction on small ones. Do not act until the pattern is complete.
- Three advancing white soldiers: three whites with higher closes, each opening inside/near the prior body and closing near its high; strongest from a low base after stable prices; if extended = overbought caution. Advance block = the 2nd/3rd soldiers weaken (smaller bodies, longer upper shadows); stalled/deliberation = two long whites then a small white (star or "riding the shoulder"). Use to protect/liquidate longs, not to short; matter more at high prices.
- Separating lines: opposite-colour bar with the SAME OPEN as the prior bar, in trend direction, = continuation (bullish: after a black in an uptrend, a white opens at that black's open and closes higher, ideally a bullish belt hold).
- Upside-gap tasuki: white gaps up, black opens in the white body and closes under it but stays above the gap; buy the close; void if the gap fills with pressure. Rare.
- High-price gapping play: after a 1-2 bar sharp advance, a cluster of small bodies near the high (should not last > ~11 sessions), then an UP gap = buy. Low-price gapping play mirror = sell. Even the Japanese user in his example acted on an incomplete (no real gap) version: subjectivity.
- Windows (see above). Rules: window in direction of trend continues it; corrections return to the window; closing the window then continued pressure = trend void; 3 windows -> turn near; ~8-10 record highs/lows -> correction likely.
### Doji chapter (ch8)
- Doji = open ~ close (within a few ticks); judge "near-doji" RELATIVE to recent bars (if many small bodies recently, a new one is not special). Doji lose meaning on very short intraday timeframes (<30 min) because most bars become doji.
- Doji at tops (esp. after a long white bar in an uptrend, or at an extended move) = strong warning; doji in downtrends are weaker and need more confirmation (a falling market can continue under its own weight). Confirmation (next bar) raises success; trade-off between risk and reward of waiting.
- Long-legged doji (long upper and lower shadows) = irresolution, good at tops; rickshaw man = long-legged doji with open/close mid-range; high-wave line = small body with very long shadows (cluster = reversal). Gravestone doji (open=close=low, long upper shadow) = specific shooting star, bearish at tops, forte is tops; stronger the longer the upper shadow and the higher the price. Doji can leave support/resistance zones. Tri-star = three doji, middle one a doji star: very rare, strong.
### Ch9: visual practice charts; interpretations are subjective. Confluence examples (ch10): multiple candle signals at the same price area (e.g. hanging man + doji + shooting star at a prior high) raise importance; an area of such a confluence becomes support/resistance later.
### Part 2: "Rule of Multiple Techniques" (Sklarew): the more independent indicators agree at a price area the better the forecast. Nison's examples: a target/resistance zone derived from 4 sources (trendline, double-bottom measured move = height between lows added to the intervening high, old support turned resistance, 65-day MA, Fibonacci retracement, Elliott 4th wave limit). Always define the price at which the view is wrong (market is never wrong); trade WITH the trend; stops placed at entry, protective stop below the signal bar's low; "lose your opinion not your money".
### Ch11: trendlines: support line = >=2 rising reaction lows; resistance line = >=2 falling reaction highs; strength = number of tests, volume at tests, duration. Candle signals AT a line (hammer at 3rd test of support in uptrend; DCC/shooting star at resistance; piercing at a downward-sloping support while major trend is down = cover shorts, not buy) are the preferred setups. Upward-sloping resistance and downward-sloping support lines: used to defend positions, not to initiate.

## Retracements, moving averages, oscillators, volume, Elliott, Market Profile, options, hedging (ch12-20)
- Springs and upthrusts (Wyckoff idea): markets are non-trending a large share of the time (he cites up to ~70%). In a range, an UNSUSTAINED break of the range high that closes back inside (upthrust) = short, stop above the new high, target = the opposite end of the range; spring (break of low that closes back above) = long, stop below the spring low, target = top of the range. Advantage: a clear stop and a clear target. Stronger when the failing bar is itself a shooting star / hammer. A close beyond the level for a few days ("beachhead") negates it and the old level flips polarity.
- Change of polarity: old support -> resistance and vice versa; strength rises with the number of tests and the volume/OI at them; rationale given = the people who bought at a level and are then stopped or underwater use rallies back to it to exit. (Plausible behavioural story; not tested.)
- Retracements (ch12): 38%, 50%, 62% of the prior swing; expect the turn candle signal near those levels (50% most watched). Always combine with prior support/resistance and a candle sign.
- Moving averages (ch13): SMA/WMA/EMA; MACD = EMA12 - EMA26 with a 9 EMA signal; favourites 65-day MA as "broad spectrum" (author's preference), 13/40-week MA popular in Japan; golden cross / dead cross = short MA crossing above / below the long one; price minus 5-day MA as a short-term overbought/oversold gauge (e.g. 4 cents in copper); widening/narrowing of the MA spread as momentum confirmation/divergence. MA is trend-following and lags; best in trends.
- Oscillators (ch14): RSI (9, 14; 70/80 overbought, 30/20 oversold), stochastics (%K 14/21 smoothed, %D; buy = oversold <=25 + positive divergence + %K crossing above %D), momentum (close - close N bars ago, 10-day). Uses: divergence, overbought/oversold (mostly in lateral markets), momentum confirmation. Only take oscillator signals in the direction of the dominant trend (use the opposite signal to exit, not to reverse). Candle signal + divergence = earlier than waiting for the oscillator's own confirmation.
- Volume/OI (ch15): rising volume confirms trend; light-volume retest of a high = bearish (double top evidence), of a low = bullish. OBV: trend confirmation, accumulation (OBV up, price flat at lows) / distribution. "Tick volume" = number of trades per period (intraday proxy). Open interest: rising OI with price trend = trend strengthens; falling OI in a rally = short covering, rally vulnerable; very high OI at new highs = fragile (stop cascades, trapped longs). [Relevant: NSE F&O OI + participant data are in the lake.]
- Elliott (ch16): 5-wave impulse (1,3,5), 3-wave correction; waves 3 and 5 most tradeable; Fibonacci targets (wave 3 ~1.618 x wave 1; wave 4 retraces 38/50% of wave 3; wave 4 should not overlap wave 1's range); candle signals used as confirmation of count termination points. He admits counts are subjective until the wave is finished.
- Market Profile (ch17): TPO half-hour letters, initial balance (first hour), range extension, value area (70% of volume), price probe -> either rejection (back into value) or acceptance (value relocates); initiating vs responsive buying/selling relative to prior day's value area; "extremes" (single prints). Narrowing value area while price rises = poor trade facilitation (parallels advance-block/stalled candles). Needs tick/TPO data.
- Options (ch18): vol is the key input; candle reversal signals can time selling vol (after a strong trend, harami/counterattack = truce) or buying vol (at a range with low vol and a convergence of reversal signals); works better vs historic than implied vol by his experience; options as limited-risk way to try picking tops/bottoms. Hedging (ch19): use reversal candles to time/scale hedges (raise hedge ratio on bearish reversals for a producer).
- Ch20 examples: his own trades: Western structure first (trend, neckline retest of H&S, old support as resistance), candle as the trigger, explicit stop and target from structure; hammer in a downtrend = cover shorts not buy; one trade shown lost (stopped at breakeven then fell). Conclusion: market "personality" differs (dark-cloud covers in daily crude, hammers in weekly copper) - i.e. he suggests tracking which patterns work in YOUR market (this is the in-sample-selection trap unless done out of sample).
- Glossary A repeats definitions; a few extra names: homing/other not present in this edition; "three gaps", "eight/ten new records", "blow-off", "morning/night attack".
### What Nison does NOT give: any frequency, win rate, average move, holding period, or loss statistics. Every claim rests on hand-picked 1989-90 chart examples (mostly US futures), with definitions chosen to favour the pattern. Evidence level in this project's terms: Historical observation (author's, unverified).
