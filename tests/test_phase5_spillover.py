import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import spillover as SP


def links():
    return pd.DataFrame({"a": ["A", "A"], "b": ["B", "C"],
                         "first_seen": pd.to_datetime(["2020-01-01", "2022-01-01"]),
                         "last_seen": pd.to_datetime(["2020-06-01", "2022-06-01"])})


def test_a_link_is_invisible_before_it_was_evidenced_and_after_it_lapses():
    assert SP.live_siblings(links(), "A", "2019-12-31") == []
    assert SP.live_siblings(links(), "A", "2020-03-01") == ["B"]
    assert SP.live_siblings(links(), "B", "2020-03-01") == ["A"]          # symmetric
    assert SP.live_siblings(links(), "A", "2022-03-01") == ["B", "C"] or \
        SP.live_siblings(links(), "A", "2022-03-01") == ["C", "B"]
    assert SP.live_siblings(links(), "A", "2025-01-01") == []             # both lapsed


def test_clustering_by_date_shrinks_t_for_stacked_events():
    rng = np.random.default_rng(0)
    d = pd.Series(np.repeat(pd.date_range("2020-01-01", periods=40), 25))
    day = np.repeat(rng.normal(0, 1, 40), 25)                            # a shared shock per date
    e = pd.Series(day + rng.normal(0, 0.05, 1000))
    naive = e.mean() / (e.std(ddof=1) / np.sqrt(len(e)))
    assert abs(SP.clustered_t(e, d)["t"]) < abs(naive)


def test_too_few_dates_refused():
    with pytest.raises(SP.SpilloverError):
        SP.clustered_t(pd.Series([0.1] * 10), pd.Series(pd.date_range("2020-01-01", periods=10)))


def test_a_planted_spillover_is_detected_and_the_placebo_is_not():
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2019-01-01", periods=400)
    cols = [f"L{i}" for i in range(60)]
    r = rng.normal(0, 0.01, (400, 60))
    px = pd.DataFrame(100 * np.exp(np.cumsum(r, 0)), index=days, columns=cols)
    ev_rows, lk = [], []
    for k in range(0, 50, 2):
        lk.append(("L%d" % k, "L%d" % (k + 1), pd.Timestamp("2019-01-01"), pd.Timestamp("2021-01-01")))
    for n, t in enumerate(range(20, 380, 3)):
        k = 2 * (n % 25)
        s = 1 if n % 2 else -1
        ev_rows.append(("L%d" % k, days[t], 0.03 * s))
        r[t + 1:t + 6, k + 1] += 0.004 * s                               # the sibling follows for 5 days
    px = pd.DataFrame(100 * np.exp(np.cumsum(r, 0)), index=days, columns=cols)
    ev = pd.DataFrame(ev_rows, columns=["lineage", "entry", "ar_react"])
    L = pd.DataFrame(lk, columns=["a", "b", "first_seen", "last_seen"])
    out = SP.run(ev, L, px, horizons=(5,))
    assert out[5]["real"]["mean"] > 0.01 and out[5]["real"]["t"] > 3
    assert abs(out[5]["placebo"]["t"]) < 3
