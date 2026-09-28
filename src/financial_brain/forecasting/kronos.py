"""Kronos as a forecaster, behind the same interface as a bootstrap.

Kronos (Shi et al., AAAI 2026; MIT licensed) is a decoder-only foundation model over K-lines, trained
on 12B+ bars from 45+ exchanges. It is a *forecasting specialist* and this adapter treats it as
exactly that: it returns a :class:`~.distribution.ForecastDistribution` and nothing more. It does not
decide anything. The question "should this be bought" is answered downstream by the expected-value,
risk and portfolio gates, on evidence of which a forecast is one input.

Importing this module does not import torch. Constructing a :class:`KronosForecaster` does, and says
what is missing if it cannot.

## What the adapter is responsible for

**Sampling a distribution, not a point.** The model is generative, so ``sample_count`` paths are
drawn per forecast and kept as paths. A median is available and is the least interesting thing here;
the questions worth asking are first-passage questions, which need the paths (see
:mod:`.distribution`).

**Point-in-time context.** Bars come from :mod:`.bars`, which is bounded at ``as_of``, adjusted, and
restricted to one exchange and series. A foundation model handed an unadjusted series reads a 1:10
split as a 90% crash, and handed an interleaved NSE/BSE series reads noise.

**Refusing to extrapolate its own context window.** Kronos has a finite context (512 bars in the
released checkpoints). Asking for a 250-session forecast from a model trained to continue a few dozen
steps is not a forecast; :data:`MAX_PRED_RATIO` caps the horizon as a fraction of the context and the
adapter raises rather than returning something shaped like an answer.

## What it is explicitly not responsible for

It does not decide whether Kronos is any good. That is :mod:`.walkforward`, against the nulls in
:mod:`.null`, and the bar to clear is not zero: on 1,296 point-in-time forecasts of Indian equities at
h20, a drift-free random walk scored CRPS 87.15 against a block bootstrap's 94.71. A 102M-parameter
model that does not beat "today's price, at this name's volatility" has not been shown to be wrong,
only redundant - and it enters ``evaluation_runs`` as a trial either way, which raises the Bonferroni
bar for every other signal in this project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .bars import Bar
from .distribution import ForecastDistribution, ForecastError

#: Checkpoints published by the Kronos authors, smallest first. The tokenizer is shared.
CHECKPOINTS = {
    "kronos-mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k"),
    "kronos-small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base"),
    "kronos-base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base"),
}

#: Context length of the released checkpoints, in bars.
MAX_CONTEXT = 512

#: Largest horizon the adapter will ask for, as a fraction of the context it was given. A model
#: asked to continue 250 steps from 500 is being asked to write half as much as it has read, which is
#: not what it was trained to do; the number is a judgement, but the refusal is not optional.
MAX_PRED_RATIO = 0.25

#: Bars a forecast needs before the adapter will speak. Well under MAX_CONTEXT so the model still has
#: a usable window on names with short histories, and far enough above the horizon to matter.
MIN_CONTEXT = 120


class KronosUnavailable(ForecastError):
    """Raised when the model's dependencies or weights are not present.

    A distinct type so a study can record "not evaluated" rather than "did not beat the null": those
    are different findings and collapsing them is how an untested model acquires a verdict.
    """


@dataclass
class KronosForecaster:
    """A Kronos checkpoint, wrapped as a forecaster.

    ``temperature`` and ``top_p`` control the sampling. They are part of the configuration recorded
    with the trial, because a model tuned to a temperature that happened to work is a searched
    parameter like any other and the ledger has to know it was spent.
    """

    checkpoint: str = "kronos-small"
    device: str = "cpu"
    temperature: float = 1.0
    top_p: float = 0.9
    context: int = MAX_CONTEXT
    seed: int = 0
    #: Set when the weights are already on disk; otherwise the HF cache location is used.
    local_dir: str | None = None
    name: str = field(default="", init=False)
    #: Tells the walk-forward harness to hand this forecaster bars rather than closes.
    needs_bars: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if self.checkpoint not in CHECKPOINTS:
            raise ForecastError(
                f"unknown checkpoint {self.checkpoint!r}; have {sorted(CHECKPOINTS)}")
        if not 0 < self.top_p <= 1:
            raise ForecastError(f"top_p {self.top_p} outside (0, 1]")
        if self.temperature <= 0:
            raise ForecastError(f"temperature {self.temperature} must be positive")
        self.context = min(self.context, MAX_CONTEXT)
        self.name = self.checkpoint
        self._predictor = None

    # ------------------------------------------------------------------------ loading
    def _load(self):
        """Import torch and the Kronos package, and build the predictor. Cached per instance."""
        if self._predictor is not None:
            return self._predictor
        try:
            from model import Kronos, KronosPredictor, KronosTokenizer
        except ImportError as exc:
            raise KronosUnavailable(
                "the Kronos package is not importable. It is a repository rather than a "
                "distribution: clone https://github.com/shiyu-coder/Kronos and put it on "
                f"sys.path, and install the `forecasting` extra for torch and einops ({exc})"
            ) from exc

        model_id, tok_id = CHECKPOINTS[self.checkpoint]
        try:
            tok = KronosTokenizer.from_pretrained(self.local_dir or tok_id)
            mdl = Kronos.from_pretrained(self.local_dir or model_id)
        except Exception as exc:                                  # noqa: BLE001 - reported as-is
            raise KronosUnavailable(
                f"could not load {model_id} / {tok_id}: {exc}. The weights are a download from "
                f"Hugging Face and are not fetched implicitly by this adapter") from exc

        self._predictor = KronosPredictor(mdl, tok, device=self.device,
                                          max_context=self.context)
        return self._predictor

    @property
    def available(self) -> bool:
        try:
            self._load()
            return True
        except KronosUnavailable:
            return False

    # ----------------------------------------------------------------------- forecasting
    def forecast(self, *, instrument: str, as_of: date, horizon: int,
                 bars: list[Bar] | None = None, prices: list[float] | None = None,
                 n_paths: int = 30) -> ForecastDistribution:
        """Sample ``n_paths`` futures of length ``horizon``.

        ``bars`` is required. ``prices`` is accepted so the signature matches the other forecasters
        and the harness can call every model the same way, but a close-only history is refused rather
        than padded into fake candles: a model trained on K-lines given open == high == low == close
        is being fed a distribution it never saw.
        """
        if not bars:
            raise ForecastError(
                "Kronos needs OHLCV bars; a close-only history would have to be padded into "
                "candles with zero range, which is not a K-line the model was trained on"
                + (" (prices were supplied instead)" if prices else ""))
        if len(bars) < MIN_CONTEXT:
            raise ForecastError(
                f"{len(bars)} bars is below the {MIN_CONTEXT} this adapter requires")
        window = bars[-self.context:]
        if horizon > len(window) * MAX_PRED_RATIO:
            raise ForecastError(
                f"a {horizon}-session forecast from {len(window)} bars of context asks the model "
                f"to write {horizon / len(window):.0%} of what it read; the cap is "
                f"{MAX_PRED_RATIO:.0%}")

        predictor = self._load()
        anchor = window[-1].close
        try:
            paths = self._sample(predictor, window, horizon, n_paths)
        except Exception as exc:                                  # noqa: BLE001
            raise KronosUnavailable(f"Kronos inference failed: {exc}") from exc

        if not paths:
            raise ForecastError("the model returned no paths")
        return ForecastDistribution(
            instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor,
            paths=paths, model=self.name, model_version=CHECKPOINTS[self.checkpoint][0],
            meta={"context": len(window), "temperature": self.temperature,
                  "top_p": self.top_p, "seed": self.seed, "device": self.device,
                  "first_context_session": window[0].session,
                  "last_context_session": window[-1].session})

    def _sample(self, predictor, window: list[Bar], horizon: int,
                n_paths: int) -> list[list[float]]:
        """Draw paths from the predictor, as closes.

        Kept separate so a test can exercise everything around inference - the context window, the
        horizon cap, the point-in-time boundary, the path normalisation - against a stub predictor,
        without torch or 100MB of weights.
        """
        cols = {
            "open": [b.open for b in window],
            "high": [b.high for b in window],
            "low": [b.low for b in window],
            "close": [b.close for b in window],
            "volume": [b.volume for b in window],
            "amount": [b.turnover for b in window],
        }
        sessions = [b.session for b in window]
        df, stamps, future = _context(cols, sessions, horizon)
        out = predictor.predict(
            df=df, x_timestamp=stamps, y_timestamp=future, pred_len=horizon,
            T=self.temperature, top_p=self.top_p, sample_count=n_paths,
            verbose=False)
        return _paths_from(out, horizon, n_paths)


def _context(cols: dict, sessions: list[date], horizon: int):
    """Marshal the context into whatever the predictor expects.

    Kronos's ``predict`` takes pandas objects, so pandas is a real requirement of the *model* path and
    lives in the ``forecasting`` extra alongside torch. It is not a requirement of the adapter's
    logic, and making it one would mean the window, the horizon cap and the path normalisation could
    only be tested on a machine that can run a 102M-parameter model. Without pandas the columns are
    passed through as plain lists, which a stub can read and the real model cannot - so a caller
    reaching the real model without the extra installed gets a clear failure from it rather than a
    confusing one from here.

    The future timestamps only *position* the horizon. Business days approximate the exchange
    calendar; the realised price a forecast is scored against is read from the calendar itself, in
    :func:`.walkforward.realised`, which counts sessions.
    """
    try:
        import pandas as pd
    except ImportError:
        return cols, sessions, list(range(horizon))
    df = pd.DataFrame(cols)
    stamps = pd.Series(pd.to_datetime(sessions))
    future = pd.Series(pd.bdate_range(
        start=stamps.iloc[-1] + pd.Timedelta(days=1), periods=horizon))
    return df, stamps, future


def _paths_from(out, horizon: int, n_paths: int) -> list[list[float]]:
    """Normalise whatever the predictor returned into a list of close paths.

    Kronos returns either one averaged frame or a per-sample structure depending on version and
    ``sample_count``. A frame of ``horizon`` rows is *one* path: accepting it as ``n_paths`` paths
    would report a point forecast as a distribution, and every interval derived from it would be
    zero-width - which scores as a perfectly sharp forecast and is the most dangerous possible
    failure here. So a single frame is returned as a single path and the caller's sample count is
    not assumed to have been honoured.
    """
    def closes_of(frame) -> list[float]:
        if hasattr(frame, "columns"):
            for col in ("close", "Close", "c"):
                if col in frame.columns:
                    return [float(v) for v in frame[col].tolist()]
            raise ForecastError(f"no close column in {list(frame.columns)}")
        return [float(v) for v in frame]

    paths: list[list[float]]
    if isinstance(out, (list, tuple)):
        paths = [closes_of(f) for f in out]
    else:
        paths = [closes_of(out)]
    paths = [p for p in paths if len(p) == horizon]
    if not paths:
        raise ForecastError(f"no returned path had {horizon} steps")
    if len(paths) == 1 and n_paths > 1:
        # Deliberately not duplicated up to n_paths. A distribution of n identical paths has zero
        # spread and scores as a flawless forecast.
        return paths
    return paths
