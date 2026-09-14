---
type: concept
tags:
  - concept
  - principle
  - meta
---

# Resource discipline

Twenty-plus repositories is already past the point where adding more makes the
architecture worse rather than better. Three rules:

1. **Nothing becomes a dependency without a verdict** in [[MOC Resources]].
2. **Every INTEGRATE must name what it replaces.** If it replaces nothing, it is STUDY.
3. **Licence before code.** Two load-bearing assumptions were already wrong
   ([[OpenBB]], [[MLFinLab]]) — assume others are. See [[D7 Verify unverified licences]].

> Don't build a Frankenstein: integrate a small core, evaluate the rest.
