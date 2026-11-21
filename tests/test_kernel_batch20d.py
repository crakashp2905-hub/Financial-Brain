from __future__ import annotations

import json
from decimal import Decimal

import pytest

from financial_brain.kernel.batch20d_api import (
    StreamEvent,
    export_rows,
    import_alert,
    import_mql5_csv,
    method_allowed,
    paginate,
    provider_status,
    request_id,
    route_status,
    validate_manifest,
)
from financial_brain.kernel.batch20d_engine import (
    BoundedBuffer,
    EventQueue,
    benchmark,
    abi_compatible,
    check_finite,
    decode_decimal,
    encode_decimal,
    load_kernel,
    matches_oracle,
    migration_justified,
    multiply_bounded,
    parity,
)


def test_rust_migration_needs_both_hot_path_and_measured_speedup() -> None:
    assert migration_justified(0.5, 3.0) is True
    assert migration_justified(0.1, 3.0) is False
    assert migration_justified(0.5, 1.2) is False


def test_decimal_text_round_trip_keeps_precision() -> None:
    value = Decimal("1.50")
    text = encode_decimal(value)
    assert text == "1.50"
    assert decode_decimal(text) == value


def test_event_queue_orders_by_time_then_arrival() -> None:
    q = EventQueue()
    q.push(5, "b")
    q.push(5, "c")
    q.push(1, "a")
    assert [q.pop(), q.pop(), q.pop()] == ["a", "b", "c"]
    with pytest.raises(IndexError):
        q.pop()


def test_bounded_buffer_refuses_overflow_instead_of_dropping() -> None:
    buf = BoundedBuffer(2)
    buf.append(1)
    buf.append(2)
    with pytest.raises(BufferError):
        buf.append(3)
    assert len(buf) == 2


def test_abi_needs_same_major_and_not_older_minor() -> None:
    assert abi_compatible("1.4", "1.2") is True
    assert abi_compatible("2.0", "1.9") is False
    assert abi_compatible("1.1", "1.2") is False


def test_fast_implementation_matches_reference_oracle() -> None:
    assert matches_oracle(lambda x: x * 2, lambda x: x + x, [1, 2, 3]) is True
    assert matches_oracle(lambda x: x * 3, lambda x: x + x, [1]) is False


def test_non_finite_and_overflow_are_refused() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        check_finite(float("nan"))
    assert multiply_bounded(Decimal("1000"), Decimal("999"), Decimal("999999")) == Decimal("999000")
    with pytest.raises(OverflowError):
        multiply_bounded(Decimal("1000"), Decimal("1000"), Decimal("999999"))


def test_native_absence_falls_back_to_python_and_says_so() -> None:
    assert load_kernel(False) == ("python", "PYTHON_FALLBACK")
    assert load_kernel(True) == ("native", "NATIVE")
    assert parity(1, 1) == "PARITY"
    assert parity(1, 2) == "MISMATCH"


def test_versioned_routes_only() -> None:
    assert route_status("/v1/bars") == 200
    assert route_status("/bars") == 404
    assert route_status("/v1/secret") == 404


def test_stream_events_are_typed() -> None:
    assert StreamEvent("QUOTE", {}).kind == "QUOTE"
    with pytest.raises(ValueError, match="unknown stream"):
        StreamEvent("ORDER", {})


def test_read_level_cannot_post_and_paper_can() -> None:
    assert method_allowed("GET", "READ") is True
    assert method_allowed("POST", "READ") is False
    assert method_allowed("POST", "PAPER") is True
    with pytest.raises(KeyError):
        method_allowed("GET", "ADMIN")


def test_manifest_requires_fields_and_sandbox() -> None:
    assert validate_manifest({"name": "x", "version": "1", "api_version": "1", "sandbox": False}) == ["sandbox must be enabled"]
    assert validate_manifest({"name": "x", "version": "1", "api_version": "1", "sandbox": True}) == []


def test_alerts_are_untrusted_and_cannot_carry_orders() -> None:
    out = import_alert('{"symbol": "NIFTY", "condition": "cross_above"}')
    assert out["source"] == "EXTERNAL_UNTRUSTED"
    with pytest.raises(ValueError, match="order instructions"):
        import_alert(json.dumps({"symbol": "NIFTY", "condition": "x", "order": "BUY"}))


def test_mql5_csv_is_type_checked() -> None:
    rows = import_mql5_csv("ticket,symbol,side,volume,price\n1,NIFTY,BUY,50,100.5\n")
    assert rows == [{"ticket": "1", "symbol": "NIFTY", "side": "BUY", "volume": 50.0, "price": Decimal("100.5")}]
    with pytest.raises(ValueError, match="volume"):
        import_mql5_csv("ticket,symbol,side,volume,price\n1,NIFTY,BUY,-1,100\n")
    with pytest.raises(ValueError, match="header"):
        import_mql5_csv("a,b\n1,2\n")


def test_no_provider_is_reported_explicitly() -> None:
    assert provider_status(None) == "NO_PROVIDER"
    assert provider_status("local_csv") == "PROVIDER:local_csv"


def test_exports_csv_json_and_markdown() -> None:
    rows = [{"a": 1, "b": "x"}]
    assert export_rows(rows, "csv").splitlines()[0] == "a,b"
    assert json.loads(export_rows(rows, "json")) == rows
    assert export_rows(rows, "markdown").splitlines()[0] == "| a | b |"
    with pytest.raises(ValueError):
        export_rows(rows, "pdf")


def test_pagination_and_size_cap() -> None:
    page = paginate(list(range(25)), page=2, size=10)
    assert page == {"items": list(range(10, 20)), "page": 2, "pages": 3}
    with pytest.raises(ValueError):
        paginate([1], page=1, size=101)


def test_request_ids_are_deterministic() -> None:
    assert request_id(7) == "req-00000007"
    assert request_id(7) == request_id(7)
    with pytest.raises(ValueError):
        request_id(-1)


def test_benchmark_reports_positive_throughput() -> None:
    assert benchmark(lambda: None, 1000) > 0.0
    with pytest.raises(ValueError):
        benchmark(lambda: None, 0)
