import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
import yaml

from quant.scripts.strategy_dashboard_server import _pending_submit_orders, build_dashboard_payload, create_app
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


def test_strategy_dashboard_payload_reads_live_records_positions_and_controls(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    paper_positions_path = root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    report_path = root / "quant" / "features" / "strategies" / "DemoStrategy" / "full_research_report.html"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    shared_config = root / "quant" / "shared" / "config" / "config.yaml"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    report_path.parent.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    shared_config.parent.mkdir(parents=True, exist_ok=True)

    shared_config.write_text(
        yaml.safe_dump({"live_trading": {"strategy_initial_cash": 20000}}),
        encoding="utf-8",
    )
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 50000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 30000}]}),
        encoding="utf-8",
    )
    report_path.write_text("<html>report</html>", encoding="utf-8")
    control_path.write_text(
        json.dumps({
            "strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "live_enabled": True,
                    "live_state": "running",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-03T09:31:00",
                }
            }
        }),
        encoding="utf-8",
    )
    paper_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 9.99,
                        "market_value": 0.0,
                        "unrealized_pnl": 0.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"PAPER-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 10.0,
                        "market_value": 0.0,
                        "unrealized_pnl": 0.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [
            {
                "timestamp": "2026-06-03T09:30:59",
                "strategy_name": "DemoStrategy",
                "order_id": "CLIENT-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "LIMIT",
                "price": 10.02,
                "status": "accepted",
            },
            {
                "timestamp": "2026-06-03T15:01:00",
                "strategy_name": "DemoStrategy",
                "order_id": "CLIENT-2",
                "symbol": "000001",
                "side": "SELL",
                "quantity": 200,
                "order_type": "LIMIT",
                "price": None,
                "reference_price": 12.34,
                "execution_cost_bps": 25.0,
                "status": "accepted",
                "submit_date": date.today().isoformat(),
            },
        ],
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.02,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "price": 10.0,
            "commission": 1.23,
        }],
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "broker_order_id": "PAPER-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.02,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        paper_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "price": 9.99,
            "commission": 0.5,
        }],
    )
    _write_jsonl(
        live_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "date": "2026-06-03",
            "strategy_name": "DemoStrategy",
            "nav": 120000.0,
            "cash": 30000.0,
            "market_value": 90000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 100.0,
            "total_pnl": 100.0,
        }],
    )
    _write_daily_ohlc(stock_db, "600519", "2026-06-03", 10.0, 11.0)

    payload = build_dashboard_payload(root)

    strategy = payload["strategies"][0]
    assert strategy["name"] == "DemoStrategy"
    assert strategy["report_url"] == "/reports/DemoStrategy"
    assert strategy["live"]["configured"] is True
    assert strategy["live"]["accepts_signals"] is True
    assert strategy["initial_cash"]["live"] == pytest.approx(50000.0)
    assert strategy["initial_cash"]["paper"] == pytest.approx(30000.0)
    assert strategy["live"]["initial_cash"] == pytest.approx(50000.0)
    assert strategy["paper"]["initial_cash"] == pytest.approx(30000.0)
    assert strategy["live"]["control"]["mode"] == "live"
    assert strategy["live"]["control"]["live_state"] == "running"
    assert strategy["live"]["state"]["run"]["initial_cash"] == pytest.approx(50000.0)
    assert strategy["live"]["state"]["control_state"]["lifecycle_state"] == "running"
    assert strategy["live"]["state"]["watermark"]["latest_market_data_date"] == "2026-06-03"
    assert strategy["paper"]["control"]["mode"] == "paper"
    assert strategy["paper"]["control"]["live_state"] == "running"
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["live"]["records"]["orders"][0]["limit_price"] == 10.02
    assert strategy["live"]["records"]["orders"][0]["fill_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["raw_fill_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["commission"] == 1.23
    assert strategy["live"]["records"]["orders"][0]["open_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["slippage_bps"] == 0.0
    assert strategy["live"]["performance"]["total_commission"] == 1.23
    assert strategy["live"]["records"]["pending_orders"][0]["order_id"] == "CLIENT-2"
    assert strategy["live"]["records"]["pending_orders"][0]["signal_date"] == "2026-06-03"
    assert strategy["live"]["records"]["pending_orders"][0]["submit_date"] == date.today().isoformat()
    assert strategy["live"]["records"]["pending_orders"][0]["cost_bps_display"] == "+25.0 bps"
    assert strategy["live"]["records"]["pending_orders"][0]["display_status"] == "pending_submit"
    assert strategy["live"]["holdings"]["items"][0]["current_price"] == 11.0
    assert strategy["live"]["holdings"]["items"][0]["price_date"] == "2026-06-03"
    assert strategy["live"]["holdings"]["items"][0]["avg_cost"] == pytest.approx(10.0123)
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(98.77)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(48998.77)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(50098.77)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(98.77)
    assert strategy["live"]["performance"]["total_pnl_pct"] == pytest.approx(98.77 / 50000.0)
    assert strategy["live"]["performance"]["total_return"] == pytest.approx(98.77 / 50000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(50098.77)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(48998.77)
    assert strategy["live"]["performance"]["slippage_sample_count"] == 1
    assert strategy["live"]["performance"]["median_slippage_bps"] == 0.0
    assert strategy["live"]["performance"]["weighted_avg_slippage_bps"] == 0.0
    assert strategy["paper"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["paper"]["records"]["orders"][0]["limit_price"] == 10.02
    assert strategy["paper"]["records"]["orders"][0]["fill_price"] == 10.02
    assert strategy["paper"]["records"]["orders"][0]["raw_fill_price"] == 9.99
    assert strategy["paper"]["records"]["orders"][0]["commission"] == 0.5
    assert strategy["paper"]["records"]["orders"][0]["open_price"] == 10.0
    assert strategy["paper"]["records"]["orders"][0]["slippage_bps"] == pytest.approx(20.0)
    assert strategy["paper"]["holdings"]["items"][0]["avg_cost"] == pytest.approx(10.025)
    assert strategy["paper"]["holdings"]["total_pnl"] == pytest.approx(97.5)
    assert strategy["paper"]["performance"]["total_pnl_pct"] == pytest.approx(97.5 / 30000.0)
    assert strategy["paper"]["performance"]["total_return"] == pytest.approx(97.5 / 30000.0)
    assert strategy["paper"]["performance"]["total_nav"] == pytest.approx(30097.5)
    assert strategy["paper"]["performance"]["cash"] == pytest.approx(28997.5)
    assert strategy["paper"]["performance"]["total_commission"] == 0.5
    assert strategy["paper"]["performance"]["median_slippage_bps"] == pytest.approx(20.0)


def test_strategy_dashboard_materializes_and_reads_strategy_mode_records(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    control_path.write_text(
        json.dumps({
            "paper_strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "mode": "paper",
                    "live_enabled": True,
                    "live_state": "paused",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-03T09:30:00",
                }
            }
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [
            {
                "timestamp": "2026-06-03T15:00:00",
                "strategy_name": "DemoStrategy",
                "order_id": "LIVE-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "MARKET",
                "status": "accepted",
            },
            {
                "timestamp": "2026-06-03T15:00:00",
                "strategy_name": "OtherStrategy",
                "order_id": "LIVE-OTHER",
                "symbol": "000002",
                "side": "BUY",
                "quantity": 100,
                "order_type": "MARKET",
                "status": "accepted",
            },
        ],
    )
    _write_jsonl(
        paper_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "000001",
            "side": "BUY",
            "quantity": 100,
            "order_type": "MARKET",
            "status": "accepted",
        }],
    )

    payload = build_dashboard_payload(root)
    strategy = next(item for item in payload["strategies"] if item["name"] == "DemoStrategy")

    assert payload["record_dirs"]["strategy_modes"].endswith("strategy_modes")
    assert payload["record_dirs"]["strategy_state"].endswith("strategy_state.duckdb")
    assert payload["latest_record_date"] == {"live": "2026-06-03", "paper": "2026-06-03"}
    assert [row["symbol"] for row in strategy["live"]["records"]["signals"]] == ["600519"]
    assert [row["symbol"] for row in strategy["paper"]["records"]["signals"]] == ["000001"]
    assert strategy["live"]["records"]["signals"][0]["run_id"] == strategy["live"]["state"]["run"]["run_id"]
    assert strategy["paper"]["records"]["signals"][0]["run_id"] == strategy["paper"]["state"]["run"]["run_id"]
    assert strategy["paper"]["control"]["live_state"] == "paused"
    assert (
        root
        / "quant"
        / "infrastructure"
        / "var"
        / "strategy_state.duckdb"
    ).exists()


def test_strategy_dashboard_api_reads_strict_state_without_migration_writes(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "LIVE-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "MARKET",
            "status": "accepted",
        }],
    )
    build_dashboard_payload(root)
    state_path = root / "quant" / "infrastructure" / "var" / "strategy_state.duckdb"
    before = state_path.stat().st_mtime_ns

    res = create_app(root).test_client().get("/api/dashboard")

    assert res.status_code == 200
    assert res.get_json()["record_dirs"]["strategy_state"].endswith("strategy_state.duckdb")
    assert state_path.stat().st_mtime_ns == before


def test_strategy_dashboard_signal_table_hides_live_submit_attempts(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_day = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_submit_day = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    live_config.mkdir(parents=True)
    live_records_day.mkdir(parents=True)
    live_submit_day.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_day / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "SIGNAL-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": None,
            "execution_cost_bps": 25.0,
            "status": "accepted",
            "submit_date": "2026-06-09",
        }],
    )
    _write_jsonl(
        live_submit_day / "signals.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47.208000",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "execution_reference_price": 4.769,
            "status": "accepted",
        }],
    )
    _write_jsonl(
        live_submit_day / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47.208000",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_submit_day / "fills.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:48",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "price": 4.769,
            "commission": 5.0,
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert [row["timestamp"] for row in strategy["live"]["records"]["signals"]] == ["2026-06-08T15:00:00"]
    assert strategy["live"]["records"]["pending_orders"] == []
    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"


def test_strategy_dashboard_cash_only_configured_strategy_uses_initial_cash(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    shared_config = root / "quant" / "shared" / "config" / "config.yaml"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    shared_config.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    shared_config.write_text(
        yaml.safe_dump({"live_trading": {"strategy_initial_cash": 20000}}),
        encoding="utf-8",
    )
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "CashOnlyStrategy", "enabled": True, "allocation_cash": 15000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "CashOnlyStrategy", "enabled": True, "allocation_cash": 25000}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "600519", "2026-06-04", 10.0, 11.0)

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["holdings"]["items"] == []
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(15000.0)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(15000.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(15000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(15000.0)
    assert strategy["paper"]["holdings"]["cash"] == pytest.approx(25000.0)
    assert strategy["paper"]["performance"]["total_nav"] == pytest.approx(25000.0)
    assert strategy["paper"]["records"]["snapshots"][-1]["source"] == "cash_only_no_activity"
    assert strategy["paper"]["records"]["snapshots"][-1]["date"] == "2026-06-04"
    assert strategy["paper"]["records"]["snapshots"][-1]["nav"] == pytest.approx(25000.0)
    assert strategy["paper"]["performance"]["pnl_curve"][-1]["nav"] == pytest.approx(25000.0)
    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_state.duckdb")
    state_snapshots = state_store.read("snapshots", mode="paper", strategy_name="CashOnlyStrategy")
    assert state_snapshots[-1]["source"] == "cash_only_no_activity"
    assert state_snapshots[-1]["nav"] == pytest.approx(25000.0)


def test_strategy_dashboard_derives_live_curve_from_fills_when_snapshots_missing(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "159949": {
                        "symbol": "159949",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 2.005,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 2.01,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "price": 2.0,
            "commission": 0.5,
        }],
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-03", 2.0, 2.01)
    _write_daily_ohlc(stock_db, "159949", "2026-06-04", 2.02, 2.03)

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["live"]["performance"]["pnl_curve"]

    assert [point["date"] for point in curve] == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert curve[0]["cash"] == pytest.approx(20000.0)
    assert curve[0]["nav"] == pytest.approx(20000.0)
    assert curve[1]["nav"] == pytest.approx(20000 - 200.5 + 201.0)
    assert curve[2]["nav"] == pytest.approx(20000 - 200.5 + 203.0)


def test_strategy_dashboard_does_not_publish_live_nav_after_latest_market_data(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_prev = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_records_today = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records_prev.mkdir(parents=True)
    live_records_today.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "510300": {
                        "symbol": "510300",
                        "strategy_name": "DemoStrategy",
                        "qty": 300.0,
                        "avg_cost": 4.7856666667,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_prev / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 10000.0,
            "cash": 10000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )
    _write_jsonl(
        live_records_today / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records_today / "fills.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:48",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "price": 4.769,
            "commission": 5.0,
        }],
    )
    _write_daily_ohlc(stock_db, "510300", "2026-06-08", 4.7, 4.739)

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["live"]["performance"]["pnl_curve"]

    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["live"]["records"]["fills"][0]["timestamp"] == "2026-06-09T09:39:48"
    assert [row["date"] for row in curve] == ["2026-06-08"]
    assert strategy["live"]["performance"]["latest_snapshot"]["date"] == "2026-06-08"
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(9995.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(8564.3)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["performance"]["unrealized_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["performance"]["total_return"] == pytest.approx(-5.0 / 10000.0)
    assert strategy["live"]["holdings"]["price_date"] is None
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(8564.3)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(9995.0)
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(1430.7)
    assert strategy["live"]["holdings"]["total_cost"] == pytest.approx(1435.7)
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["holdings"]["items"][0]["valuation_status"] == "unmarked_after_activity"
    assert strategy["live"]["holdings"]["items"][0]["current_price"] == pytest.approx(4.769)
    assert strategy["live"]["holdings"]["items"][0]["price_source"] == "unmarked_fill_after_activity"
    assert strategy["live"]["holdings"]["items"][0]["unrealized_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(
        10000.0
        - strategy["live"]["records"]["orders"][0]["filled_qty"] * strategy["live"]["records"]["orders"][0]["fill_price"]
        - strategy["live"]["records"]["orders"][0]["commission"]
    )
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(
        strategy["live"]["holdings"]["cash"] + strategy["live"]["holdings"]["total_market_value"]
    )
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(
        strategy["live"]["holdings"]["nav"] - 10000.0
    )


def test_strategy_dashboard_submitted_orders_without_fills_keep_cash_unchanged(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_prev = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_records_today = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    live_config.mkdir(parents=True)
    live_records_prev.mkdir(parents=True)
    live_records_today.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_prev / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 10000.0,
            "cash": 10000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )
    _write_jsonl(
        live_records_today / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-UNFILLED",
            "broker_order_id": "BRK-UNFILLED",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["records"]["orders"][0]["display_status"] == "no_fill"
    assert strategy["live"]["records"]["orders"][0]["filled_qty"] == pytest.approx(0.0)
    assert strategy["live"]["holdings"]["items"] == []
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(10000.0)
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(0.0)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(0.0)


def test_strategy_dashboard_materializes_canonical_snapshot_when_legacy_snapshot_mismatches_initial_cash(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    paper_positions_path = root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    paper_config.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    paper_positions_path.parent.mkdir(parents=True, exist_ok=True)
    stock_db.parent.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    paper_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "159949": {
                        "symbol": "159949",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 2.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"PAPER-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "broker_order_id": "PAPER-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 2.0,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        paper_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "price": 2.0,
            "commission": 0.0,
        }],
    )
    _write_jsonl(
        paper_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "date": "2026-06-03",
            "strategy_name": "DemoStrategy",
            "nav": 210.0,
            "cash": 0.0,
            "market_value": 210.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 10.0,
            "total_pnl": 10.0,
        }],
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-03", 2.0, 2.1)

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["paper"]["performance"]["pnl_curve"]
    snapshot_rows = StrategyStateStore(
        root / "quant" / "infrastructure" / "var" / "strategy_state.duckdb"
    ).read("snapshots", mode="paper", strategy_name="DemoStrategy")
    canonical = [row for row in snapshot_rows if row.get("source") == "canonical_fill_ledger"]

    assert curve[0]["date"] == "2026-06-02"
    assert curve[0]["cash"] == pytest.approx(20000.0)
    assert curve[0]["nav"] == pytest.approx(20000.0)
    assert curve[1]["date"] == "2026-06-03"
    assert curve[1]["cash"] == pytest.approx(19795.0)
    assert curve[1]["nav"] == pytest.approx(20005.0)
    assert strategy["paper"]["records"]["orders"][0]["commission"] == pytest.approx(5.0)
    assert strategy["paper"]["records"]["fills"][0]["commission"] == pytest.approx(5.0)
    assert strategy["paper"]["records"]["fills"][0]["commission_source"] == "estimated_paper_shared_model"
    assert strategy["paper"]["performance"]["total_commission"] == pytest.approx(5.0)
    assert canonical[0]["cash"] == pytest.approx(20000.0)
    assert canonical[0]["nav"] == pytest.approx(20000.0)
    assert canonical[-1]["cash"] == pytest.approx(19795.0)
    assert canonical[-1]["nav"] == pytest.approx(20005.0)


def test_strategy_dashboard_paper_reused_order_ids_match_fills_by_record_date(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    day1 = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-04"
    day2 = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-05"
    paper_config.mkdir(parents=True)
    day1.mkdir(parents=True)
    day2.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    for day, side, price in ((day1, "SELL", 2.017), (day2, "BUY", 1.989)):
        _write_jsonl(
            day / "orders.jsonl",
            [{
                "timestamp": f"{day.name}T09:31:00",
                "strategy_name": "DemoStrategy",
                "order_id": "PAPER_1",
                "broker_order_id": "PAPER_1",
                "symbol": "159949",
                "side": side,
                "quantity": 100,
                "order_type": "LIMIT",
                "price": price,
                "status": "submitted",
            }],
        )
        _write_jsonl(
            day / "fills.jsonl",
            [{
                "timestamp": f"{day.name}T09:31:01",
                "strategy_name": "DemoStrategy",
                "order_id": "PAPER_1",
                "symbol": "159949",
                "side": side,
                "quantity": 100,
                "price": price,
                "commission": 0.0,
            }],
        )

    strategy = build_dashboard_payload(root)["strategies"][0]
    orders = strategy["paper"]["records"]["orders"]
    fills = strategy["paper"]["records"]["fills"]

    assert [row["filled_qty"] for row in orders] == [pytest.approx(100.0), pytest.approx(100.0)]
    assert [row["commission"] for row in orders] == [pytest.approx(5.0), pytest.approx(5.0)]
    assert [row["commission"] for row in fills] == [pytest.approx(5.0), pytest.approx(5.0)]


def test_strategy_dashboard_does_not_mark_near_timestamp_order_as_pending(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-04"
    paper_config.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        paper_records / "signals.jsonl",
        [{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-04T16:00:33.498762",
        }],
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "broker_order_id": "PAPER_1",
            "order_id": "PAPER_1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-04T16:00:33.498753",
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["paper"]["records"]["pending_orders"] == []


def test_strategy_dashboard_does_not_keep_d_close_signal_pending_after_execution_order(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "CLIENT-1",
            "order_type": "LIMIT",
            "price": 2.9060032716214454,
            "quantity": 200,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510050",
            "timestamp": "2026-06-08T15:00:00",
        }],
        [{
            "broker_order_id": "BROKER-1",
            "order_id": "BROKER-1",
            "order_type": "LIMIT",
            "price": 2.9090084456355316,
            "quantity": 200,
            "side": "BUY",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "510050",
            "timestamp": "2026-06-09T09:39:47.176570",
        }],
        [],
        as_of_date="2026-06-09",
    )

    assert pending == []


def test_strategy_dashboard_does_not_keep_retry_signal_pending_after_earlier_order(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "CLIENT-RETRY",
            "order_type": "LIMIT",
            "price": 4.7690324738177505,
            "quantity": 300,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-09T09:50:49.780552",
        }],
        [{
            "broker_order_id": "BROKER-1",
            "order_id": "BROKER-1",
            "order_type": "LIMIT",
            "price": 4.769,
            "quantity": 300,
            "side": "BUY",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-09T09:39:47.208000",
        }],
        [],
        as_of_date="2026-06-09",
    )

    assert pending == []


