# Human-approved paper eligibility (PE-0048)

A strategy can enter `PAPER_ELIGIBLE` only when the registry receives all of the following in one transition:

- The exact currently registered `SHADOW_ONLY` strategy spec.
- An immutable `ExperimentCharter` with complete evaluation identity, matching strategy and code digest, a frozen benchmark list, and a first-observation date (`start_on`).
- A registered benchmark definition whose freeze date is strictly earlier than the charter's first-observation date.
- A non-expired `STRATEGY_ENROLLMENT` approval whose named human subject is the strategy and whose scope digest equals the canonical charter digest.

The former `human_approved=True` switch is removed. A boolean, missing evidence, AI/non-human actor, stale charter approval, mismatched strategy/code/benchmark, or benchmark frozen on/after first observation is rejected. The benchmark registry rejects changes to an already registered benchmark ID.

This is an application-level integrity gate. The current `Approval` record is not a cryptographic signature, does not authenticate the human identity, and is not durably recorded with the registry transition. Hostile callers able to construct Python objects can forge actor metadata; production enrollment therefore remains incomplete until approval is authenticated, durably journaled, and enforced by all paper-book writers.
