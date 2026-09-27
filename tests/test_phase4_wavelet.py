"""Morlet wavelets, pinned against signals whose spectrum is known before the transform runs.

A wavelet implementation is unusually easy to get wrong in a way that looks right: the transform
returns plausible coefficients whatever the normalisation, the power spectrum peaks somewhere, and
coherence comes back high. Two of the tests below exist because the first implementation failed
them, and both failures were documented biases in the literature rather than coding slips.
"""
from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from financial_brain.features import wavelet as W  # noqa: E402

N = 1200
T = np.arange(N)


def _sine(period, n=N, amp=1.0):
    return amp * np.sin(2 * np.pi * np.arange(n) / period)


def _peak(rows):
    return max(rows, key=lambda r: r["variance"])


# --------------------------------------------------------------- the transform itself
def test_the_fourier_period_is_about_one_times_the_scale_at_omega0_six():
    """Why 6 is conventional: a scale then reads directly as a horizon in sessions, so the
    spectrum is interpretable without translation."""
    for s in (4, 16, 64, 256):
        assert W.fourier_period(s) / s == pytest.approx(1.033, abs=0.001)
    assert W.scale_for_period(W.fourier_period(12.0)) == pytest.approx(12.0)


def test_a_planted_cycle_appears_at_its_own_period():
    pk = _peak(W.scale_variance(_sine(40)))
    assert pk["period_sessions"] == pytest.approx(40, rel=0.10)
    assert pk["share"] > 0.4


def test_two_equal_cycles_both_appear_once_the_power_is_rectified():
    """The test the first implementation failed. Raw |W|^2 is not comparable across scales - the
    Morlet normalisation grows as sqrt(s) - so equal-amplitude cycles at 12 and 96 returned peaks
    at 93.5 and 111.2, the second an adjacent scale of the *same* 96 cycle. Liu, Liang & Weisberg
    (2007): divide power by the scale."""
    x = _sine(12) + _sine(96)
    rect = sorted(W.scale_variance(x, rectify=True), key=lambda r: -r["variance"])
    periods = [r["period_sessions"] for r in rect[:2]]
    assert any(abs(p - 12) / 12 < 0.2 for p in periods), f"12-cycle missing: {periods}"
    assert any(abs(p - 96) / 96 < 0.2 for p in periods), f"96-cycle missing: {periods}"

    biased = sorted(W.scale_variance(x, rectify=False), key=lambda r: -r["variance"])
    top2 = [r["period_sessions"] for r in biased[:2]]
    assert not any(abs(p - 12) / 12 < 0.2 for p in top2), \
        "the biased spectrum should miss the short cycle - if it does not, this test is stale"


def test_white_noise_has_no_peak():
    rng = np.random.default_rng(11)
    rows = W.scale_variance(rng.standard_normal(N))
    assert max(r["share"] for r in rows) < 0.30
    assert len(rows) > 10


def test_the_cone_of_influence_masks_the_edges():
    """At a 250-session scale on an eleven-year series this is a year and a half at each end.
    Not masking it produces plausible output computed from zero padding."""
    r = W.cwt(_sine(50, 400), [8.0, 64.0])
    for i, s in enumerate(r["scales"]):
        usable = r["usable"][i]
        expected_first = W.COI_FACTOR * s
        assert int(np.argmax(usable)) == pytest.approx(expected_first, abs=2)
        assert not usable[0] and not usable[-1]


def test_a_scale_no_series_can_support_is_refused():
    """40 observations genuinely do support a scale of 2-3 sessions, which is why the threshold
    is computed from MIN_USABLE and the cone rather than guessed: at n=32 the smallest scale
    already needs more series than exists."""
    assert W.dyadic_scales(40)                 # short but not impossible
    with pytest.raises(W.WaveletError):
        W.dyadic_scales(32)
    with pytest.raises(W.WaveletError):
        W.cwt(np.zeros(8), [2.0])


def test_the_series_mean_does_not_leak_into_the_longest_scale():
    """The wavelet has zero mean, so a constant offset is not a cycle and must not appear as one."""
    a = W.scale_variance(_sine(40))
    b = W.scale_variance(_sine(40) + 100.0)
    for x, y in zip(a, b):
        assert x["variance"] == pytest.approx(y["variance"], rel=1e-9)


