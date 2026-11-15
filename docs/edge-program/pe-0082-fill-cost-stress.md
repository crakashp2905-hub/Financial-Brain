# PE-0082 — Fill and cost stress

`build_execution_stress_report()` computes the complete cost/fill matrix against the frozen intention population. It requires unique intention IDs and complete full-fill gross outcome and cost inputs, so missed opportunities cannot disappear through row filtering. The report includes an input hash and passes its grid into the existing promotion gate.

The result is scenario analysis under a uniform fill-scaling assumption, not empirical broker evidence. Actual forward fills are still needed to calibrate the scenario assumptions; until then the task remains partial.

Evidence and source hashes are in `pe-0082-fill-cost-stress-evidence.json`.
