from datetime import datetime

import pytest

from quant.features.trading.dashboard_projection import (
    project_pending_orders,
    project_fill_rows,
    project_signal_rows,
    project_holdings,
    project_order_rows,
    project_execution_summary,
    project_performance,
    project_run_status_bar,
)


def test_dashboard_projection_blocks_curve_snapshot_after_latest_market_data():
    raw_performance = {
        "pnl_curve": [{
            "date": "2026-06-11",
            "timestamp": "2026-06-11T15:00:00",
            "nav": 20000.0,
            "cash": 10000.0,
            "market_value": 10000.0,
        }],
        "latest_snapshot": {
            "date": "2026-06-11",
            "timestamp": "2026-06-11T15:00:00",
            "nav": 20000.0,
        },
    }
    holdings = {
        "initial_cash": 20000.0,
        "cash_source": "position_baseline",
        "cash": 8372.9,
        "nav": 20164.2,
        "total_market_value": 11791.3,
        "total_cost": 11627.1,
        "total_pnl": 164.2,
        "realized_pnl": 0.0,
        "unrealized_pnl": 164.2,
        "price_date": None,
        "latest_activity_date": "2026-06-12",
    }

    performance = project_performance(
        strategy_name="GoldBarbell",
        raw_performance=raw_performance,
        holdings=holdings,
        latest_market_data_date="2026-06-11",
        now=datetime(2026, 6, 12, 20, 0, 0),
    )

    assert [point["date"] for point in performance["pnl_curve"]] == ["2026-06-11"]
    assert performance["latest_snapshot"]["date"] == "2026-06-11"
    assert performance["total_nav"] == pytest.approx(20164.2)
    assert performance["cash"] == pytest.approx(8372.9)
    assert performance["total_nav_source"] == "position_baseline"


def test_dashboard_projection_appends_holdings_snapshot_only_on_valid_market_date():
    raw_performance = {"pnl_curve": []}
    holdings = {
        "initial_cash": 10000.0,
        "cash_source": "initial_cash",
        "cash": 10000.0,
        "nav": 10000.0,
        "total_market_value": 0.0,
        "total_cost": 0.0,
        "total_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "price_date": "2026-06-11",
        "latest_activity_date": "",
    }

    performance = project_performance(
        strategy_name="CashOnly",
        raw_performance=raw_performance,
        holdings=holdings,
        latest_market_data_date="2026-06-11",
        now=datetime(2026, 6, 11, 15, 30, 0),
    )

    assert [point["date"] for point in performance["pnl_curve"]] == ["2026-06-11"]
    assert performance["latest_snapshot"]["date"] == "2026-06-11"
    assert performance["total_nav"] == pytest.approx(10000.0)


def test_dashboard_projection_uses_db_position_total_when_intraday_fill_is_partial():
    holdings = project_holdings(
        stored_positions={
            "159949": {
                "qty": 1200.0,
                "avg_cost": 1.8738333333,
            }
        },
        fills=[],
        latest_prices={"159949": {"date": "2026-06-11", "price": 1.85, "source": "duckdb"}},
        order_rows=[{
            "timestamp": "2026-06-12T09:35:30",
            "symbol": "159949",
            "side": "BUY",
            "filled_qty": 400.0,
            "fill_price": 1.874,
            "commission": 5.0,
        }],
        initial_cash=20000.0,
    )

    expected_cost = 1200.0 * 1.8738333333
    expected_market_value = 1200.0 * 1.874
    item = holdings["items"][0]

    assert holdings["cash_source"] == "position_baseline"
    assert item["qty"] == pytest.approx(1200.0)
    assert item["valuation_status"] == "unmarked_after_activity"
    assert item["current_price"] == pytest.approx(1.874)
    assert holdings["cash"] == pytest.approx(20000.0 - expected_cost)
    assert holdings["total_market_value"] == pytest.approx(expected_market_value)
    assert holdings["nav"] == pytest.approx(20000.0 - expected_cost + expected_market_value)
    assert holdings["price_date"] is None
    assert holdings["latest_activity_date"] == "2026-06-12"


def test_dashboard_projection_run_status_no_signal_days_are_green():
    status_bar = project_run_status_bar(
        dates=["2026-06-10", "2026-06-11", "2026-06-12"],
        configured=True,
        control={"live_enabled": True, "live_state": "running", "liquidation_requested": False},
        records={"signals": [], "orders": [], "fills": [], "snapshots": []},
        latest_market_data_date="2026-06-12",
    )

    assert status_bar["status"] == "ok"
    assert [(item["date"], item["key"]) for item in status_bar["timeline"]] == [
        ("2026-06-10", "DATA_READY"),
        ("2026-06-10", "SIGNAL_READY"),
        ("2026-06-11", "ORDER_SUBMITTED"),
        ("2026-06-11", "DATA_READY"),
        ("2026-06-11", "SIGNAL_READY"),
        ("2026-06-12", "ORDER_SUBMITTED"),
        ("2026-06-12", "DATA_READY"),
        ("2026-06-12", "SIGNAL_READY"),
    ]
    assert all(item["status"] == "ok" for item in status_bar["timeline"])
    assert status_bar["days"][0]["checkpoints"][1]["message"] == "no signal"