def test_strategy_dashboard_marks_submit_failure_in_pending_actions(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": None,
            "order_type": "LIMIT",
            "price": 1.887081939845566,
            "quantity": 100,
            "reason": "risk_check_failed",
            "side": "BUY",
            "status": "rejected",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-09T09:39:47.176570",
        }],
        [],
        [],
        as_of_date="2026-06-09",
    )

    assert pending[0]["display_status"] == "failed"
    assert pending[0]["submit_date"] == "2026-06-09"
    assert pending[0]["reason"] == "risk_check_failed"


def test_strategy_dashboard_expires_past_submit_date_from_pending_orders(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-03T15:00:00",
        }],
        [],
        [],
        as_of_date="2026-06-05",
    )

    assert pending == []


def test_strategy_dashboard_pending_orders_default_to_next_trading_date(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "SIGNAL-FRIDAY",
            "order_type": "LIMIT",
            "price": 10.0,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "timestamp": "2026-06-05T15:00:00",
        }],
        [],
        [],
        as_of_date="2026-06-08",
    )

    assert pending[0]["signal_date"] == "2026-06-05"
    assert pending[0]["submit_date"] == "2026-06-08"


def test_strategy_dashboard_pending_orders_display_cost_bps_instead_of_limit(tmp_path):
    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": None,
            "reference_price": 10.0,
            "execution_cost_bps": 25.0,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "timestamp": "2026-06-05T15:00:00",
        }],
        [],
        [],
        as_of_date="2026-06-08",
    )

    assert pending[0]["cost_bps"] == pytest.approx(25.0)
    assert pending[0]["cost_bps_display"] == "+25.0 bps"
    assert pending[0]["price"] is None


