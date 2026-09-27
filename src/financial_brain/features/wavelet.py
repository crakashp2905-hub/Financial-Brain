"""Morlet wavelets: what a series is doing at each scale, and at each point in time.

A single correlation, a single volatility, a single variance ratio - each collapses a series onto one
number and throws away the thing that matters most about financial data, which is that its behaviour
is **different at different horizons**. Two names can move together over months and be uncorrelated
day to day. A book can look diversified at the scale of a session and be one bet at the scale it is
actually held.

Fourier analysis decomposes by frequency but assumes stationarity, which financial series violate
comprehensively. The wavelet transform decomposes by frequency **and** localises in time, which is
the right instrument for a series whose character changes.

## Why Morlet specifically

The Morlet wavelet is a complex sinusoid in a Gaussian window:

    psi_0(eta) = pi^(-1/4) * exp(i * omega0 * eta) * exp(-eta^2 / 2)

Being complex, it returns amplitude *and* phase, which is what makes coherence and phase-lead
between two series computable at all. At the conventional ``omega0 = 6`` the Fourier period is
`1.03 * scale`, so a scale reads directly as a horizon in sessions - which is why 6 is the standard
choice and why it is not a parameter to tune here.

Implementation follows Torrence & Compo (1998), *A Practical Guide to Wavelet Analysis*, including
its Fourier-domain transform and - importantly - its **cone of influence**.

## The cone of influence, which is a correctness issue and not a detail

A wavelet at scale *s* needs roughly `sqrt(2) * s` sessions either side of a point. Nearer the ends of
the series it is convolving with zero padding, and the coefficients there are contaminated. At a
250-session scale on an 11-year series that is a year and a half at each end, which is a lot of
plausible-looking output that means nothing.

``cwt`` returns the mask and every downstream function honours it. Not masking it is exactly the
class of error this project keeps finding: a confident number computed correctly from the wrong
input.

## numpy here, and not in ``risk/linalg.py``

``risk`` gates trades, so it must run on the core install (DuckDB and pytz) and its linear algebra is
pure Python. This is research tooling - it measures and reports, it does not refuse anything - so it
uses the FFT rather than hand-rolling one, and it degrades with a clear message if numpy is absent.
The distinction is deliberate: the modules that can stop a trade carry no optional dependency.
"""
from __future__ import annotations

import math

#: The conventional Morlet parameter. At 6 the Fourier period is 1.03 * scale, so a scale reads as
#: a horizon in sessions, and the wavelet is admissible to within the usual approximation.
OMEGA0 = 6.0
#: e-folding time as a multiple of scale, for the cone of influence (Torrence & Compo eq. 12).
COI_FACTOR = math.sqrt(2.0)
#: Minimum usable coefficients at a scale before that scale is reported at all.
MIN_USABLE = 30


class WaveletError(ValueError):
    pass


def _np():
    try:
        import numpy as np
    except ImportError as exc:                                  # pragma: no cover
        raise WaveletError(
            "the wavelet module needs numpy, which is an optional extra here: "
            'pip install -e ".[models]"') from exc
    return np


def fourier_period(scale: float, omega0: float = OMEGA0) -> float:
    """The Fourier period a scale corresponds to. At omega0=6 this is 1.03 * scale."""
    return 4 * math.pi * scale / (omega0 + math.sqrt(2 + omega0 * omega0))


def scale_for_period(period: float, omega0: float = OMEGA0) -> float:
    """The inverse: the scale that resolves a given horizon in sessions."""
    return period * (omega0 + math.sqrt(2 + omega0 * omega0)) / (4 * math.pi)


def dyadic_scales(n: int, *, dt: float = 1.0, s0: float | None = None,
                  per_octave: int = 4, omega0: float = OMEGA0) -> list[float]:
    """Scales from the smallest resolvable to the longest the series can support.

    The longest is capped so that at least ``MIN_USABLE`` coefficients survive the cone of
    influence. A scale whose every coefficient is contaminated is not a scale this series can speak
    about, and returning it would invite a reader to interpret padding.
    """
    s0 = s0 or 2 * dt
    max_scale = (n - MIN_USABLE) * dt / (2 * COI_FACTOR)
    if max_scale <= s0:
        raise WaveletError(
            f"{n} observations cannot support any scale: the smallest ({s0:g}) already needs "
            f"more than the series has once the cone of influence is honoured")
    n_oct = math.log2(max_scale / s0)
    return [s0 * 2 ** (j / per_octave)
            for j in range(int(n_oct * per_octave) + 1)]


