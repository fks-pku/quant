from datetime import date, datetime

import pytest

from quant.domain.models.order import Order, OrderSide, OrderType
from quant.features.portfolio.tracker import StrategyPositionTracker
from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_controls import apply_strategy_control_action
from quant.infrastructure.execution.strategy_ledger import build_strategy_mode_ledger, sync_broker_trade_history
from quant.infrastructure.execution.strategy_mode_records import StrategyModeRecordStore
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


class HistoryBroker:
    def __init__(self, trades, orders=None):
        self.trades = trades
        self.orders = orders or []
        self.calls = []
        self.order_calls = []

    def get_trade_history(self, start_date=None, end_date=None):
        self.calls.append((start_date, end_date))
        return list(self.trades)

    def get_order_history(self, start_date=None, end_date=None):
        self.order_calls.append((start_date, end_date))
        return list(self.orders)

    def estimate_commission(self, symbol, side, quantity, price):
        return 5.0


def test_strategy_mode_record_store_isolates_strategy_and_mode(tmp_path):
    store = StrategyModeRecordStore(tmp_path / "strategy_modes")

    store.append(
        "signals",
        mode="live",
        strategy_name="DemoStrategy",
        record={"timestamp": "2026-06-03T15:00:00", "symbol": "600519", "side": "BUY", "quantity": 100},
    )
    store.append(
        "signals",
        mode="paper",
        strategy_name="DemoStrategy",
        record={"timestamp": "2026-06-03T15:00:00", "symbol": "000001", "side": "BUY", "quantity": 200},
    )
    store.append(
        "signals",
        mode="live",
        strategy_name="OtherStrategy",
        record={"timestamp": "2026-06-03T15:00:00", "symbol": "000002", "side": "BUY", "quantity": 300},
    )

    live_demo = store.read("signals", mode="live", strategy_name="DemoStrategy")
    paper_demo = store.read("signals", mode="paper", strategy_name="DemoStrategy")
    live_other = store.read("signals", mode="live", strategy_name="OtherStrategy")

    assert [row["symbol"] for row in live_demo] == ["600519"]
    assert [row["symbol"] for row in paper_demo] == ["000001"]
    assert [row["symbol"] for row in live_other] == ["000002"]
    assert store.strategy_names("live") == ["DemoStrategy", "OtherStrategy"]
    assert store.latest_record_date("live") == "2026-06-03"


def test_strategy_mode_record_store_reads_utf8_sig_jsonl(tmp_path):
    store = StrategyModeRecordStore(tmp_path / "strategy_modes")
    path = store.path("snapshots", mode="paper", strategy_name="DemoStrategy")
    path.parent.mkdir(parents=True)
    path.write_text(
        '\ufeff{"timestamp":"2026-06-05T15:00:00","date":"2026-06-05","nav":20000}\n',
        encoding="utf-8",
    )

    rows = store.read("snapshots", mode="paper", strategy_name="DemoStrategy")

    assert rows[0]["date"] == "2026-06-05"
    assert rows[0]["nav"] == 20000


def test_strategy_state_store_persists_flexible_runtime_checkpoint(tmp_path):
    store = StrategyStateStore(tmp_path / "strategy_dashboard.duckdb")

    row = store.upsert_runtime_state(
        strategy_name="DemoStrategy",
        mode="paper",
        as_of_date="2026-06-17",
        stage="post_signal_close",
        strategy_class="DemoStrategy",
        state={
            "positions": {"510300": 300.0},
            "daily_bar_state": {
                "last_rebalance_date": "2026-06-17",
                "days_since_rebalance": 0,
            },
            "strategy_specific": {"branch": "risk_on", "weights": {"510300": 0.6}},
        },
        schema_version=1,
        config_hash="cfg-a",
        run_id="run-a",
        recorded_at="2026-06-17T15:00:00",
    )

    restored = store.get_latest_runtime_state(
        strategy_name="DemoStrategy",
        mode="paper",
    )

    assert restored is not None
    assert restored["state_id"] == row["state_id"]
    assert restored["state_hash"] == row["state_hash"]
    assert restored["state"]["strategy_specific"]["weights"]["510300"] == pytest.approx(0.6)
    assert restored["config_hash"] == "cfg-a"
    assert restored["run_id"] == "run-a"