def test_strategy_dashboard_pending_orders_backfill_submit_bps_from_legacy_limit_price(tmp_path):
    db_path = tmp_path / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _write_daily_ohlc(db_path, "510300", "2026-06-05", 9.9, 10.0)

    pending = _pending_submit_orders(
        tmp_path,
        [{
            "order_id": "LEGACY-SIGNAL-1",
            "order_type": "LIMIT",
            "price": 10.025,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-05T15:00:00",
        }],
        [],
        [],
        as_of_date="2026-06-08",
    )

    assert pending[0]["cost_bps"] == pytest.approx(25.0)
    assert pending[0]["cost_bps_display"] == "+25.0 bps"


def test_strategy_dashboard_renders_only_state_appropriate_mode_actions():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "function renderModeActions(configured, controlState, mode)" in html
    assert "controlState === 'stopped' || controlState === 'liquidating'" in html
    assert "controlState === 'running'" in html


def test_strategy_dashboard_nav_tile_labels_current_execution_state():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "navSourceText(perf, mode)" in html
    assert "current fills + cash" in html
    assert "total_nav_source === 'current_execution_state'" in html


def test_strategy_dashboard_pending_table_renders_submit_bps_column():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "['Signal Date', 'Submit Date', 'Symbol', 'Side', 'Qty', 'Type', 'Submit +bps', 'Status', 'Order ID', 'Reason']" in html
    assert "submitBps(row)" in html
    assert "value === 'failed'" in html
    assert "controlState === 'paused'" in html
    assert "not configured in ${escapeHtml(modeLabel(mode))}" in html
    assert "already started" in html
    assert "Initial ${initialCash}" in html
    assert "Initial Cash ${initialCashText(modeInitialCash(strategy, mode))}" in html
    assert "Total Return" in html
    assert "perf.total_return" in html
    assert "Total PnL %" not in html
    assert "perf.total_pnl_pct" not in html
    assert "function initialCashText(value)" in html
    assert "function modeInitialCash(strategy, mode)" in html
    assert "strategy?.initial_cash?.default" in html
    assert "dashboard_asset_version" in html
    assert "window.location.reload()" in html
    assert "locked-cash" not in html
    assert "function renderUnconfiguredStart" in html
    assert "data-initial-cash-input" in html
    assert "initial_cash" in html


