---
type: strategy
tags:
  - strategy
  - tested
verdict: VOID
---

# Following disclosed insiders

**Claim.** Insiders and substantial acquirers must disclose their dealings, and their
purchases predict returns (Lakonishok & Lee 2001).

**How it was built here.** `insider_60d`, counting disclosures in the trailing 60 sessions.

**Verdict.** **VOID, not REJECT** - and the distinction is the point.

The test returned **t = -0.35** and the note used to read "no signal; the count is blunt,
as its pre-registration warned". Counting what the feature actually contained showed
something worse: **30.6% of it was quarterly compliance certificates reporting no dealing
at all**, and a further 6.1% was a regulatory regime that ended in 2015, almost all of it
in that one year. The rule matching `reg 7(` had swallowed the certificates that the PIT
regulations happen to number 7(3).

So `t = -0.35` was never a measurement of insider behaviour. It is what a properly
computed, fully firewalled statistic looks like when the feature does not contain the
thing being tested. See [[The insider feature contained no insider trades]].

**What would change the answer.** It is being answered now. [[h8]] re-tests the claim on
the corrected population - dealing disclosures only - pre-registered before the result and
counted as a separate trial. Direction is still unread; if the cleaned count also fails,
that is the one remaining variable, and only then is parsing 95,751 SAST attachments worth
its cost.

**What this cost, and what it bought.** The pre-registration's proposed repair was to
parse direction out of the filings. That would have been an expensive answer to a question
the data could not yet support. Auditing what the feature held cost one query, and it
changed the diagnosis rather than confirming it.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Procedural memory]] ·
[[The insider feature contained no insider trades]] · [[h8]]
