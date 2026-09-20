---
type: concept
tags:
  - concept
  - models
  - calibration
---

# Calibration belongs to a prompt

A threshold describes a model **and the prompt it was asked with**. Change the prompt and
the numbers no longer describe the running system.

This was measured, not assumed. Wrapping model input as untrusted data
([[Untrusted text boundary]]) moved fin-r1 from 0.848 to **0.879** accuracy on the same
165 filings, and llama3.1:8b's macro-F1 from 0.820 to 0.791. So:

- every benchmark records a fingerprint of the prompt template it ran under;
- the [[Model router]] treats a route calibrated under another prompt as **no route**,
  and names the command to re-measure;
- only models measured under the deployed prompt are candidates, so the optimiser cannot
  propose a route that cannot be used.

The cost is deliberate: changing a prompt costs a full re-benchmark (~2.5 hours for three
models across four labelled sets on this laptop). That makes prompt churn visible.

The same rule applies to **text type**: thresholds fitted on filing text were wrong 19.8%
of the time on news headlines, against 9.8% on filings. Headlines got their own route.

Related: [[Model router]] · [[C21 India evaluation benchmark]]