def test_strategy_dashboard_uses_mode_subpages_with_shared_components():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "mode: initialMode()" in html
    assert "window.location.pathname" in html
    assert "window.history.pushState(null, '', `/${state.mode}${window.location.search}`)" in html
    assert "function renderModePage(strategy, mode)" in html
    assert "${renderModePage(strategy, mode)}" in html
    assert "function renderModeNavigation(strategy, activeMode)" in html
    assert "data-mode-tab=\"${mode}\"" in html
    assert "renderModeControl(strategy, mode)" in html
    assert "renderMetricsTable(strategy, mode)" in html
    assert "drawCurve(selected, state.mode)" in html
    assert "filterCurveByStart(" in html
    assert "alignSeriesDates(series)" in html
    assert "function alignSeriesDates(series)" in html
    assert "carried: true" in html
    assert "curveDateDomain(series)" in html
    assert "function curveX(dateText, domain, pad, width)" in html
    assert "const index = domain.dates.indexOf(dateKey)" in html
    assert "baseValue: strategyInitialCash" in html
    assert "explicitBase > 0 ? explicitBase : points[0].value" in html
    assert "width / 2" not in html
    assert "renderSplitTables" not in html
    assert "modeCard(" not in html


def test_strategy_dashboard_serves_live_and_paper_subpages(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    client = create_app(tmp_path).test_client()

    root = client.get("/")
    live = client.get("/live")
    paper = client.get("/paper")
    dashboard = client.get("/api/dashboard")

    assert root.status_code == 200
    assert live.status_code == 200
    assert paper.status_code == 200
    assert dashboard.status_code == 200
    body = dashboard.get_json()
    assert body["dashboard_asset_version"] != "missing"
    assert "expected_market_data_date" in body["freshness"]
    assert set(body["scheduled_jobs"]) == {"data_update", "live_recovery", "live_pending", "paper_replay"}
    assert b"strategy_dashboard" in root.data or b"mode-tabs" in root.data
    assert b"strategy_dashboard" in live.data or b"mode-tabs" in live.data
    assert b"strategy_dashboard" in paper.data or b"mode-tabs" in paper.data


def test_strategy_dashboard_launcher_opens_current_port_and_restarts_stale_payload():
    script = Path("quant/scripts/open_strategy_dashboard.ps1").read_text(encoding="utf-8")
    shortcut = Path("策略管理看板.cmd").read_text(encoding="utf-8")

    assert "[int]$Port = 8791" in script
    assert "-Port 8791" in shortcut
    assert "$DashboardUrl = \"${Url}api/dashboard\"" in script
    assert "function Test-DashboardCompatible" in script
    assert "$Strategy.initial_cash" in script
    assert "$Strategy.live.initial_cash" in script
    assert "$Strategy.paper.initial_cash" in script
    assert "Get-NetTCPConnection" in script
    assert "Stop-DashboardOnPort" in script


def test_strategy_dashboard_surfaces_scheduled_job_failures(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    log_dir = tmp_path / "logs" / "scheduled"
    html_path.parent.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    (log_dir / "qmt_live_daily_20260604.log").write_text(
        "\n".join([
            "2026-06-04 16:00:12 qmt live daily start",
            "RuntimeError: No daily bars loaded for 2026-06-04",
            "2026-06-04 16:00:15 qmt live daily exit_code=1 paper_exit_code=0",
        ]),
        encoding="utf-16",
    )

    body = create_app(tmp_path).test_client().get("/api/dashboard").get_json()

    live_job = body["scheduled_jobs"]["live_pending"]
    assert live_job["status"] == "failed"
    assert live_job["exit_code"] == 1
    assert live_job["error"] == "RuntimeError: No daily bars loaded for 2026-06-04"


def test_strategy_dashboard_surfaces_live_recovery_job_status(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    log_dir = tmp_path / "logs" / "scheduled"
    html_path.parent.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    (log_dir / "qmt_live_recovery_20260605.log").write_text(
        "\n".join([
            "2026-06-05 23:05:00 qmt live recovery start read_only=true dry_run=False",
            "2026-06-05 23:05:02 qmt live recovery exit_code=0",
        ]),
        encoding="utf-8",
    )

    body = create_app(tmp_path).test_client().get("/api/dashboard").get_json()

    recovery_job = body["scheduled_jobs"]["live_recovery"]
    assert recovery_job["status"] == "ok"
    assert recovery_job["exit_code"] == 0


def test_strategy_dashboard_allocation_endpoint_rejects_configured_cash_change(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_config.mkdir(parents=True)
    config_path = live_config / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )

    app = create_app(root)
    res = app.test_client().post(
        "/api/strategies/DemoStrategy/allocation",
        json={"initial_cash": 35000},
    )

    assert res.status_code == 409
    body = res.get_json()
    assert body["allocation_locked"] is True
    assert body["current_allocation"]["live"]["initial_cash"] == pytest.approx(20000.0)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["strategies"][0]["initial_cash"] == pytest.approx(20000.0)
    assert "allocation_cash" not in config["strategies"][0]
    payload = build_dashboard_payload(root)
    assert payload["strategies"][0]["live"]["initial_cash"] == pytest.approx(20000.0)


def test_strategy_dashboard_start_unconfigured_mode_assigns_initial_cash(tmp_path):
    root = tmp_path
    strategy_dir = root / "quant" / "features" / "strategies" / "DemoStrategy"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    strategy_dir.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("class DemoStrategy: pass\n", encoding="utf-8")
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": []}),
        encoding="utf-8",
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "paper", "initial_cash": 25000},
    )

    assert res.status_code == 200
    body = res.get_json()["control"]
    assert body["mode"] == "paper"
    assert body["live_state"] == "running"
    config = yaml.safe_load((paper_config / "config.yaml").read_text(encoding="utf-8"))
    assert config["strategies"] == [{"name": "DemoStrategy", "enabled": True, "initial_cash": 25000.0}]
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["paper"]["configured"] is True
    assert strategy["paper"]["initial_cash"] == pytest.approx(25000.0)
    assert strategy["paper"]["state"]["run"]["initial_cash"] == pytest.approx(25000.0)
    assert strategy["paper"]["state"]["control_state"]["current_run_id"] == strategy["paper"]["state"]["run"]["run_id"]


def test_strategy_dashboard_start_unconfigured_mode_requires_initial_cash(tmp_path):
    root = tmp_path
    strategy_dir = root / "quant" / "features" / "strategies" / "DemoStrategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("class DemoStrategy: pass\n", encoding="utf-8")

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "live"},
    )

    assert res.status_code == 400
    assert "initial_cash" in res.get_json()["error"]


