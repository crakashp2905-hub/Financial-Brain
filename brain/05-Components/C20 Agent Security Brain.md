---
type: component
phase: 2
status: in-progress
resources:
  - "[[Anthropic Cybersecurity Skills]]"
  - "[[Browser Use]]"
depends-on: []
tags:
  - component
  - phase/2
---

# C20 Agent Security Brain

**Phase 2** · Everything external is untrusted

Prompt-injection detection and isolation · credential vaulting · secret redaction ·
tool allowlists · least privilege · transaction scopes · immutable audit logs ·
sandboxed execution · exfiltration controls.

Attack surface: Browser -> Internet -> Research -> Code execution -> Financial data ->
Portfolio -> Broker.

## Resources needed
- [[Anthropic Cybersecurity Skills]]
- [[Browser Use]]

## Depends on
- _none — can start immediately_

---
[[MOC Build]]

> [!success] Partly built (2026-09-20)
> The boundary that mattered first: [[Untrusted text boundary]], because the system now
> reads fetched article titles and filing text into prompts. Tool allowlists, credential
> vaulting and sandboxing remain unbuilt - they matter when something can *act* rather
> than only read.
