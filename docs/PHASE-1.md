# Phase 1 — Perception (in progress)

**Goal:** the system knows what is happening and can say why it matters.
**Exit test:** a cited daily brief you would actually read before the market opens.

Plan: [BUILD-FLOW.md](BUILD-FLOW.md) §3. Live checklist: [HANDOFF.md](HANDOFF.md).

| Step | Component | State |
|---|---|---|
| P1-1a | C06 · BSE corporate-action feed (Tier 1) | **done** |
| P1-1b | C06 · BSE announcements + event classification | **done** (history backfilling) |
| P1-2 | C07 · Market Regime Brain | **done** (v2) |
| P1-3 | C09 · Evidence ledger | **done** |
| P1-4 | C08 · World state | **done** |
| P1-5 | C10 · Daily brief | **done** (deterministic; LLM prose and portfolio owner-gated) |

---

## P1-1a — BSE corporate-action feed

Phase 0 closed without an authoritative corporate-action source: NSE's sits behind a
browser-session wall, so actions were *derived* from prices. BSE's
`api.bseindia.com/BseIndiaAPI/api/DefaultData/w` needs no session and returns every action
in a window with scrip code, ex-date, record date and a free-text purpose.

`fb corpact-feed --start 2015-01 --end 2026-09`:

```
141 months, 23,625 feed rows
recorded   22,274 reported actions   (mostly dividends)
unmapped    1,344 (scrip code not listed on BSE near the ex-date)
```

**Strict purpose parsing** (`providers/bse_corpact.py`), built from phrasings surveyed
across the whole period: `Stock Split From Rs.10/- to Rs.2/-` → 1:5; `Bonus issue 1:3` →
factor 3/4; dividends carry an amount but no price factor; rights, buybacks, spin-offs,
capital reductions, mergers, schemes and suspensions are typed without a factor. Anything
not fully understood keeps its type and text but never a guessed factor.

**Point-in-time ISIN mapping.** A split's ex-date is exactly the day the ISIN changes, so
a scrip code maps to the listing in force on the ex-date: Schaeffler's 2021 dividend lands
on the old ISIN, its 2022 split on the new one.

### Derivation, measured for the first time

Phase 0's price-derived actions could only be spot-checked. Against BSE's own reports:

| | |
|---|---|
| **Precision 73.7%** | 659 of 894 derived actions match a reported one (ISIN or succession-linked ISIN, ±3 days, factor within 2%) |
| | 101 matched in time but had the wrong ratio — superseded by the reported ratio |
| | 134 have nothing reported — largely ETF unit splits, outside BSE's equity feed |
| **Recall 67.9%** | 500 of 736 reported splits/bonuses on liquid names were derived |

Getting an honest number took two corrections:

- **Same event, two ISINs.** On Yes Bank's 1:5 (2017-09-21) BSE reported the split on the
  new ISIN while NSE's gap was derived on the old one. Matched by ISIN alone they looked
  unrelated — and chaining history across the succession would have applied **both**
  factors, splitting the history twice. Events are now matched across successions, and a
  reported action supersedes the derived one in the adjustment factors.
- **Derivation must stay blind to the feed.** Succession derivation had skipped events
  that already had *any* action nearby, reported ones included, suppressing 579
  derivations and making the reconciliation measure only leftovers ("53% / 25%").
  Duplicates are now checked against derived actions only.

Also: successions are restricted to equity shares (ISIN type `01`) and fund units
(`INF…`). A debenture's ISIN changes on partial redemption (`INE721A07ON4` showed a
0.75 "split"), which is not an adjustment to anything priced.

### Effect on the Phase 0 residue

The feed also explains gaps: a gap with a BSE-reported action (split, bonus, spin-off,
capital reduction, merger, scheme, rights) on the same or a succession-linked ISIN within
±3 days is `action_recorded`, with the reported purpose as evidence.

| | before the feed | after |
|---|---|---|
| gaps explained (`action_recorded`) | 91 | **124** |
| open (`needs_source`) | 201 | **65** |

`fb gate`: still 12/12 — now with 23,168 corporate actions and 1,610 adjustment factors.