def cwt(x, scales, *, dt: float = 1.0, omega0: float = OMEGA0) -> dict:
    """Continuous wavelet transform with the Morlet wavelet, via the FFT.

    Returns complex coefficients ``W[scale][t]``, the power ``|W|^2``, and a boolean ``usable`` mask
    that is False inside the cone of influence. Every downstream function here masks with it; a
    caller reading ``W`` directly must do the same.
    """
    np = _np()
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 16:
        raise WaveletError(f"{n} observations is too few for a transform")
    scales = list(scales)
    if not scales:
        raise WaveletError("no scales given")

    # Mean-removed: the wavelet has zero mean, so a non-zero series mean leaks into the largest
    # scale as a constant that is not a cycle.
    x = x - x.mean()
    # Zero-pad to the next power of two, which is what makes the FFT exact and is also where the
    # cone of influence comes from.
    n_fft = 1 << (n - 1).bit_length() + 1
    xf = np.fft.fft(x, n_fft)
    omega = 2 * np.pi * np.fft.fftfreq(n_fft, d=dt)

    coeffs, power = [], []
    for s in scales:
        # Torrence & Compo eq. 6: the Morlet in the Fourier domain, one-sided.
        norm = math.sqrt(2 * math.pi * s / dt) * math.pi ** -0.25
        psi_hat = norm * np.exp(-0.5 * (s * omega - omega0) ** 2)
        psi_hat = np.where(omega > 0, psi_hat, 0.0)
        w = np.fft.ifft(xf * np.conj(psi_hat))[:n]
        coeffs.append(w)
        power.append(np.abs(w) ** 2)

    t = np.arange(n)
    usable = np.array([
        (t >= COI_FACTOR * s / dt) & (t < n - COI_FACTOR * s / dt) for s in scales])
    return {"scales": scales,
            "periods": [fourier_period(s, omega0) for s in scales],
            "W": np.array(coeffs), "power": np.array(power),
            "usable": usable, "n": n, "dt": dt, "omega0": omega0,
            "usable_counts": usable.sum(axis=1).tolist()}


def scale_variance(x, scales=None, *, dt: float = 1.0, omega0: float = OMEGA0,
                  rectify: bool = True) -> list[dict]:
    """How much of the series' variance sits at each scale, uncontaminated coefficients only.

    This is the wavelet variance, and it generalises the variance ratio already in
    ``features/behaviour.py``: ``vr_60 = var(5-day) / (5 * var(1-day))`` compares exactly two
    scales, and this reports the whole spectrum. A name whose variance is concentrated at short
    scales reverts; one with variance at long scales trends; and the single ratio cannot tell a
    name with both from a name with neither.

    ## The bias this corrects, which the first version had

    Raw wavelet power ``|W|^2`` is **not comparable across scales**. The Morlet normalisation grows
    as ``sqrt(s)``, so a long-period component swamps a short-period one of the same amplitude and
    every spectrum looks like it peaks at the longest scale available.

    Measured: two sinusoids of equal amplitude at periods 12 and 96 sessions returned peaks at 93.5
    and 111.2 - the second was an adjacent scale of the *same* 96 cycle, and the 12-session cycle
    did not appear at all. It was there the whole time and the statistic could not see it.

    Liu, Liang & Weisberg (2007), *Rectification of the Bias in the Wavelet Power Spectrum*: divide
    power by the scale. ``rectify=False`` returns the biased form, for comparison only.
    """
    np = _np()
    x = np.asarray(x, dtype=float)
    scales = scales if scales is not None else dyadic_scales(x.size, dt=dt, omega0=omega0)
    r = cwt(x, scales, dt=dt, omega0=omega0)
    out, total = [], 0.0
    rows = []
    for i, s in enumerate(r["scales"]):
        mask = r["usable"][i]
        k = int(mask.sum())
        if k < MIN_USABLE:
            continue
        v = float(r["power"][i][mask].mean())
        if rectify:
            v /= s
        rows.append((s, r["periods"][i], v, k))
        total += v
    for s, period, v, k in rows:
        out.append({"scale": s, "period_sessions": period, "variance": v,
                    "share": (v / total) if total else float("nan"),
                    "usable": k})
    return out


