from pathlib import Path


def test_forward_edge_runbooks_cover_fail_closed_operational_recovery() -> None:
    path = Path(__file__).resolve().parents[1] / "docs/forward-edge/RUNBOOKS.md"
    text = path.read_text(encoding="utf-8").lower()
    for required in ("session preflight", "feed outage", "unknown order state", "session close",
                     "backup and restore", "evidence_downgraded", "do not resend", "paper-only"):
        assert required in text
