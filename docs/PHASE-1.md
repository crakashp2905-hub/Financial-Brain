# Phase 1 — Perception (in progress)

**Goal:** the system knows what is happening and can say why it matters.
**Exit test:** a cited daily brief you would actually read before the market opens.

Plan: [BUILD-FLOW.md](BUILD-FLOW.md) §3. Live checklist: [HANDOFF.md](HANDOFF.md).

| Step | Component | State |
|---|---|---|
| P1-1a | C06 · BSE corporate-action feed (Tier 1) | **done** |
| P1-1b | C06 · NSE/BSE announcements + event classification | next |
| P1-2 | C07 · Market Regime Brain | — |
| P1-3 | C09 · Evidence ledger | — |
| P1-4 | C08 · World state | — |
| P1-5 | C10 · Daily brief | — |

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
