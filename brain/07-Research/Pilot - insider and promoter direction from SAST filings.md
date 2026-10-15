---
type: research
tags:
  - research
  - data
  - insider
date: 2026-10-14
---

# Pilot - insider and promoter direction from SAST filings

The owner's Medium source names insider cluster buys (US SEC Form 4) as an edge. India's nearest equivalent in this
archive is the SAST Regulation 29 disclosure: **96,000 filings since 2015** (Reg. 29(2): 80,172; 29(1): 15,706),
almost every one with an attachment. [[The insider feature contained no insider trades]] found the headlines carry no
direction - "Acquisition" is the regulation's name. The direction is in the form: SEBI's Part A states the holding
before and after, each closed by an `e) Total (a+b+c)` row.

## What a 40-filing pilot measured (2023-2026, random)

* All 40 attachments fetched (lake: `BSE/filing_sast`).
* **15 have a text layer; 25 are scanned images.**
* `docintel/sast.py` reads direction with confidence from **4 of the 15** - the rest lose the before/after
  percentages when the PDF's table is flattened to text (cells run together), and a share-count fallback read the
  wrong direction in the pilot, so it was removed (precision over recall).
* **Inter-se transfers** (promoter-group reshuffles, no outside view) can only be detected from the cover letter: the
  prescribed format lists "inter-se transfer" among its example modes on every form.
* **OCR** (EasyOCR on CPU, already installed): ~47 s for a 3-page form, and its text loses the table layout too -
  0 of 3 scanned forms parsed. At that rate all ~58,000 scanned forms would take ~750 hours.

**Confident coverage today: 4 of 40 (10%).** Not enough to test anything.

## Step 1 done (2026-10-15): layout reading and the letter cross-check

* The form is re-read **by row** from word coordinates (PyMuPDF), so each `Total` keeps its own numbers.
* The cover letter's own statement - "acquisition of 38,739 equity shares", "sale of 25,000 shares" (a verb followed by
  a share count; the regulation's name never is) - is read separately and used as a **cross-check**.
* **One of the original four "confident" reads was wrong**: International Conveyors was read as a sale (68.74% ->
  0.06%) - the last percentage was the quantity acquired; its letter says acquisition, and the layout read agrees.
  The earlier precision claim did not hold; the cross-check is why.
* On the 15 text-layer forms: **2 high** (form and letter agree), **3 form-only**, **4 letter-only**, 6 unreadable;
  no conflicts. Direction from either source on **9 of 40** sampled (22%), up from 4.

## What it would take (the build, for 2027Q1)

1. Read the form as a **table** from coordinates (`docintel/tables.py` already rebuilds results tables this way), for
   text-layer PDFs - the likely route from 10% to most of the 38% with text.
2. For scans, OCR **with boxes** (EasyOCR returns them) and the same coordinate rebuild - restricted to the names a
   test would use (the 500 most liquid), which cuts the OCR volume by an order of magnitude.
3. A **hand-labelled validation set** (~100 forms, direction and inter-se) before any number is trusted, as for the
   sentiment router.
4. Only then a pre-registered test: promoter/insider net buying (excluding inter-se) as an event, 20-60 sessions,
   in the 2027Q1 budget.

Information, not advice.