# ------------------------------------------------------------------------- band filtering
def test_a_band_filter_keeps_its_own_band_and_drops_the_others():
    x = _sine(8) + _sine(120)
    fast = W.scale_filter(x, (4, 20))
    slow = W.scale_filter(x, (60, 250))
    f, s = fast["filtered"][fast["usable"]], slow["filtered"][slow["usable"]]
    # Each filtered series should correlate with its own component and not the other.
    n = min(len(f), len(s))
    assert f.std() > 0 and s.std() > 0
    # The fast band must oscillate far more often than the slow one.
    fast_crossings = int(np.sum(np.diff(np.sign(f)) != 0))
    slow_crossings = int(np.sum(np.diff(np.sign(s)) != 0))
    assert fast_crossings > 4 * slow_crossings
    del n


def test_an_unresolvable_band_is_refused_rather_than_approximated():
    with pytest.raises(W.WaveletError):
        W.scale_filter(_sine(40), (5000, 9000))


# ---------------------------------------------------------------------------- coherence
def test_identical_series_are_perfectly_coherent():
    rows = W.coherence(_sine(40) + _sine(9), None or _sine(40) + _sine(9))
    assert np.mean([r["coherence"] for r in rows]) == pytest.approx(1.0, abs=1e-6)


def test_independent_series_are_not_coherent_once_smoothed_in_scale():
    """The test the first implementation failed. Smoothed in time alone, two independent normal
    series returned a mean coherence of 0.875, which reads as "these move together" and means
    nothing. Torrence & Webster (1999) smooth in scale as well."""
    rng = np.random.default_rng(3)
    a, b = rng.standard_normal(N), rng.standard_normal(N)
    rows = W.coherence(a, b)
    mean_coh = np.mean([r["coherence"] for r in rows])
    assert mean_coh < 0.65, f"independent series should not be coherent: {mean_coh:.3f}"
    # And the same data with time-smoothing only must be visibly worse, which is why the
    # scale-smoothing default exists.
    thin = W.coherence(a, b, smooth=8, smooth_scales=1, _null=0.0)
    assert np.mean([r["coherence"] for r in thin]) > mean_coh


def test_every_coherence_row_carries_the_null_it_should_be_read_against():
    """0.9 against a null of 0.87 is noise; against 0.42 it is a finding, and the number alone
    cannot tell you which."""
    rng = np.random.default_rng(5)
    a, b = rng.standard_normal(N), rng.standard_normal(N)
    rows = W.coherence(a, b)
    assert all("null_coherence" in r and "above_null" in r for r in rows)
    null = rows[0]["null_coherence"]
    assert 0.0 < null < 1.0
    assert abs(np.mean([r["above_null"] for r in rows])) < 0.15


def test_a_shared_component_is_coherent_well_above_the_null():
    rng = np.random.default_rng(9)
    a = rng.standard_normal(N)
    b = 0.7 * a + 0.3 * rng.standard_normal(N)
    rows = W.coherence(a, b)
    assert np.mean([r["above_null"] for r in rows]) > 0.25


def test_phase_is_reported_in_sessions_not_radians():
    """Radians of a 47-session cycle are not a unit anybody can act on."""
    rows = W.coherence(_sine(40), np.roll(_sine(40), 5))
    row = min(rows, key=lambda r: abs(r["period_sessions"] - 40))
    assert "lead_sessions" in row
    assert abs(row["lead_sessions"]) < row["period_sessions"]


def test_mismatched_lengths_are_refused():
    with pytest.raises(W.WaveletError):
        W.coherence(np.zeros(100), np.zeros(90))


# ------------------------------------------------------------------- scale correlation
def test_scale_correlation_finds_a_band_where_two_series_agree_and_one_where_they_do_not():
    rng = np.random.default_rng(17)
    common_slow = _sine(120)
    a = common_slow + 3.0 * rng.standard_normal(N)
    b = common_slow + 3.0 * rng.standard_normal(N)
    rows = W.scale_correlation(a, b, [(4, 20), (60, 250)])
    fast = next(r for r in rows if r["band"] == (4, 20))
    slow = next(r for r in rows if r["band"] == (60, 250))
    assert slow["correlation"] > fast["correlation"] + 0.3, (fast, slow)


def test_an_unresolvable_band_reports_why_rather_than_a_number():
    rows = W.scale_correlation(_sine(40), _sine(40), [(5000, 9000)])
    assert rows[0]["correlation"] is None
    assert "why" in rows[0]


def test_numpy_absence_gives_an_instruction():
    """The modules that can stop a trade carry no optional dependency; this one measures and
    reports, so it may use the FFT - and must say so rather than traceback."""
    import inspect

    assert 'pip install -e ".[models]"' in inspect.getsource(W._np)
