---
type: concept
tags:
  - concept
  - contract
  - execution
---

# Order validation chain

The **harness**, not the model, enforces validation.

```
Agent -> Proposed Order
      -> Risk Validator -> Exposure Validator -> Position Validator
      -> Market-state Validator -> Compliance/Policy Validator
      -> Human approval / execution policy
      -> Broker
```

From [[Awesome Harness Engineering]]. Built as [[C18 Execution gateway]].