def scale_filter(x, band, *, dt: float = 1.0, omega0: float = OMEGA0):
    """Reconstruct the part of ``x`` living in a band of periods, in sessions.

    The inverse transform for Morlet (Torrence & Compo eq. 11) is a sum over scales of the *real*
    part of the coefficients, scaled. Reconstructing from a subset of scales is a band-pass filter
    with no phase lag - which is the property a moving average does not have, and the reason a
    wavelet-filtered trend is not just a smoother.
    """
    np = _np()
    x = np.asarray(x, dtype=float)
    lo, hi = band
    scales = [s for s in dyadic_scales(x.size, dt=dt, omega0=omega0)
              if lo <= fourier_period(s, omega0) <= hi]
    if not scales:
        raise WaveletError(f"no resolvable scale has a period in [{lo}, {hi}] sessions")
    r = cwt(x, scales, dt=dt, omega0=omega0)
    # Reconstruction constants for Morlet at omega0=6 (Torrence & Compo table 2).
    c_delta, psi0_0 = 0.776, math.pi ** -0.25
    dj = (math.log2(scales[-1] / scales[0]) / (len(scales) - 1)) if len(scales) > 1 else 0.25
    acc = np.zeros(x.size)
    for i, s in enumerate(scales):
        acc += np.real(r["W"][i]) / math.sqrt(s)
    recon = acc * dj * math.sqrt(dt) / (c_delta * psi0_0)
    # The union of the scales' cones: a point is trustworthy only if every scale used is.
    usable = np.all(r["usable"], axis=0)
    return {"filtered": recon, "usable": usable, "scales": scales,
            "periods": [fourier_period(s, omega0) for s in scales],
            "usable_count": int(usable.sum())}


def coherence(x, y, scales=None, *, dt: float = 1.0, omega0: float = OMEGA0,
              smooth: int = 24, smooth_scales: int = 3,
              _null: float | None = None) -> list[dict]:
    """Wavelet coherence and phase between two series, per scale.

    Coherence is the squared cross-spectrum normalised by the two auto-spectra, smoothed - the
    wavelet analogue of a squared correlation, resolved by scale.

    ## Smoothing is the whole difficulty, and under-smoothing is the trap

    Unsmoothed, coherence is identically **1** everywhere: the ratio has no degrees of freedom left
    to disagree, so an implementation that skips smoothing reports perfect coherence at every scale
    and looks profound. Smoothing in time alone is not enough either - Torrence & Webster (1999)
    smooth in **scale** as well, and the first version here did not. Measured on two independent
    normal series it returned a mean coherence of **0.875**, which reads as "these move together"
    and means nothing at all.

    Both smoothings are applied, and ``null_coherence`` is returned with every result: what two
    independent series of the same length produce at the same settings. A coherence of 0.9 against
    a null of 0.87 is noise; against a null of 0.35 it is a finding. Reporting the number without
    its null invites the first to be read as the second.

    Phase says which series leads. It is reported in sessions at that scale, because radians of a
    47-session cycle are not a unit anybody can act on.
    """
    np = _np()
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size != y.size:
        raise WaveletError(f"series differ in length: {x.size} vs {y.size}")
    scales = scales if scales is not None else dyadic_scales(x.size, dt=dt, omega0=omega0)
    rx = cwt(x, scales, dt=dt, omega0=omega0)
    ry = cwt(y, scales, dt=dt, omega0=omega0)

    def box(a, k):
        if k <= 1:
            return a
        ker = np.ones(k) / k
        return np.convolve(a, ker, mode="same")

    def smooth2d(arr):
        """Time then scale, as Torrence & Webster (1999). Scale smoothing is what gives the
        estimate degrees of freedom; time alone leaves coherence near one."""
        sm = np.array([box(row, smooth) for row in arr])
        if smooth_scales > 1 and sm.shape[0] >= smooth_scales:
            ker = np.ones(smooth_scales) / smooth_scales
            sm = np.apply_along_axis(lambda c: np.convolve(c, ker, mode="same"), 0, sm)
        return sm

    SXY = smooth2d(rx["W"] * np.conj(ry["W"]))
    SXX = smooth2d(np.abs(rx["W"]) ** 2)
    SYY = smooth2d(np.abs(ry["W"]) ** 2)

    out = []
    for i, s in enumerate(scales):
        mask = rx["usable"][i] & ry["usable"][i]
        if int(mask.sum()) < MIN_USABLE:
            continue
        sxy, sxx, syy = SXY[i], SXX[i], SYY[i]
        denom = sxx * syy
        coh = np.where(denom > 0, np.abs(sxy) ** 2 / np.maximum(denom, 1e-300), np.nan)
        ph = np.angle(sxy)
        period = fourier_period(s, omega0)
        out.append({
            "scale": s, "period_sessions": period,
            "coherence": float(np.nanmean(coh[mask])),
            # Signed correlation-like quantity: coherence carries no sign, so the cross-spectrum's
            # real part supplies one.
            "signed": float(np.nanmean(np.real(sxy[mask]) /
                                       np.maximum(np.sqrt(denom[mask]), 1e-300))),
            "phase_radians": float(np.nanmean(ph[mask])),
            "lead_sessions": float(np.nanmean(ph[mask]) / (2 * math.pi) * period),
            "usable": int(mask.sum()),
        })
    # `_null=0.0` is the recursion guard: the null estimate itself calls this function.
    null = (_null if _null is not None else
            _null_coherence(len(x), scales, dt=dt, omega0=omega0, smooth=smooth,
                            smooth_scales=smooth_scales))
    for row in out:
        row["null_coherence"] = null
        row["above_null"] = row["coherence"] - null
    return out


