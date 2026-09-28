"""Distributional forecasting, and the machinery that decides whether a forecast is worth anything.

The subsystem is deliberately shaped so the model is the replaceable part. A forecaster is anything
with a ``forecast(...)`` that returns a :class:`~.distribution.ForecastDistribution`; Kronos, an
ARIMA, a bootstrap and a gradient-boosted tree all satisfy that, and the evaluation harness cannot
tell them apart. What the harness insists on is the thing that usually goes missing:

* a forecast is a **distribution over paths**, because the questions a position asks are
  path-dependent - "target before stop" is not answerable from a marginal (``distribution``);
* it is scored on **calibration and sharpness separately**, with CRPS against a named null, because
  an absolute CRPS is in price units and means nothing (``calibration``);
* the null is not a strawman. It is a stationary block bootstrap of the name's own history, which
  carries its fat tails and volatility clustering and knows nothing about the present, so the margin
  over it is the part of the model's skill that is *conditional* (``null``);
* and a forecast model is a **trial**. It enters the same ledger and faces the same Bonferroni bar as
  every other signal in this project, because a foundation model is not exempt from the arithmetic
  that 150 trials already spent.

The core install stays duckdb + pytz: everything here is pure Python. The model adapters need torch,
which lives behind the ``forecasting`` extra, and importing this package never imports torch.

The pieces, in the order they matter:

* ``distribution`` - the object, and the path-dependent questions only paths can answer.
* ``null`` - the forecasters a candidate has to beat. Not strawmen.
* ``calibration`` - calibration, sharpness and CRPS skill, measured separately.
* ``bars`` - point-in-time adjusted OHLCV, for models that need candles.
* ``walkforward`` - the walk, and entry into ``evaluation_runs`` as a trial.
* ``store`` - persistence, and resolving a forecast once its horizon closes.
* ``ensemble`` - mixing distributions (never averaging point forecasts), and a regime router that
  refuses to route until a regime has enough observations of its own.
* ``dataset`` - exporting bars for fine-tuning, with purged chronological splits and a holdout that
  is refused by default.
* ``kronos`` - one model, behind the same interface as a bootstrap. Imports torch; nothing else here
  does.
"""
from . import (
    bars, calibration, dataset, distribution, ensemble, null, store, walkforward,
)
from .distribution import ForecastDistribution, ForecastError

__all__ = ["ForecastDistribution", "ForecastError", "bars", "calibration", "dataset",
           "distribution", "ensemble", "null", "store", "walkforward"]
