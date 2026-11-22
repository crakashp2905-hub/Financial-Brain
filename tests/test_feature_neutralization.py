from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from financial_brain.features.neutralization import NeutralizationError, NeutralizationModel

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


def _panel(sessions: int = 46, names: int = 90, seed: int = 21) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(sessions):
        day = date(2026, 1, 1) + timedelta(days=s)
        for i in range(names):
            size, liquidity, beta = rng.normal(), rng.normal(), rng.normal()
            corp_action = float(i % 19 == 0)
            sector = "BANK" if i % 3 == 0 else "IT" if i % 3 == 1 else "ENERGY"
            sector_effect = {"BANK": 0.5, "IT": -0.4, "ENERGY": 0.1}[sector]
            feature = 1.3 * size - 0.7 * liquidity + 0.9 * beta + 0.6 * corp_action + sector_effect
            feature += rng.normal(0, 0.05)
            rows.append({
                "session": day,
                "available_at": datetime.combine(day, time(15, 0), tzinfo=IST),
                "symbol": f"S{i:03d}", "feature": feature, "sector": sector,
                "size": size, "liquidity": liquidity, "beta": beta,
                "corporate_action": corp_action,
            })
    return pd.DataFrame(rows)


def test_fit_uses_only_prior_sessions_and_transform_removes_known_exposures():
    panel = _panel()
    as_of = date(2026, 2, 15)
    training = panel[panel.session < as_of]
    model = NeutralizationModel.fit(training, feature="feature", as_of=as_of,
                                    min_training_sessions=30)
    current = panel[panel.session == as_of]
    neutral = model.transform(current, session=as_of)
    controls = ["size", "liquidity", "beta", "corporate_action"]
    raw_corr = max(abs(current.feature.corr(current[c])) for c in controls)
    neutral_corr = max(abs(neutral.feature_neutral.corr(neutral[c])) for c in controls)
    assert model.training_sessions == 45
    assert model.training_rows == len(training)
    assert neutral_corr < raw_corr * 0.4
    assert neutral.neutralization_model_sha256.nunique() == 1
    assert neutral.neutralization_fit_as_of.eq(as_of).all()


def test_future_training_sessions_are_rejected_and_late_corrections_are_excluded():
    panel = _panel()
    as_of = date(2026, 2, 15)
    training = panel[panel.session < as_of].copy()
    model = NeutralizationModel.fit(training, feature="feature", as_of=as_of,
                                    min_training_sessions=30)
    with pytest.raises(NeutralizationError, match="strictly before"):
        NeutralizationModel.fit(panel, feature="feature", as_of=as_of,
                                min_training_sessions=30)

    late = training.iloc[[0]].copy()
    late["feature"] = 1e9
    late["available_at"] = datetime(2026, 2, 16, 10, tzinfo=UTC)
    altered = pd.concat([training, late], ignore_index=True)
    same = NeutralizationModel.fit(altered, feature="feature", as_of=as_of,
                                   min_training_sessions=30)
    assert same.model_sha256 == model.model_sha256
    assert same.excluded_unavailable_rows == 1


def test_current_cross_section_must_be_available_and_not_before_fit_cutoff():
    panel = _panel()
    as_of = date(2026, 2, 15)
    model = NeutralizationModel.fit(panel[panel.session < as_of], feature="feature", as_of=as_of,
                                    min_training_sessions=30)
    current = panel[panel.session == as_of].copy()
    current.loc[current.index[0], "available_at"] = datetime(2026, 2, 16, 10, tzinfo=UTC)
    with pytest.raises(NeutralizationError, match="unavailable"):
        model.transform(current, session=as_of)
    current = panel[panel.session == as_of]
    with pytest.raises(NeutralizationError, match="later session"):
        model.transform(current, session=date(2026, 2, 14))


def test_insufficient_history_fails_closed():
    panel = _panel(sessions=20)
    with pytest.raises(NeutralizationError, match="eligible past sessions"):
        NeutralizationModel.fit(panel, feature="feature", as_of=date(2026, 2, 15),
                                min_training_sessions=30)