def _null_coherence(n, scales, *, dt, omega0, smooth, smooth_scales,
                    seed: int = 20260927, draws: int = 3) -> float:
    """Mean coherence of independent normal series at these settings - the level to read against.

    Computed per call rather than stored as a constant, because it depends on the series length and
    both smoothing widths, and a stale constant would be worse than no null at all.
    """
    np = _np()
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(draws):
        rows = coherence(rng.standard_normal(n), rng.standard_normal(n), scales,
                         dt=dt, omega0=omega0, smooth=smooth,
                         smooth_scales=smooth_scales, _null=0.0)
        vals += [r["coherence"] for r in rows]
    return float(np.mean(vals)) if vals else float("nan")


def scale_correlation(x, y, bands, *, dt: float = 1.0, omega0: float = OMEGA0) -> list[dict]:
    """Ordinary Pearson correlation of the two series band-pass filtered to each band.

    Offered alongside ``coherence`` because it is the one a portfolio manager can read without
    translation: "these names correlate 0.2 at a weekly horizon and 0.7 at a quarterly one" is a
    sentence about a book. Coherence is the better statistic and the harder one to explain; both are
    reported and they should broadly agree.
    """
    np = _np()
    out = []
    for lo, hi in bands:
        try:
            fx = scale_filter(x, (lo, hi), dt=dt, omega0=omega0)
            fy = scale_filter(y, (lo, hi), dt=dt, omega0=omega0)
        except WaveletError as exc:
            out.append({"band": (lo, hi), "correlation": None, "why": str(exc)})
            continue
        mask = fx["usable"] & fy["usable"]
        if int(mask.sum()) < MIN_USABLE:
            out.append({"band": (lo, hi), "correlation": None,
                        "why": f"only {int(mask.sum())} coefficients outside the cone"})
            continue
        a, b = fx["filtered"][mask], fy["filtered"][mask]
        sa, sb = a.std(), b.std()
        out.append({
            "band": (lo, hi),
            "correlation": (float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
                            if sa > 0 and sb > 0 else None),
            "usable": int(mask.sum()),
            "variance_share_x": float(a.var() / np.asarray(x, dtype=float).var()),
        })
    return out
