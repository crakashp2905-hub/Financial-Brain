from financial_brain.paper.terminal import etf_desk_panel


def test_etf_panel_displays_quote_valuation_tracking_cost_and_liquidity_evidence():
    html = etf_desk_panel([{
        "symbol": "INDEXETF",
        "underlying_index": "Broad Market TRI",
        "quote": {"bid": "100.0", "ask": "100.2", "event_time": "2026-11-24T10:00:00+05:30", "source": "KITE"},
        "valuation": {"premium_discount_bps": "12.3", "inav_age_seconds": 2, "inav_source": "NSE"},
        "tracking": {"tracking_difference_bps": "-35.0"},
        "liquidity": {"estimated_all_in_round_trip_cost_bps": 21.0,
                      "median_daily_turnover_inr": 10000000, "status": "ESTIMATE"},
    }])

    assert "Broad Market TRI" in html
    assert "100.0" in html and "100.2" in html
    assert "12.3 bp" in html and "fresh comparison" in html
    assert "-35.0 bp" in html
    assert "21.0 bp" in html and "10,000,000 INR" in html
    assert "KITE" in html and "2026-11-24T10:00:00+05:30" in html


def test_etf_panel_never_fills_missing_or_stale_fields_with_guesses():
    html = etf_desk_panel([{"symbol": "ETF", "quote": {}, "valuation": {"inav_age_seconds": None},
                           "tracking": {}, "liquidity": {"status": "INSUFFICIENT_DATA"}}])

    assert "unavailable / freshness gate failed" in html
    assert "INSUFFICIENT_DATA" in html
    assert "Tracking difference" in html
    assert "—" in html


def test_etf_panel_with_no_permissioned_observations_explains_data_gap():
    html = etf_desk_panel()

    assert "No point-in-time ETF observations were supplied" in html
    assert "permissioned source data is connected" in html
