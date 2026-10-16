from datetime import date


from financial_brain.evaluation import news_model as N

SESSIONS = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)]


def test_company_name_is_stripped_and_digits_collapse():
    assert N.clean("Reliance Industries Ltd results for Q3 2024", "Reliance Industries Ltd") \
        == "results for q0 0000"


def test_entry_is_same_session_before_close_next_after():
    p = N.entry_positions(SESSIONS, [date(2024, 1, 2), date(2024, 1, 2)], [False, True])
    assert list(p) == [1, 2]


def test_a_non_session_date_rolls_to_the_next_session():
    p = N.entry_positions(SESSIONS, [date(2024, 1, 4)], [False])
    assert list(p) == [3]


def test_spread_needs_enough_dates():
    import pandas as pd
    import pytest
    df = pd.DataFrame({"entry": [date(2024, 1, 1)] * 4, "score": [0.1, 0.2, 0.8, 0.9],
                       "ar3": [0.0, 0.0, 0.01, 0.01]})
    with pytest.raises(N.NewsModelError):
        N.date_clustered_spread(df, "score")
