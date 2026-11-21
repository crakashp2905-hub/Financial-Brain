# Chart patterns: data limitations (CP-0008)

Recorded from the vault notes, not from new data. Nothing here adds coverage.

| Limitation | Source in the vault | What it means for the pattern module |
|---|---|---|
| Ten years of Indian daily bars with corporate-action adjustment, F&O history with open interest and a participant split, delivery share, and minute bars for a 610-name survivor universe | `brain/07-Research/Chart patterns and candles - what is testable and how.md`, line 78 | The universe is a survivor set. Any pattern statistic from it carries survivorship bias. Treat every rate as conditional on that universe. |
| Research window 2016-04 to 2026-06 throughout | `brain/07-Research/Four edges that replicate, and the chart patterns that do work.md`, line 122 | Results are in-sample for this window. Subsample replication is not out-of-sample evidence. |
| Short exposure only through F&O names; the 2016-21 and 2022-26 halves must each clear abs(t) >= 2 | `brain/07-Research/Chart patterns and candles - what is testable and how.md`, line 95 | Shorting is allowed only with fixture support (`shorting_allowed` in `marketdata.py`). The halves are a study requirement, not a result. |
| F&O eligibility and reference data are a separate component | `brain/05-Components/C28 Reference data and F&O eligibility.md` | Point-in-time F&O membership is not read by the pattern code. CP-0156 stays incomplete until that membership table is read. |
| Book statistics (Bulkowski, Nison) come from US and Japanese sources and are not verified against their pages | `docs/audits/pattern-reference-index.json` | `reference.page_unverified` is true for every catalogue row. Source-quality fields are `UNASSESSED` (CP-0023). |

What is not claimed:

- No row in this file measures market performance.
- No coverage for dates after 2026-06 is assumed.
- The offline demo data (`synthetic_corpus`) is generated, labelled SYNTHETIC, and never stands in for these bars.
