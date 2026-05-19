from types import SimpleNamespace

from quant.api.state import runtime


def test_maybe_snapshot_records_strategy_history_in_runtime_state(monkeypatch):
    monkeypatch.setattr(runtime, "_last_snapshot_date", None)
    with runtime._strategy_history_lock:
        runtime._strategy_history.clear()

    class Tracker:
        def snapshot_all(self, total_nav):
            return [
                SimpleNamespace(
                    date="2026-05-19",
                    strategy_name="alpha",
                    nav=total_nav,
                    market_value=900.0,
                    cash=100.0,
                    unrealized_pnl=12.5,
                    realized_pnl=3.0,
                )
            ]

    assert runtime._maybe_snapshot(Tracker(), 1000.0) is True

    history = runtime._get_strategy_history("alpha")
    assert history == [{
        "date": "2026-05-19",
        "strategy_name": "alpha",
        "nav": 1000.0,
        "market_value": 900.0,
        "cash": 100.0,
        "unrealized_pnl": 12.5,
        "realized_pnl": 3.0,
    }]


def test_maybe_snapshot_skips_duplicate_daily_snapshot(monkeypatch):
    monkeypatch.setattr(runtime, "_last_snapshot_date", None)
    with runtime._strategy_history_lock:
        runtime._strategy_history.clear()

    calls = {"count": 0}

    class Tracker:
        def snapshot_all(self, total_nav):
            calls["count"] += 1
            return [
                SimpleNamespace(
                    date="2026-05-19",
                    strategy_name="alpha",
                    nav=total_nav,
                    market_value=900.0,
                    cash=100.0,
                    unrealized_pnl=12.5,
                    realized_pnl=3.0,
                )
            ]

    assert runtime._maybe_snapshot(Tracker(), 1000.0) is True
    assert runtime._maybe_snapshot(Tracker(), 1000.0) is False
    assert calls["count"] == 1
