# Scheduled checkout provenance audit — 2026-11-23

Checklist item: **PA-01-12** — “Resolve effective scheduled checkout paths: production runner must report exact source SHA at startup and refuse running stale or unreviewed branch content.”

## Current behavior

- `src/financial_brain/forward_edge/provenance.py::repository_provenance` captures `git rev-parse HEAD`, current branch, dirty/clean status, parent ancestry hash and an entry-point label.
- `ForwardSessionRunner.__init__` captures that provenance and attaches it to durable session records. The existing test verifies that a clean temporary Git checkout produces a commit SHA and ancestry hash.
- Provenance collection is explicitly best effort: missing Git commands/repository state yield nullable values. The runner does not reject missing SHA, a dirty tree, an unexpected branch, a stale approved SHA or an unreviewed commit.
- The `forward real-paper` CLI path runs archived local replay and reports that it is not verified forward evidence. There is no scheduled production-session runner in `.github/workflows/ci.yml` that currently enforces a reviewed checkout.

## Assessment

The code records source provenance but does not attest the checkout as approved or refuse stale/unreviewed content. The production-style entrypoint and its trusted release pin are also not defined. An exact `HEAD` capture alone is insufficient to satisfy the requested refusal behavior.

**Status: PARTIAL.** Remaining acceptance work: define the production/scheduled entrypoint and trusted release-SHA source; require an exact match and clean checkout before startup; fail closed when source identity or review status cannot be verified; and test stale, dirty, missing-SHA and unreviewed cases.
