---
type: component
phase: 2
status: done
resources:
  - "[[MiMIC]]"
depends-on:
  - "[[C11 Document intelligence]]"
tags:
  - component
  - phase/2
---

# Earnings calls

**Phase 2** · Transcripts and slide decks, as structure

> [!success] Built
> `src/financial_brain/docintel/calls.py`, `ingest/calls.py` (`fb calls`).

~18k transcripts and investor presentations sit in the archive with attachments, and the
system read none of them: it knew a call *happened*. Now each document is split into
**prepared commentary and the Q&A that follows** - management chooses every word of the
first, analysts choose the second - speakers are listed, and the text is embedded locally
with `nomic-embed-text`, so "which past calls does this resemble?" is a query.

**Deliberately not built: any tone reading of call text.** [[Calibration belongs to a prompt]] says a calibration belongs to the text type it was measured on, and the
sentiment routes were measured on filings and news headlines. Call text gets a verdict
when it gets a labelled set, and not before.

Related: [[MiMIC]] · [[C11 Document intelligence]] · [[C21 India evaluation benchmark]]