@pytest.mark.skip(reason="JSONL mode_records removed in simplified state store")
def test_strategy_control_action_writes_mode_operation(tmp_path):
    control_file = tmp_path / "var" / "strategy_controls.json"

    apply_strategy_control_action(
        "DemoStrategy",
        "start",
        control_file,
        mode="paper",
        default_live_enabled=True,
        now=datetime(2026, 6, 3, 9, 30),
    )

    store = StrategyModeRecordStore(tmp_path / "var" / "strategy_modes")
    operations = store.read("operations", mode="paper", strategy_name="DemoStrategy")
    assert len(operations) == 1
    assert operations[0]["action"] == "start"
    assert operations[0]["payload"]["control"]["live_state"] == "running"
    assert store.read("operations", mode="live", strategy_name="DemoStrategy") == []


@pytest.mark.skip(reason="old record_operation/read methods removed")
def test_strategy_state_store_records_strict_run_control_and_facts(tmp_path):
    store = StrategyStateStore(tmp_path / "strategy_state.duckdb")

    operation = store.record_operation(
        mode="live",
        strategy_name="DemoStrategy",
        operation_type="start",
        requested_by="dashboard",
        requested_at="2026-06-03T09:30:00",
        params={"initial_cash": 25000},
    )
    run = store.ensure_run(
        mode="live",
        strategy_name="DemoStrategy",
        initial_cash=25000,
        started_at="2026-06-03T09:30:00",
        operation_id=operation["operation_id"],
    )
    store.record_control_state(
        mode="live",
        strategy_name="DemoStrategy",
        lifecycle_state="running",
        signal_enabled=True,
        submit_enabled=True,
        current_run_id=run["run_id"],
        last_operation_id=operation["operation_id"],
        timestamp="2026-06-03T09:30:00",
    )
    signal = {
        "timestamp": "2026-06-03T15:00:00",
        "strategy_name": "DemoStrategy",
        "order_id": "SIG-1",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100,
        "order_type": "MARKET",
        "status": "accepted",
    }
    store.migrate_records(
        mode="live",
        strategy_name="DemoStrategy",
        records={
            "signals": [signal],
            "orders": [{
                "timestamp": "2026-06-04T09:30:00",
                "strategy_name": "DemoStrategy",
                "order_id": "BRK-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "price": 10.0,
                "status": "submitted",
            }],
            "fills": [{
                "timestamp": "2026-06-04T09:31:00",
                "strategy_name": "DemoStrategy",
                "order_id": "BRK-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "price": 10.0,
                "commission": 1.0,
            }],
            "snapshots": [{
                "timestamp": "2026-06-04T15:00:00",
                "date": "2026-06-04",
                "strategy_name": "DemoStrategy",
                "nav": 25010.0,
                "cash": 24000.0,
                "market_value": 1010.0,
            }],
        },
        run_id=run["run_id"],
    )
    first_count = len(store.read("signals", mode="live", strategy_name="DemoStrategy"))
    store.migrate_records(
        mode="live",
        strategy_name="DemoStrategy",
        records={"signals": [signal]},
        run_id=run["run_id"],
    )

    records = store.read_records(mode="live", strategy_name="DemoStrategy")
    assert records["operations"][0]["operation_type"] == "start"
    assert records["runs"][0]["initial_cash"] == pytest.approx(25000.0)
    assert records["control_state"][-1]["current_run_id"] == run["run_id"]
    assert records["signals"][0]["run_id"] == run["run_id"]
    assert records["orders"][0]["run_id"] == run["run_id"]
    assert records["fills"][0]["run_id"] == run["run_id"]
    assert records["snapshots"][0]["run_id"] == run["run_id"]
    assert len(records["signals"]) == first_count
    assert store.latest_record_date("live") == "2026-06-04"


