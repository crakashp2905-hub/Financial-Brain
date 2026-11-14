# PE-0057 feature timing and horizon metadata

The daily deep feature library now attaches metadata to every non-date output field without changing the feature columns. Metadata distinguishes predictor, liquidity-context, and forward outcome-label fields; records the construction lookback where it can be derived; declares that predictors are usable only after both the daily bar and source are available; and marks refresh cadence. Forward labels are tagged as unavailable until their future sessions mature, preventing accidental treatment as predictors.

Construction windows are not equated with economic holding horizons. Economic horizons and predictor signal half-lives remain explicitly `UNESTIMATED` until measured on a frozen, suitable cohort. The library does not invent values for them. Every metadata row has a canonical digest, and every feature column receives an explicit classified record.

This task remains partial because economic horizon estimates, release-delay contracts for non-price features, and empirical signal half-life estimates are not registered across the wider feature and fundamentals libraries. Prospective evidence is still required.
