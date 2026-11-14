# PE-0067 calibrated cost-to-forecast-benefit gate

Added a development-only calibration policy for forecast benefit. It fits a through-origin scale to positive forecast-benefit versus realized excess returns on unique `DEVELOPMENT` rows, clips that scale to `[0,1]` so it cannot inflate the forecast, records model/data/protocol identity, and freezes the policy in an immutable journal before validation. The cost gate computes fees + spread + impact divided by calibrated forecast benefit and abstains when policy identity, model identity, validation date, or positive benefit is missing, or when costs exceed the frozen maximum fraction.

The forward runner can enforce the policy for paper/shadow entries when configured with the registry. It adjusts the costed alpha before the net-alpha hurdle; a placeholder zero-alpha forecast cannot reach paper execution. Legacy runner construction without the optional registry remains compatible and uses the existing recomputed net-alpha hurdle, but does not enforce model-level benefit calibration.

Status remains `PARTIAL`: calibration and runner behavior were verified with synthetic fixtures only. A real in-house development sample, approved calibration protocol, and routine registry wiring are still needed; no realized profitability or forecast-calibration claim is made.