def test_dashboard_projection_run_status_partial_fill_is_warning():
    status_bar = project_run_status_bar(
        dates=["2026-06-10", "2026-06-11"],
        configured=True,
        control={"live_enabled": True, "live_state": "running", "liquidation_requested": False},
        records={
            "signals": [{
                "timestamp": "2026-06-10T15:00:00",
                "signal_date": "2026-06-10",
                "projected_submit_date": "2026-06-11",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100.0,
                "order_type": "LIMIT",
                "reference_price": 10.0,
                "status": "accepted",
                "order_id": "CLIENT-1",
            }],
            "orders": [{
                "timestamp": "2026-06-11T09:31:00",
                "record_date": "2026-06-11",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100.0,
                "filled_qty": 50.0,
                "status": "partial",
                "order_id": "CLIENT-1",
            }],
            "fills": [],
            "snapshots": [],
        },
        latest_market_data_date="2026-06-11",
    )

    order = status_bar["timeline"][2]

    assert status_bar["status"] == "warning"
    assert order["key"] == "ORDER_SUBMITTED"
    assert order["status"] == "warning"
    assert order["message"] == "partial fill"
    assert order["observed"] == "submitted=100 filled=50 for 1 signal(s)"
    assert order["details"][0]["status"] == "partial"


def test_dashboard_projection_pending_orders_expire_past_submit_date():
    pending = project_pending_orders(
        signals=[{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 2.01,
            "quantity": 100,
            "side": "SELL",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-03T15:00:00",
            "projected_submit_date": "2026-06-04",
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-05",
    )

    assert pending == []


def test_dashboard_projection_pending_orders_do_not_duplicate_submitted_orders():
    pending = project_pending_orders(
        signals=[{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 10.0,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "timestamp": "2026-06-05T15:00:00",
            "projected_submit_date": "2026-06-08",
        }],
        orders=[{
            "order_id": "BROKER-1",
            "order_type": "LIMIT",
            "price": 10.01,
            "quantity": 100,
            "side": "BUY",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "record_date": "2026-06-08",
            "timestamp": "2026-06-08T09:31:00",
        }],
        fills=[],
        as_of_date="2026-06-08",
    )

    assert pending == []


def test_dashboard_projection_pending_orders_backfill_submit_bps_from_signal_close():
    pending = project_pending_orders(
        signals=[{
            "order_id": "LEGACY-SIGNAL-1",
            "order_type": "LIMIT",
            "price": 10.025,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-05T15:00:00",
            "projected_submit_date": "2026-06-08",
            "signal_close_price": 10.0,
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-08",
    )

    assert pending[0]["signal_date"] == "2026-06-05"
    assert pending[0]["submit_date"] == "2026-06-08"
    assert pending[0]["cost_bps"] == pytest.approx(25.0)
    assert pending[0]["cost_bps_display"] == "+25.0 bps"


def test_dashboard_projection_signal_rows_hide_submit_attempts():
    rows = project_signal_rows(
        signals=[
            {
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
                "projected_submit_date": "2026-06-09",
            },
            {
                "timestamp": "2026-06-09T09:39:47.208000",
                "strategy_name": "DemoStrategy",
                "order_id": "BRK-1",
                "symbol": "510300",
                "side": "BUY",
                "quantity": 300,
                "order_type": "LIMIT",
                "price": 4.769,
                "status": "accepted",
                "projected_submit_date": "2026-06-09",
            },
        ],
        orders=[{
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
        fills=[],
    )

    assert [row["timestamp"] for row in rows] == ["2026-06-08T15:00:00"]


def test_dashboard_projection_order_rows_use_actual_fill_price_and_slippage():
    fills = project_fill_rows(
        mode="paper",
        orders=[{
            "timestamp": "2026-06-09T09:31:00",
            "order_id": "ORDER-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 100,
            "price": 10.05,
            "record_date": "2026-06-09",
        }],
        fills=[{
            "timestamp": "2026-06-09T09:31:01",
            "order_id": "ORDER-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 100,
            "price": 9.99,
            "commission": 0.5,
            "record_date": "2026-06-09",
        }],
        commission_config={},
    )
    orders = project_order_rows(
        mode="paper",
        orders=[{
            "timestamp": "2026-06-09T09:31:00",
            "order_id": "ORDER-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 100,
            "price": 10.05,
            "record_date": "2026-06-09",
        }],
        fills=fills,
        open_prices={("510300", "2026-06-09"): 10.0},
        commission_config={},
    )
    summary = project_execution_summary(orders, fills)

    assert fills[0]["commission"] == pytest.approx(0.5)
    assert orders[0]["limit_price"] == pytest.approx(10.05)
    assert orders[0]["open_price"] == pytest.approx(10.0)
    assert orders[0]["fill_price"] == pytest.approx(9.99)
    assert orders[0]["raw_fill_price"] == pytest.approx(9.99)
    assert orders[0]["display_contract"] == "actual_fill"
    assert orders[0]["display_status"] == "filled"
    assert orders[0]["slippage_bps"] == pytest.approx(-10.0)
    assert summary["total_commission"] == pytest.approx(0.5)
    assert summary["median_slippage_bps"] == pytest.approx(-10.0)


def test_dashboard_projection_order_rows_use_persisted_execution_reference_open():
    fills = project_fill_rows(
        mode="live",
        orders=[{
            "timestamp": "2026-06-16T09:30:00",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "price": 10.02,
            "record_date": "2026-06-16",
        }],
        fills=[{
            "timestamp": "2026-06-16T09:30:06",
            "order_id": "BRK-1",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "price": 10.01,
            "commission": 5.0,
            "record_date": "2026-06-16",
        }],
        commission_config={},
    )

    orders = project_order_rows(
        mode="live",
        orders=[{
            "timestamp": "2026-06-16T09:30:00",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "strategy_name": "DemoStrategy",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "order_type": "LIMIT",
            "price": 10.02,
            "execution_reference_price": 10.0,
            "record_date": "2026-06-16",
        }],
        fills=fills,
        signals=[],
        open_prices={("510880", "2026-06-16"): 9.99},
        commission_config={},
    )

    assert orders[0]["open_price"] == pytest.approx(10.0)
    assert orders[0]["limit_price"] == pytest.approx(10.02)
    assert orders[0]["fill_price"] == pytest.approx(10.01)
    assert orders[0]["slippage_bps"] == pytest.approx(10.0)


def test_dashboard_projection_order_rows_infer_open_from_execution_cost_bps():
    fills = project_fill_rows(
        mode="live",
        orders=[{
            "timestamp": "2026-06-16T09:30:00",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "price": 10.02,
            "record_date": "2026-06-16",
        }],
        fills=[{
            "timestamp": "2026-06-16T09:30:06",
            "order_id": "BRK-1",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "price": 10.01,
            "commission": 5.0,
            "record_date": "2026-06-16",
        }],
        commission_config={},
    )

    orders = project_order_rows(
        mode="live",
        orders=[{
            "timestamp": "2026-06-16T09:30:00",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "strategy_name": "DemoStrategy",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "order_type": "LIMIT",
            "price": 10.02,
            "record_date": "2026-06-16",
        }],
        fills=fills,
        signals=[{
            "timestamp": "2026-06-15T15:00:00",
            "strategy_name": "DemoStrategy",
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "order_type": "LIMIT",
            "reference_price": 10.0,
            "status": "accepted",
            "signal_date": "2026-06-15",
            "submit_date": "2026-06-16",
            "cost_bps": 20.0,
        }],
        open_prices={},
        commission_config={},
    )

    assert orders[0]["open_price"] == pytest.approx(10.0)
    assert orders[0]["fill_price"] == pytest.approx(10.01)
    assert orders[0]["slippage_bps"] == pytest.approx(10.0)


def test_dashboard_projection_due_paper_signal_without_fill_displays_no_fill_order():
    orders = project_order_rows(
        mode="paper",
        orders=[],
        fills=[],
        signals=[{
            "timestamp": "2026-06-11T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "SIGNAL-1",
            "symbol": "518880",
            "side": "BUY",
            "quantity": 1100,
            "order_type": "LIMIT",
            "reference_price": 8.506,
            "status": "accepted",
            "submit_date": "2026-06-12",
            "signal_date": "2026-06-11",
        }],
        open_prices={("518880", "2026-06-12"): 8.55},
        as_of_date="2026-06-12",
        commission_config={},
    )

    assert len(orders) == 1
    assert orders[0]["record_date"] == "2026-06-12"
    assert orders[0]["signal_date"] == "2026-06-11"
    assert orders[0]["display_status"] == "no_fill"
    assert orders[0]["filled_qty"] == pytest.approx(0.0)
    assert orders[0]["fill_price"] is None
    assert orders[0]["open_price"] == pytest.approx(8.55)
