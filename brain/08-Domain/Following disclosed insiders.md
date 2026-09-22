---
type: strategy
tags:
  - strategy
  - tested
verdict: REJECT
---

# Following disclosed insiders

**Claim.** Insiders and substantial acquirers must disclose their dealings, and their purchases predict returns (Lakonishok & Lee 2001).

**How it was built here.** `insider_60d`, counting disclosures in the trailing 60 sessions.

**Verdict.** REJECT - **t = -0.35**, no signal. As its pre-registration warned, the count is blunt: a pledge release, a sale and a purchase all count the same.

**What would change the answer.** Parse the **direction** out of the filing - buy, sell, pledge, release - which [[C11 Document intelligence]] can now do. This is the most promising unfinished idea in the playbook.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Procedural memory]]
