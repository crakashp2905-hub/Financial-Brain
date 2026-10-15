"""The evening tone step skips a model server another client is using."""
from financial_brain.events import tone


def test_busy_when_a_foreign_model_is_loaded():
    ps = {"models": [{"name": "qwen2.5:3b"}, {"name": "nomic-embed-text:latest"}]}
    assert "qwen2.5:3b" in tone.ollama_busy({"fin-r1:latest", "phi4:latest"}, ps=ps)


def test_not_busy_with_only_our_models_or_none():
    assert tone.ollama_busy({"fin-r1:latest"}, ps={"models": [{"name": "fin-r1:latest"}]}) is None
    assert tone.ollama_busy({"fin-r1:latest"}, ps={"models": []}) is None
