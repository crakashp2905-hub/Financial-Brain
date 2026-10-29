from financial_brain.agents.evaluate import evaluate
from financial_brain.cli import main


def test_versioned_agent_guardrails():
    result = evaluate()
    assert result["suite"] == "financial-brain-agent-guards.v1"
    assert result["cases"] == 8
    assert result["failed"] == 0
    assert all(row["passed"] for row in result["results"])


def test_agent_eval_cli(capsys):
    assert main(["agent-eval"]) == 0
    assert '"failed": 0' in capsys.readouterr().out
