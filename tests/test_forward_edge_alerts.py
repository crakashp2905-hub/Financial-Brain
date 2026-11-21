"""FE-0074 operational alerts deduplicate causes and halt new entries."""
from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.alerts import Alert, AlertBook, AlertError

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("alert_type", ["STALE_QUOTE", "TOKEN_EXPIRED", "SESSION_ABORTED",
                                         "DUPLICATE_ORDER", "LEDGER_DIFFERENCE"])
def test_high_alert_deduplicates_root_cause_and_halts_entries(tmp_path, alert_type) -> None:
    book = AlertBook(tmp_path / "alerts.sqlite3")
    try:
        alert = Alert.create(root_cause_id="root-1", session_id="s1", alert_type=alert_type,
                             severity="HIGH", occurred_at=NOW, details="fixture cause")
        assert book.raise_alert(alert)
        assert not book.raise_alert(alert)
        assert len(book.active("s1")) == 1
        assert book.halt_entries("s1") is True
        book.acknowledge(alert.alert_id)
        assert book.halt_entries("s1") is True
        book.clear(alert.alert_id)
        assert book.halt_entries("s1") is False
    finally:
        book.close()


def test_alert_identity_is_content_bound_and_unknown_clear_is_rejected(tmp_path) -> None:
    book = AlertBook(tmp_path / "alerts.sqlite3")
    try:
        alert = Alert.create(root_cause_id="r", session_id="s", alert_type="STALE_QUOTE",
                             severity="WARNING", occurred_at=NOW, details="old")
        book.raise_alert(alert)
        changed = Alert(alert.alert_id, alert.root_cause_id, alert.session_id, alert.alert_type,
                        alert.severity, NOW, "new")
        with pytest.raises(AlertError, match="changed content"):
            book.raise_alert(changed)
        with pytest.raises(AlertError, match="unknown alert"):
            book.clear("absent")
        assert book.halt_entries("s") is False
    finally:
        book.close()