@pytest.mark.skip(reason="old strict run/control_state methods removed")
def test_strategy_control_action_writes_strict_run_and_control_state(tmp_path):
    control_file = tmp_path / "var" / "strategy_controls.json"

    apply_strategy_control_action(
        "DemoStrategy",
        "start",
        control_file,
        mode="paper",
        default_live_enabled=True,
        initial_cash=30000.0,
        now=datetime(2026, 6, 3, 9, 30),
    )

    state_store = StrategyStateStore(tmp_path / "var" / "strategy_state.duckdb")
    records = state_store.read_records(mode="paper", strategy_name="DemoStrategy")
    assert records["operations"][-1]["operation_type"] == "start"
    assert records["runs"][-1]["initial_cash"] == pytest.approx(30000.0)
    assert records["control_state"][-1]["lifecycle_state"] == "running"
    assert records["control_state"][-1]["current_run_id"] == records["runs"][-1]["run_id"]


@pytest.mark.skip(reason="reconciliations table removed from state store")
def test_sync_broker_trade_history_imports_missing_fills_once(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    store = StrategyStateStore(tmp_path / "strategy_dashboard.duckdb")
    tracker = StrategyPositionTracker(store=store, mode="live")
    tracker.record_order("BRK-1", "DemoStrategy")
    broker = HistoryBroker([
        {
            "order_id": "BRK-1",
            "trade_id": "FILL-1",
            "timestamp": datetime(2026, 6, 3, 9, 35),
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "price": 10.0,
            "commission": 1.0,
        }
    ])

    first = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=tracker,
        mode="live",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 4),
    )
    second = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=tracker,
        mode="live",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 4),
    )

    assert first["broker_history_supported"] is True
    assert first["imported_count"] == 1
    assert first["unresolved_count"] == 0
    assert second["imported_count"] == 0
    fills = recorder.read_day("fills", "2026-06-03", strategy_name="DemoStrategy")
    assert len(fills) == 1
    assert fills[0]["fill_id"] == "FILL-1"
    positions = tracker.get_positions_for_strategy("DemoStrategy")
    assert positions["600519"]["qty"] == 100
    assert positions["600519"]["avg_cost"] == 10.01


def test_sync_broker_trade_history_imports_filled_order_history_when_trades_empty(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    store = StrategyStateStore(tmp_path / "strategy_dashboard.duckdb")
    tracker = StrategyPositionTracker(store=store, mode="live")
    tracker.record_order("BRK-FILLED", "DemoStrategy")
    recorder.record_order(
        Order(
            symbol="510300",
            quantity=300,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_id="BRK-FILLED",
            price=4.769,
            timestamp=datetime(2026, 6, 9, 9, 39, 47),
            strategy_name="DemoStrategy",
        ),
        broker_order_id="BRK-FILLED",
        status="submitted",
        timestamp=datetime(2026, 6, 9, 9, 39, 47),
    )
    broker = HistoryBroker(
        [],
        orders=[{
            "order_id": "BRK-FILLED",
            "timestamp": datetime(2026, 6, 9, 9, 39, 47),
            "strategy_name": "Demo",
            "symbol": "510300",
            "side": "",
            "quantity": 300,
            "price": 4.769,
            "status": "56",
        }],
    )

    first = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=tracker,
        mode="live",
        start_date=date(2026, 6, 9),
        end_date=date(2026, 6, 9),
    )
    second = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=tracker,
        mode="live",
        start_date=date(2026, 6, 9),
        end_date=date(2026, 6, 9),
    )

    assert first["broker_history_supported"] is True
    assert first["imported_count"] == 1
    assert first["order_history_imported_count"] == 1
    assert second["imported_count"] == 0
    fills = recorder.read_day("fills", "2026-06-09", strategy_name="DemoStrategy")
    assert len(fills) == 1
    assert fills[0]["order_id"] == "BRK-FILLED"
    assert fills[0]["fill_time"] == "2026-06-09T09:39:47"
    assert fills[0]["side"] == "BUY"
    assert fills[0]["commission"] == 5.0
    assert fills[0]["fill_quantity"] * fills[0]["fill_price"] == pytest.approx(1430.7)
    positions = tracker.get_positions_for_strategy("DemoStrategy")
    assert positions["510300"]["qty"] == 300
    assert positions["510300"]["avg_cost"] == pytest.approx((300 * 4.769 + 5.0) / 300)