def test_strategy_dashboard_control_endpoint_updates_modes_independently(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    app = create_app(root)
    client = app.test_client()

    live_res = client.post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "pause", "mode": "live"},
    )
    paper_res = client.post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "resume", "mode": "paper"},
    )

    assert live_res.status_code == 200
    assert paper_res.status_code == 200
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    control = json.loads(control_path.read_text(encoding="utf-8"))
    assert control["strategies"]["DemoStrategy"]["mode"] == "live"
    assert control["strategies"]["DemoStrategy"]["live_state"] == "paused"
    assert control["paper_strategies"]["DemoStrategy"]["mode"] == "paper"
    assert control["paper_strategies"]["DemoStrategy"]["live_state"] == "running"
    audit_path = root / "quant" / "infrastructure" / "var" / "strategy_audit.jsonl"
    audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [(row["mode"], row["action"]) for row in audit_rows[-2:]] == [
        ("live", "pause"),
        ("paper", "resume"),
    ]
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["live"]["accepts_signals"] is False
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["live"]["ledger"]["mode"] == "live"
    assert strategy["paper"]["ledger"]["mode"] == "paper"
    assert payload["operations_health"]["status"] in {"ok", "warning"}


def test_strategy_dashboard_start_action_enables_paper_mode(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    paper_config.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    control_path.write_text(
        json.dumps({
            "paper_strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "mode": "paper",
                    "live_enabled": False,
                    "live_state": "stopped",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-04T15:00:00",
                }
            }
        }),
        encoding="utf-8",
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "paper"},
    )

    assert res.status_code == 200
    body = res.get_json()["control"]
    assert body["mode"] == "paper"
    assert body["live_state"] == "running"
    assert body["live_enabled"] is True
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["paper"]["control"]["live_state"] == "running"


