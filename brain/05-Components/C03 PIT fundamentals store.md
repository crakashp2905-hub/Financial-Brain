---
type: component
phase: 0
status: done
resources:
  - "[[Company filings]]"
  - "[[NSE BSE announcements]]"
  - "[[Screener]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/0
---

# C03 PIT fundamentals store

> [!success] Delivered in Phase 0
> See `docs/PHASE-0.md` and `src/financial_brain/`.

**Phase 0** · The only dataset that compounds

> [!important] Start this month
> Snapshot every fundamental with an `observed_at` timestamp and **never overwrite**.
> In three years you own a dataset nobody can sell you.

Recovers partial PIT by keying fundamentals to the **announcement timestamp**, not the
quarter end. See [[Point-in-time fundamentals]], [[D2 Start the PIT store]].

## Resources needed
- [[Company filings]]
- [[NSE BSE announcements]]
- [[Screener]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]

> [!success] Now filled from the filings themselves (2026-09-20)
> `docintel/results.py` + table `financial_results`: quarterly revenue/PAT/EPS parsed
> from each company's results PDF, stored **append-only with `filed_at`**, so a
> restatement is a new row and `as_known_on()` answers what was knowable on a date.
>
> The statement must pass **its own arithmetic** before storage (revenue + other income =
> total income; total income - expenses = PBT), the table is rebuilt from coordinates,
> and labels are matched through OCR damage. Verified: Reliance Q1 FY27 consolidated
> revenue Rs 298,621 cr; Jindal Poly Films Rs 696 cr revenue, Rs 107 cr PAT.
> **Measured recall ~14 statements per 60 results filings** - the remainder are cover
> letters, scans without a text layer, or pages refused for failing their own sums.
>
> This is the answer to [[Point-in-time fundamentals]] that does not require CMIE: it
> cannot recover 2015-2024 as-reported history, but from today it compounds.