---

## P1-1b — BSE announcements

`fb announcements --start … --end …`. BSE's announcement API needs no session; one lake
object per day holds every page verbatim. Each announcement is typed from **BSE's own
category/subcategory** (Tier 1), refined by headline rules only where BSE is coarse, and
every label records the rule that fired.

First 90 days: **101,461 announcements, 96% resolved to an ISIN, every day complete**
against BSE's declared row count (the job's own quality contract). The residue drove the
rules before any backfill:

- **A tax demand is not an order win.** "received order of revised demand from Deputy
  Commissioner… TNGST Act" (APL Apollo) was an ORDER_WIN. Tax, penalty, court and
  regulator orders are now `LEGAL_REGULATORY` (743 in 90 days), overriding order wins.
- Types that matter in India get their own class: promoter pledges (SAST Reg. 31),
  auditor resignations, insolvency (Committee of Creditors: 225), clarifications.
- Performance: row-by-row inserts cost ~1.2 ms/row; a JSONL bulk load took a day from
  7 s to 0.35 s.

History (2015 → 2026-06) is being prefetched in the background, then loaded.

## Index lineage

NSE renamed its whole index family on 2015-11-09 and restructured Midcap/Smallcap 100
twice. Level continuity alone mis-paired renames ("CNX Nifty" → "Nifty Auto"), and a
fixed continuity threshold failed on the Bihar-result gap day, so a name rule proposes
each rename and the data verifies it against the family's gap that day: 40 of 43
verified. `index_levels_canonical` gives Nifty 50, Bank, 500, Midcap 100, Smallcap 100
and India VIX as continuous 2015–2026 series.

## P1-2 — Market Regime Brain

`fb regime --build`. Deterministic and **versioned**: a rule change is a new version,
never a rewrite. Inputs: Nifty 50 trend/drawdown/realised vol, India VIX, and breadth
(share of NSE stocks above their own 200/50-session averages). Regimes: `CRISIS`,
`RISK_OFF`, `NARROW`, `NEUTRAL`, `RISK_ON`, each stored with the reasons that fired;
changes need 3 consecutive sessions, except CRISIS, which is immediate.

Validated against known episodes — **12 of 13**: 2016 sell-off, demonetisation, 2017
bull, IL&FS 2018, COVID (CRISIS, VIX 72), 2021 bull, 2022 drawdown, 2023 rally, the
2024–25 correction.

v1 → v2, both kept:
- v1's `VIX < 20` for RISK_ON called the 2021 bull NEUTRAL — post-COVID VIX sat at
  20–25. Now 25.
- v1 read 2019 — a year of new Nifty highs — as 225 sessions of RISK_OFF. It was a
  **narrow** market: large caps up, small and mid caps down ~40%. `NARROW` now says so
  (199 sessions across 2018–19).

A regime held by hysteresis says it is held ("holding RISK_OFF: today alone indicates
NEUTRAL…") rather than explaining a regime the day's own signals do not support.

## P1-3 / P1-4 / P1-5 — evidence, world state, daily brief

**Evidence ledger.** Every claim is an immutable row carrying its source and tier, the
lake file and SHA-256, event and publication time, derivation, and every consumer.
Idempotent (the id hashes what the claim says); corrections supersede, never overwrite;
derived claims list their inputs. `fb trace <id>` follows the 2026-09-18 RISK_OFF call to
the bhavcopy and index files that produced it.

**World state.** One content-addressed snapshot per session. Point-in-time: session D
covers events published in (D 09:00, next session 09:00] IST.

**Daily brief.** `fb brief` → `data/briefs/<date>.md`, every line cited, sources grouped
by file. Reading the first real one as a reader drove eight fixes, among them: movers
show their own filings (TIMEX +16.9% beside a CGST order dropping all proceedings),
favourable orders are marked, ETFs are excluded from movers, routine SAST Reg. 29
disclosures are medium not high, and one filing is always one evidence id.

**Exit test** — *a brief you would actually read before the market opens* — is the
owner's call. What it does not yet have: a portfolio section (needs Kite) and prose
(needs an LLM API); both are designed to cite the same evidence.