def test_strategy_dashboard_liquidate_stop_creates_mode_scoped_plan(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    live_config.mkdir(parents=True)
    live_positions_path.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    live_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 10.0,
                        "market_value": 1100.0,
                        "unrealized_pnl": 100.0,
                    }
                }
            },
            "realized_pnl": {},
            "order_map": {},
        }),
        encoding="utf-8",
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "liquidate_stop", "mode": "live", "note": "user requested clear"},
    )

    assert res.status_code == 200
    plan = res.get_json()["liquidation_plan"]
    assert plan["mode"] == "live"
    assert plan["strategy_name"] == "DemoStrategy"
    assert plan["orders"] == [{
        "symbol": "600519",
        "side": "SELL",
        "quantity": 100.0,
        "avg_cost": 10.0,
        "market_value": 1100.0,
    }]
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["live"]["control"]["live_state"] == "liquidating"
    assert strategy["live"]["liquidation_plan"]["status"] == "planned"


def _write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_daily_ohlc(path: Path, symbol: str, trading_date: str, open_price: float, close: float):
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            create table if not exists daily_cn_ochl (
                timestamp timestamp,
                symbol varchar,
                open double,
                close double
            )
            """
        )
        con.execute(
            "insert into daily_cn_ochl values (?, ?, ?, ?)",
            [trading_date, symbol, open_price, close],
        )
    finally:
        con.close()
