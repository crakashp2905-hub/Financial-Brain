---
type: component
phase: 2
status: done
resources:
  - "[[Company filings]]"
  - "[[Browser Use]]"
depends-on:
  - "[[C09 Evidence ledger]]"
tags:
  - component
  - phase/2
---

# C11 Document intelligence

**Phase 2** · Filings and reports into structure

Extract concepts, equations, assumptions, indicators, factors, signals, entry/exit rules,
portfolio rules, risk rules, empirical results. **Do not fine-tune on every book** —
store, index, extract, link, preserve citations, make retrievable.

## Resources needed
- [[Company filings]]
- [[Browser Use]]

## Depends on
- [[C09 Evidence ledger]]

---
[[MOC Build]]

> [!success] Built (2026-09-20) - money and dividends, not yet concepts
> `src/financial_brain/docintel/`. Reads the PDF a filing points at (2.78M attachment
> URLs already held) and mints typed claims: order value, tax demand, penalty, amount in
> default, deal value, dividend per share. The brief now says **"order value Rs 117.96
> crore"** where it once said "Receipt of order".
>
> Three precision rules, each forced by a wrong claim in the first run:
> 1. a number must be **named** - a deck's "addressable market demand of Rs 65,000 crore"
>    was minted as a tax demand;
> 2. it must agree with the exchange's **event type**, and an unlisted type mints nothing
>    - a CRISIL release filed as GENERAL became a "fund raise";
> 3. a demand the tribunal **set aside** is relief, not a liability.
>
> Still to build: counterparties, durations, conditions, litigation sections - the
> concept-level extraction this note was originally about.