def test_sync_broker_trade_history_recovers_strategy_from_persisted_order_row(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    store = StrategyStateStore(tmp_path / "strategy_dashboard.duckdb")
    tracker = StrategyPositionTracker(store=store, mode="live")
    recorder.record_order(
        Order(
            symbol="510880",
            quantity=200,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_id="BRK-PERSISTED",
            price=3.243,
            timestamp=datetime(2026, 6, 16, 9, 30, 6),
            strategy_name="DemoStrategy",
        ),
        broker_order_id="BRK-PERSISTED",
        status="submitted",
        timestamp=datetime(2026, 6, 16, 9, 30, 6),
    )
    restarted_tracker = StrategyPositionTracker(store=store, mode="live")
    broker = HistoryBroker([
        {
            "order_id": "BRK-PERSISTED",
            "trade_id": "FILL-PERSISTED",
            "timestamp": datetime(2026, 6, 16, 9, 30, 6),
            "symbol": "510880",
            "side": "BUY",
            "quantity": 200,
            "price": 3.243,
            "commission": 5.0,
        }
    ])

    result = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=restarted_tracker,
        mode="live",
        start_date=date(2026, 6, 16),
        end_date=date(2026, 6, 16),
    )

    assert result["imported_count"] == 1
    assert result["unresolved_count"] == 0
    fills = recorder.read_day("fills", "2026-06-16", strategy_name="DemoStrategy")
    assert fills[0]["order_id"] == "BRK-PERSISTED"
    positions = restarted_tracker.get_positions_for_strategy("DemoStrategy")
    assert positions["510880"]["qty"] == 200
    assert positions["510880"]["avg_cost"] == pytest.approx((200 * 3.243 + 5.0) / 200)
    assert restarted_tracker.get_positions_for_strategy("default") == {}


def test_sync_broker_trade_history_marks_unknown_order_attribution(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    store = StrategyStateStore(tmp_path / "strategy_dashboard.duckdb")
    tracker = StrategyPositionTracker(store=store, mode="live")
    broker = HistoryBroker([
        {
            "order_id": "BRK-UNKNOWN",
            "trade_id": "FILL-UNKNOWN",
            "timestamp": "2026-06-03T10:00:00",
            "symbol": "000001",
            "side": "SELL",
            "quantity": 100,
            "price": 12.0,
            "commission": 0.5,
        }
    ])

    result = sync_broker_trade_history(
        broker=broker,
        recorder=recorder,
        tracker=tracker,
        mode="live",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 4),
    )

    assert result["imported_count"] == 1
    assert result["unresolved_count"] == 1
    fills = recorder.read_day("fills", "2026-06-03", strategy_name="default")
    assert fills[0]["order_id"] == "BRK-UNKNOWN"


def test_strategy_ledger_snapshot_marker_satisfies_no_action_daily_run():
    ledger = build_strategy_mode_ledger(
        strategy_name="DemoStrategy",
        mode="live",
        configured=True,
        initial_cash=20000.0,
        control={"live_enabled": True, "live_state": "running"},
        records={
            "signals": [{"timestamp": "2026-06-03T15:00:00", "strategy_name": "DemoStrategy"}],
            "orders": [],
            "fills": [],
            "snapshots": [{"timestamp": "2026-06-04T15:00:00", "strategy_name": "DemoStrategy"}],
            "pending_orders": [],
        },
        positions_data={"positions": {"DemoStrategy": {}}},
        latest_market_data_date="2026-06-04",
        latest_record_date="2026-06-04",
    )

    assert ledger["latest_signal_date"] == "2026-06-03"
    assert ledger["latest_snapshot_date"] == "2026-06-04"
    assert ledger["latest_run_date"] == "2026-06-04"
    assert ledger["missing_signal_dates"] == []
    assert ledger["health_status"] == "ok"
