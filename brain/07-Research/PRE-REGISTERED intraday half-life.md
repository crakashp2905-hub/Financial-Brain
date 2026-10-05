---
type: research
tags:
  - research
  - pre-registration
  - signal-half-life
  - intraday
date: 2026-10-05
status: pre-registered
---

# PRE-REGISTERED intraday half-life

Written before computing. Extends the daily half-life (1-120 sessions) down to minutes: for the 300 names with Kite
minute bars (2024-09 to 2026-10), the rank IC of each daily signal, **as of the previous session's close**, against
the return from the 09:30 price to +5, +15, +30, +60, +120 minutes and to 15:15. Signals: 12-1 momentum, nearness to
the 52-week high, low 60-day volatility, 20-day reversal, and the composite.

* **H1** no daily signal has |IC| above 0.01 at 5 to 60 minutes: they are slow signals and the first hours are noise
  to them.
* **H2** the reversal signal has the largest same-day IC of the five (it is the only fast one, half-life 16 sessions).
* **H3** every signal's IC at 15:15 is below its IC at 1 session from the daily study: the information arrives over
  days, not within the opening day.
