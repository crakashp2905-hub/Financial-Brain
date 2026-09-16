---
type: resource
verdict: DEFER
category: orchestration
license: "Apache-2.0"
license-risk: green
url: https://docs.dagster.io/
role: "Data-asset orchestration and lineage"
tags:
  - resource
  - verdict/defer
  - category/orchestration
  - licence/green
---

# Dagster

**Data-asset orchestration and lineage - deferred, deliberately**

Evaluate for scheduled assets and lineage, but only once simple idempotent jobs and a
scheduler stop being enough.

Phase 0 already provides idempotence, replayability, lineage, quarantine and run history
in about 300 lines (`ingest/job.py`). Adding an orchestration framework now would be
tooling ahead of need.

**Migrate when:** multiple interdependent asset graphs, backfill coordination across
sources, or partition-aware retries become the daily problem.
