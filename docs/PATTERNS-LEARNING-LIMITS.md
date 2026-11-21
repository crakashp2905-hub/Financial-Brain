# Chart patterns: learning limits

- Labels exist only after their outcome horizon has matured. Immature labels are rejected.
- Features must be knowable at the decision time. Future features are rejected.
- Model versions are immutable. Rollback moves a pointer and keeps every artifact.
- A corrupted artifact fails closed.
- Calibration needs at least 200 valid labels; below that the status is NO_CALIBRATION.
- A learned score can only lower a decision. It cannot override a deterministic rejection.
- Promotion requires FORWARD_PAPER or EXTERNALLY_CONFIRMED evidence. Synthetic and historical-only results are refused.
- No model is marked live. The module has no live role.
- Gradient-boosting learners (LightGBM) and continual-learning dry runs are not built. See the final gates.
