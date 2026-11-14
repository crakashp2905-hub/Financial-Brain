# Forward-edge operations guide (offline)

This guide lets another operator reproduce the offline results without any secret. Nothing here places an order, contacts
a broker or needs an API key. Live money stays disabled: `PHYSICAL_ORDERS_ENABLED` is `False` in
`src/financial_brain/forward_edge/modes.py`.

## Reproduce the offline result

```bash
python -m pytest tests/test_forward_edge_*.py -q
python -m financial_brain.forward_edge.audit
python -m financial_brain.forward_edge.cli offline-demo
python -m financial_brain.forward_edge.cli preflight
python -m financial_brain.forward_edge.cli paper-run --fixture win
python -m financial_brain.forward_edge.cli close --fixture win
python -m financial_brain.forward_edge.cli report
```

The audit exits non-zero while any blocking item is open. The report exits 4 (insufficient evidence). Both are the
correct results today.

`offline-demo` runs deterministic win, loss, no-trade, feed-outage and unknown-order scenarios in one command. It
prints each result with `SYNTHETIC_FIXTURE`, `LOCAL_ONLY`, and `physical_orders: false`; the outputs are for onboarding
and do not count as market observations, alpha evidence, or promotion evidence.

## Source rights

- Fixture data in `forward_edge/fixtures.py` is synthetic and may be used freely.
- Live quotes, depth and historical feeds need a licence from the exchange or an authorised vendor. A paid Kite Connect
  plan is required for its quote feed; there is no Kite sandbox. Do not share a licensed feed with other people.
- Cost tables are supplied as data and must be copied from the exchange or SEBI notices that apply on the effective date.
  The values in the tests are fixtures, not real rates.

## Privacy

- No personal data or credential is stored in the repository. The baseline records only whether a credential
  environment variable is present, never its value.
- Evidence bundles contain hashes and decisions, not account numbers.

## Source configuration

- Venue calendar: a JSON file with `version` and `holidays` (see `market_data.load_calendar`). The NSE holiday list is not
  bundled; the owner supplies it.
- Trust roots for independent timestamps: `docs/forward-edge/trust-roots.json`, absent today. Without it every session is
  `LOCAL_ONLY` and no formal forward claim is possible.
- Cost schedule: effective-dated rules passed to `execution.CostSchedule`.

## Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `protected_pins` RED | A protected record differs from its pin | Do not edit the ledger. Find the changed file with `git diff`; restore it. |
| `independent_custody` RED | No trusted timestamp provider | Expected offline. Choose a provider and pin its key. |
| `NO_TRADE` with `STALE_QUOTE` | Feed unhealthy | The session records NO_TRADE; it stays in the denominator. |
| `UNKNOWN_ORDER` | Acknowledgement lost | Never resend. Reconcile against the broker or venue first. |
| Pin check fails only on Windows | Line-ending conversion | The ledger pin hashes LF-normalized content; other protected files need their `eol` attribute. |

## Audit procedure

1. Run the audit and read every RED line.
2. Check `docs/forward-edge/TRACKER.md` and `BLOCKERS.md`; each blocker names who unblocks it.
3. Verify an evidence bundle with `decision_custody.verify_bundle`.
4. Confirm the release label cannot exceed the evidence: `release.highest_label`.
5. Confirm `docs/trials/ledger.jsonl` is untouched: the audit's `protected_pins` check must be GREEN.
