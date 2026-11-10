# Blocker matrix

Each blocker is an external provider, owner action or elapsed evidence. None is simulated in this repository.

| Blocker | Items affected | Unblocked by |
|---|---|---|
| BLK-BROKER | FE-0013, FE-0035, FE-0132, FE-0135, FE-0136, FE-0138, FE-0139, FE-0140 | Owner obtains a broker account with API access; a sandbox or fixture stands in offline. |
| BLK-COMPLIANCE | FE-0133, FE-0134 | Broker and owner confirm SEBI retail-algo permissions, exchange IDs and static IP in writing. |
| BLK-DATA | FE-0035, FE-0036, FE-0040, FE-0062, FE-0073, FE-0087, FE-0096, FE-0111 | Owner licenses real-time quote and depth data under the exchange/vendor terms; fixtures stand in offline. |
| BLK-GITHUB | FE-0007 | Owner enables branch protection on main with required checks and review. |
| BLK-GOVERNANCE | FE-0027 | New ADR amendment by the owner, or a preregistered hypothesis in 2027Q1. |
| BLK-LINUX | FE-0010, FE-0148, FE-0150 | Run the test matrix on a Linux runner (CI after a push, or a WSL/Linux machine). |
| BLK-MARKET | FE-0045, FE-0070, FE-0079, FE-0135 | Elapsed NSE/BSE sessions with independent timestamps; cannot be shortened or simulated. |
| BLK-OWNER | FE-0129, FE-0137, FE-0139, FE-0142 | The owner makes and records the decision; agents cannot. |
| BLK-TIME | FE-0045, FE-0097 | Calendar time: the preregistered number of scheduled sessions must elapse. |
| BLK-TSA | FE-0010, FE-0047, FE-0048, FE-0051, FE-0128 | Owner selects an independent timestamp provider and pins its trust root in docs/forward-edge/trust-roots.json. |
