---
type: component
phase: 2
status: done
depends-on:
  - "[[C06 Event intelligence]]"
tags:
  - component
  - phase/2
---

# News recovered from filings

**Phase 2** · The story the boilerplate hides

> [!success] Built
> `src/financial_brain/events/newsref.py`, `providers/news_article.py`.

~19k archive filings are exchange clarifications whose text is procedural - "The Exchange
has sought clarification..." - while the news that moved the price is **quoted inside the
filing**, or recoverable from the URL slug. Extraction is deterministic over bytes we
already hold: **19,732 filings carry a reference, 7,146 with a usable headline**. No
request to any publisher.

Fetching the linked article is the fallback, and is deliberately narrow: on demand, one
at a time, allowlisted hosts, robots.txt obeyed, title and summary stored but never the
body. `providers/robots.py` exists because the stdlib matcher ignores wildcards and read
Moneycontrol's `Disallow: /stocks/company_info/*` as *allowed*.

Consequence for the brief: Nestle's procedural line became **"FSSAI initiates legal
action against Nestle India on baby formula, shares fall 2%"**, cited.

Related: [[Moneycontrol]] · [[Untrusted text boundary]] · [[C10 Daily brief]]
