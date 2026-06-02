import ast
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from quant.api.state import runtime


def _client_for(blueprint):
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    return app.test_client()


def test_api_blueprints_reference_runtime_module_state():
    api_root = Path(__file__).resolve().parents[1] / "api"
    violations = []

    for path in sorted(api_root.glob("*_bp.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "quant.api.state.runtime":
                imported = ", ".join(alias.name for alias in node.names)
                violations.append(f"{path.name}: {imported}")

    assert violations == []


def test_strategy_select_writes_shared_runtime_state(monkeypatch):
    from quant.api.strategies_bp import strategies_bp

    monkeypatch.setattr(runtime, "selected_strategy", None)
    monkeypatch.setattr(
        runtime,
        "AVAILABLE_STRATEGIES",
        {"registry_alpha": {"id": "alpha", "name": "Alpha Strategy"}},
    )

    response = _client_for(strategies_bp).post("/api/strategies/select", json={"strategy_id": "alpha"})

    assert response.status_code == 200
    assert runtime.selected_strategy == "registry_alpha"


def test_strategy_performance_includes_live_recorder_data(monkeypatch):
    from quant.api import strategies_bp as strategies_module

    class Recorder:
        def get_strategy_performance(self, strategy_name):
            return {
                "strategy_name": strategy_name,
                "total_pnl": 12.5,
                "realized_pnl": 10.0,
                "unrealized_pnl": 2.5,
                "total_trades": 2,
                "win_rate": 0.5,
                "profit_factor": 2.0,
                "max_drawdown": 0.01,
                "sharpe_ratio": 1.4,
                "pnl_curve": [{"date": "2026-06-01", "nav": 100012.5}],
                "recent_trades": [{"symbol": "600519", "pnl": 12.5}],
                "latest_snapshot": {"date": "2026-06-01", "nav": 100012.5},
            }

    monkeypatch.setattr(strategies_module, "get_live_recorder", lambda: Recorder())
    monkeypatch.setattr(runtime, "system_status", "stopped")
    monkeypatch.setattr(
        runtime,
        "AVAILABLE_STRATEGIES",
        {
            "registry_alpha": {
                "id": "alpha",
                "name": "Alpha Strategy",
                "description": "demo",
                "symbols": ["600519"],
                "backtest": {
                    "test_sharpe": 0.1,
                    "max_dd": 0.2,
                    "cagr": 0.3,
                    "win_rate": 0.4,
                },
            }
        },
    )

    response = _client_for(strategies_module.strategies_bp).get("/api/strategies/performance/alpha")
    data = response.get_json()

    assert response.status_code == 200
    assert data["performance"]["total_pnl"] == 12.5
    assert data["performance"]["sharpe_ratio"] == 1.4
    assert data["pnl_curve"] == [{"date": "2026-06-01", "nav": 100012.5}]


def test_live_records_endpoint_validates_record_kind(monkeypatch):
    from quant.api import strategies_bp as strategies_module

    class Recorder:
        def read_day(self, kind, trading_date, strategy_name=None):
            raise ValueError(f"Unsupported live record kind: {kind}")

    monkeypatch.setattr(strategies_module, "get_live_recorder", lambda: Recorder())

    response = _client_for(strategies_module.strategies_bp).get("/api/strategies/live-records/bad-kind")

    assert response.status_code == 400


def test_status_endpoint_reads_current_runtime_state(monkeypatch):
    from quant.api.system_bp import system_bp

    monkeypatch.setattr(runtime, "system_status", "running")
    monkeypatch.setattr(runtime, "selected_strategy", "registry_alpha")

    response = _client_for(system_bp).get("/api/status")

    assert response.status_code == 200
    assert response.get_json()["status"] == "running"
    assert response.get_json()["selected_strategy"] == "registry_alpha"


def test_futu_status_reads_current_runtime_broker(monkeypatch):
    from quant.api.futu_bp import futu_bp

    class Broker:
        def is_connected(self):
            return True

        def is_unlocked(self):
            return True

    monkeypatch.setattr(runtime, "_futu_broker", Broker())

    response = _client_for(futu_bp).get("/api/futu/status")

    assert response.status_code == 200
    assert response.get_json() == {"connected": True, "unlocked": True}


def test_backtest_list_reads_current_runtime_results(monkeypatch):
    from quant.api.backtest_bp import backtest_bp

    monkeypatch.setattr(
        runtime,
        "_backtest_results",
        {
            "bt-1": {
                "status": "completed",
                "strategy_id": "alpha",
                "metrics": {"total_return_pct": 12.5, "sharpe_ratio": 1.2},
            }
        },
    )

    response = _client_for(backtest_bp).get("/api/backtest/list")

    assert response.status_code == 200
    assert response.get_json()["backtests"] == [{
        "backtest_id": "bt-1",
        "status": "completed",
        "strategy_id": "alpha",
        "total_return_pct": 12.5,
        "sharpe_ratio": 1.2,
    }]


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
