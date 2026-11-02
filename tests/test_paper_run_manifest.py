import pytest

from financial_brain.paper.run_manifest import PaperRunManifest


def test_manifest_is_deterministic_and_deduplicates_inputs():
    args = dict(run_id="run-1", day="2026-11-01", mode="intraday",
                capability="intraday_orb", capability_version="v1",
                data_hash="a" * 64, required_inputs=("quotes", "OHLCV", "quotes"))
    a = PaperRunManifest(**args)
    b = PaperRunManifest(**{**args, "required_inputs": ("OHLCV", "quotes")})
    assert a.canonical() == b.canonical()
    assert a.sha256 == b.sha256


def test_manifest_rejects_unknown_capability_and_mode():
    base = dict(run_id="run-1", day="2026-11-01", mode="intraday",
                capability="missing", capability_version="v1", data_hash="a" * 64,
                required_inputs=())
    with pytest.raises(KeyError):
        PaperRunManifest(**base).canonical()
    base["capability"] = "intraday_orb"
    base["mode"] = "live"
    with pytest.raises(ValueError, match="mode"):
        PaperRunManifest(**base).canonical()


def test_manifest_binds_mode_version_inputs_and_hash():
    base = dict(run_id="run-1", day="2026-11-01", mode="intraday",
                capability="intraday_orb", capability_version="v1", data_hash="a" * 64,
                required_inputs=("OHLCV",))
    assert PaperRunManifest(**base).sha256
    with pytest.raises(ValueError, match="mode"):
        PaperRunManifest(**{**base, "mode": "options"}).canonical()
    with pytest.raises(ValueError, match="inputs"):
        PaperRunManifest(**{**base, "required_inputs": ()}).canonical()
    with pytest.raises(ValueError, match="incomplete"):
        PaperRunManifest(**{**base, "data_hash": "A" * 64}).canonical()
