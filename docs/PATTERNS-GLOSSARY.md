# Chart patterns: glossary

- **Candidate**: a shape recognisable from bars up to and including the candidate bar. Kept even if it never confirms.
- **Confirmed**: a later close beyond the breakout level. Recorded with its bar index.
- **Failed**: a close beyond the failure level before confirmation.
- **Pivot**: a local high or low confirmed only after its right-hand bars have arrived (`known_at_index`).
- **Pivot confirmation**: a pivot becomes usable only once its right-hand bars have arrived. Before that it does not exist for the detector.
- **Known-at**: the bar index from which a fact could have been used. Never earlier than the bar it describes.
- **Tolerance policy**: an explicit unit (ATR multiple, percent or ticks). Units are never mixed.
- **Pending definition**: a rule still described only in prose (for example tasuki gaps). Excluded from inference.
- **Ambiguous bar**: one bar whose range spans both a stop and a target. Resolved as AMBIGUOUS, never optimistically.
- **Evidence tier**: DESCRIPTIVE, HISTORICAL_REPLICATED, FORWARD_PAPER or EXTERNALLY_CONFIRMED. Detectors emit DESCRIPTIVE only.
- **Not calibrated**: no valid labelled history supports a probability.
- **Simulation**: prices and fills produced by a model. Labelled SYNTHETIC or ASSUMED.
- **Throwback / pullback**: a return toward a breakout level after confirmation. Not guaranteed; not modelled as a signal.
- **Ultimate-high bias**: the distortion from judging a pattern by its best price in hindsight. It is why ultimate-high or low exits are never permitted.
- **Ultimate high/low exit**: an exit chosen with hindsight. Not permitted in any study.
- **Protected record**: a source note, catalogue or trial manifest pinned by hash. Must never change.
- **K7**: the earlier candlestick-futures experiment that failed its preregistered G3 gate. Its result stands.
