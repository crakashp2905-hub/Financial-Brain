# PE-0053 futures carry and basis governance

The three futures hypotheses (stock cash-and-carry, single-expiry stock basis, and index carry proxy) now have distinct `SHADOW_ONLY` registry specs. Each specifies its asset scope, daily decision clock, required contract/spot/cost/funding/margin/dividend/corporate-action/rights inputs, and source lineage. None can create paper intents through the strategy registry.

`evaluate_carry` computes single-contract per-unit basis, dividends, transaction costs, funding and margin-financing cost only when every economic input exists. It also requires verified contract identity, resolved corporate actions, data rights, eligible universe and position limits before returning anything beyond a governance-blocked shadow result. A positive calculated net carry is labeled research-only; it never flips paper eligibility.

No roll-chain or stitched continuous series is accepted as a contract identity. The evaluator does not fetch data or attest provider rights; the caller must supply verified facts. No prospective futures evidence or economic edge is claimed.
