# Chart patterns: user guide

What this is: a local, offline view of chart shapes on bar data. Shapes are descriptions of past bars.
They are not trade recommendations, and nothing here has been shown to make money.

## Ten-minute walkthrough (offline, synthetic data)

1. Scan the demo corpus: `python -m financial_brain.patterns.cli scan`. Output says `NOT_EDGE_CONFIRMED`.
2. List stored records: `python -m financial_brain.patterns.cli list --limit 5`.
3. Read one record's explanation: `python -m financial_brain.patterns.cli explain <event_id>`.
4. Preview a simulated next-session paper intent: `python -m financial_brain.patterns.cli paper-demo`.
   Fills are labelled ASSUMED. Costs are modelled, not observed.
5. Produce the self-contained report: `python -m financial_brain.patterns.demo --offline --out <folder>`.
   Open `report.html`. It makes no network calls and shows the simulation, research-only and K7 banners.

## Reading the labels

- CANDIDATE: the shape was recognisable on a past bar. It may never confirm.
- CONFIRMED: a later close beyond the breakout level occurred. The confirmation bar is recorded.
- FAILED: price closed beyond the failure level before confirmation.
- DESCRIPTIVE evidence: no validation exists beyond the description itself.
- not calibrated: no valid historical labels back any probability, so no probability is shown.
- SIMULATION / ASSUMED: any price, fill or cost comes from a model.

See `docs/PATTERNS-GLOSSARY.md` for every term.

## What the tool will not do

It will not place or route orders, read credentials, connect to the network, promote a shape to an edge,
or state an expected return. Requests for guaranteed profit or zero loss are refused.
