# PE-0193 — Explicit insufficient evidence status

## Acceptance

Show an explicit insufficient-evidence status whenever required inputs or observations are missing. Synthetic all-green fixtures must not become forward champion or profitability claims.

## Implementation and verification

The cockpit now requires an `OBSERVED_FORWARD` evidence source, `VERIFIED` custody, a complete scheduled/verified/receipt session denominator, and a valid evidence digest before showing a verified champion or positive adjusted profitability status. Missing observations render `INSUFFICIENT EVIDENCE`. Synthetic fixture rows remain failed even when their numeric performance fields are all positive.

Verification: champion, profitability, and F10 cockpit tests — 28 passed; Ruff, targeted mypy, and source-inventory check passed.

## Limits

The cockpit consumes upstream provenance and custody classifications; it does not verify external signatures itself. Real observed sessions and pinned trust roots remain external operational inputs.
