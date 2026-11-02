from financial_brain.paper.capabilities import inventory, require


def test_inventory_covers_daily_intraday_and_options_paper_surfaces():
    assert {item.mode for item in inventory()} == {"daily", "intraday", "options"}
    assert all(item.paper_only for item in inventory())
    assert {item.name for item in inventory(mode="intraday")} >= {"intraday_orb", "intraday_gap"}


def test_capability_resolution_fails_closed():
    assert require("long_option_intent").requires == ("two_sided_quotes",)
    try:
        require("unknown")
    except KeyError as exc:
        assert "unregistered paper capability" in str(exc)
    else:
        raise AssertionError("unknown capability must not silently resolve")
