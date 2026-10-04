"""Has the edge gone? Watching a signal's information coefficient for a shift away from its baseline.

A strategy does not announce that it has stopped working. Its IC just drifts down inside the noise until
someone notices the P&L, many months after the information was lost. Two detectors, because they fail
differently:

``cusum``       a one-sided CUSUM on the standardised IC against its own baseline. Fast on a sustained
                shift, quiet on a bad month. Alarms when the cumulative shortfall passes ``h`` sigmas
                after allowing ``k`` sigmas of slack per period.
``rolling_t``   the t-statistic of the IC over the latest window. Slow and blunt, hard to argue with: a
                window whose mean IC is indistinguishable from zero or negative is not evidence of an
                edge, whatever the long-run record says.

``assess`` combines them into **OK / WATCH / DEGRADED**: WATCH when either fires alone, DEGRADED when
both agree. The baseline is the *earlier* part of the series, never the window being tested, otherwise
a collapse inflates its own baseline's variance and hides.
"""
from __future__ import annotations

import numpy as np

OK, WATCH, DEGRADED = "OK", "WATCH", "DEGRADED"


class DegradationError(ValueError):
    pass


def cusum(ic, *, baseline_mean: float, baseline_sd: float, k: float = 0.5, h: float = 5.0) -> dict:
    """One-sided (downward) CUSUM of standardised IC. Returns the path and the first alarm index."""
    if baseline_sd <= 0:
        raise DegradationError("baseline sd must be positive")
    x = (np.asarray(ic, dtype=float) - baseline_mean) / baseline_sd
    s, path, alarm = 0.0, [], None
    for i, v in enumerate(x):
        s = max(0.0, s - v - k)
        path.append(s)
        if alarm is None and s > h:
            alarm = i
    return {"path": path, "alarm_at": alarm, "final": path[-1] if path else 0.0}


def rolling_t(ic, window: int) -> float:
    x = np.asarray(ic, dtype=float)[-window:]
    x = x[np.isfinite(x)]
    if len(x) < 6:
        raise DegradationError(f"{len(x)} observations in the window; need at least 6")
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else 0.0


def assess(ic, *, baseline_n: int = 36, window: int = 12, t_floor: float = 1.0,
           k: float = 0.5, h: float = 5.0) -> dict:
    ic = np.asarray(ic, dtype=float)
    ic = ic[np.isfinite(ic)]
    if len(ic) < baseline_n + window:
        raise DegradationError(f"{len(ic)} observations; need {baseline_n + window}")
    base = ic[:-window]
    base = base[-max(baseline_n, 12):] if len(base) > baseline_n else base
    mu, sd = float(base.mean()), float(base.std(ddof=1))
    cs = cusum(ic[-(window * 2):], baseline_mean=mu, baseline_sd=sd, k=k, h=h)
    t = rolling_t(ic, window)
    fired_c = cs["alarm_at"] is not None
    fired_t = t < t_floor
    state = DEGRADED if (fired_c and fired_t) else WATCH if (fired_c or fired_t) else OK
    return {"state": state, "baseline_ic": mu, "recent_ic": float(ic[-window:].mean()),
            "rolling_t": t, "cusum_alarm": fired_c, "cusum_final": cs["final"],
            "why": f"recent IC {ic[-window:].mean():+.3f} vs baseline {mu:+.3f}; t {t:+.2f}; "
                   f"cusum {'alarm' if fired_c else 'quiet'}"}
