import json
from datetime import date, datetime

from quant.features.portfolio.tracker import StrategyPositionTracker
from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_controls import apply_strategy_control_action
from quant.infrastructure.execution.strategy_ledger import build_strategy_mode_ledger, sync_broker_trade_history
from quant.infrastructure.execution.strategy_mode_records import StrategyModeRecordStore


class HistoryBroker:
    def __init__(self, trades):
        self.trades = trades
        self.calls = []

    def get_trade_history(self, start_date=None, end_date=None):
        self.calls.append((start_date, end_date))
        return list(self.trades)


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


def test_sync_broker_trade_history_imports_missing_fills_once(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    tracker = StrategyPositionTracker(tmp_path / "strategy_positions.json")
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


def test_sync_broker_trade_history_marks_unknown_order_attribution(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live_trading")
    tracker = StrategyPositionTracker(tmp_path / "strategy_positions.json")
    audit_path = tmp_path / "strategy_audit.jsonl"
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
        audit_path=audit_path,
    )

    assert result["imported_count"] == 1
    assert result["unresolved_count"] == 1
    fills = recorder.read_day("fills", "2026-06-03", strategy_name="default")
    assert fills[0]["order_id"] == "BRK-UNKNOWN"
    audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit_rows[-1]["action"] == "broker_history_unresolved"
    assert audit_rows[-1]["mode"] == "live"


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
