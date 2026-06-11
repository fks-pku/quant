from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from quant.domain.events.base import EventType
from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.features.trading.engine import Context, Engine
from quant.infrastructure.events.event_bus import EventBus
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.execution.fill_handler import FillHandler
from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.live_executor import LiveExecutionManager, TargetOrder
from quant.infrastructure.execution.order_manager import OrderManager
from quant.features.portfolio.tracker import StrategyPositionTracker
from quant.runtime.daily_strategy_runner import build_daily_snapshot, run_daily_snapshot
from quant.runtime.execution_reference import ExecutionReferencePriceResolver
from quant.runtime.strategy_cycle import feed_strategy_bars


class ApprovingRisk:
    def __init__(self, approved=True):
        self.approved = approved
        self.recorded = 0
        self.last_check = None
        self.last_record = None

    def check_order(self, **kwargs):
        self.last_check = kwargs
        return self.approved, []

    def log_result(self, results):
        pass

    def record_order(self, *args, **kwargs):
        self.recorded += 1
        self.last_record = {"args": args, "kwargs": kwargs}


class DummyBroker:
    name = "paper"

    def __init__(self):
        self.submitted = []

    def submit_order(self, order):
        self.submitted.append(order)
        return f"BRK-{len(self.submitted)}"

    def cancel_order(self, order_id):
        return True


class DummyPortfolio:
    def __init__(self):
        self.updated = []
        self.cash = 100000.0

    def update_position(self, **kwargs):
        self.updated.append(kwargs)

    def get_position(self, symbol):
        return SimpleNamespace(quantity=1000)


class RecordingOrderManager:
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name=None, **kwargs):
        self.submitted.append({
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": order_type,
            "price": price,
            "strategy_name": strategy_name,
            **kwargs,
        })
        return f"ORD-{len(self.submitted)}"

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True


class DailySnapshotStrategy:
    name = "DailySnapshotStrategy"
    symbols = ["600519", "000001"]

    def __init__(self):
        self.context = None
        self.batches = []
        self.before_dates = []
        self.after_dates = []

    def on_before_trading(self, context, trading_date):
        self.before_dates.append(trading_date)

    def on_data_batch(self, context, bars):
        self.batches.append([bar["symbol"] for bar in bars])

    def on_after_trading(self, context, trading_date):
        self.after_dates.append(trading_date)
        context.submit_order("600519", 100, "BUY", "MARKET", 10.0, self.name)


def test_live_trading_recorder_persists_daily_signals_fills_and_performance(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    ts1 = datetime(2026, 6, 1, 9, 31)
    ts2 = datetime(2026, 6, 1, 10, 1)
    ts3 = datetime(2026, 6, 2, 10, 1)

    recorder.record_signal(
        timestamp=ts1,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="BUY",
        quantity=100,
        order_type="MARKET",
        price=10.0,
        status="accepted",
        order_id="ORD-1",
    )
    recorder.record_fill(
        order_id="ORD-1",
        timestamp=ts1,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.0,
        commission=1.0,
    )
    recorder.record_fill(
        order_id="ORD-2",
        timestamp=ts2,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="SELL",
        quantity=100,
        price=12.0,
        commission=1.0,
    )
    recorder.record_fill(
        order_id="ORD-3",
        timestamp=ts2,
        strategy_name="DemoStrategy",
        symbol="000001",
        side="BUY",
        quantity=100,
        price=10.0,
        commission=1.0,
    )
    recorder.record_fill(
        order_id="ORD-4",
        timestamp=ts3,
        strategy_name="DemoStrategy",
        symbol="000001",
        side="SELL",
        quantity=100,
        price=9.0,
        commission=1.0,
    )
    recorder.record_strategy_snapshot(
        timestamp=ts1,
        strategy_name="DemoStrategy",
        nav=100000,
        market_value=1000,
        cash=99000,
        realized_pnl=0,
        unrealized_pnl=0,
    )
    recorder.record_strategy_snapshot(
        timestamp=ts3,
        strategy_name="DemoStrategy",
        nav=100010,
        market_value=0,
        cash=100010,
        realized_pnl=10,
        unrealized_pnl=0,
    )
    recorder.record_strategy_snapshot(
        timestamp=datetime(2026, 6, 2, 14, 50),
        strategy_name="DemoStrategy",
        nav=100020,
        market_value=0,
        cash=100020,
        realized_pnl=20,
        unrealized_pnl=0,
    )

    signals = recorder.read_day("signals", "2026-06-01")
    fills = recorder.read_day("fills", "2026-06-01", strategy_name="DemoStrategy")
    perf = recorder.get_strategy_performance("DemoStrategy")

    assert signals[0]["strategy_name"] == "DemoStrategy"
    assert len(fills) == 3
    assert perf["total_trades"] >= 1
    assert "sortino_ratio" in perf
    assert "calmar_ratio" in perf
    assert "total_return" in perf
    assert perf["total_nav"] == pytest.approx(100020)
    assert perf["cash"] == pytest.approx(100020)
    assert perf["pnl_curve"][-1]["nav"] == pytest.approx(100020)


@pytest.mark.skip(reason="strategy_mode_records removed from recorder")
def test_live_trading_recorder_writes_strategy_mode_records(tmp_path):
    from quant.infrastructure.execution.strategy_mode_records import StrategyModeRecordStore

    recorder = LiveTradingRecorder(tmp_path / "paper_trading")
    ts = datetime(2026, 6, 3, 15, 0)

    recorder.record_signal(
        timestamp=ts,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="BUY",
        quantity=100,
        order_type="MARKET",
        price=10.0,
        status="accepted",
        order_id="ORD-1",
    )
    recorder.record_fill(
        order_id="ORD-1",
        timestamp=ts,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.0,
    )
    recorder.record_strategy_snapshot(
        timestamp=ts,
        strategy_name="DemoStrategy",
        nav=20000,
        market_value=1000,
        cash=19000,
        realized_pnl=0,
        unrealized_pnl=0,
    )

    store = StrategyModeRecordStore(tmp_path / "strategy_modes")
    records = store.read_records(mode="paper", strategy_name="DemoStrategy")

    assert records["signals"][0]["order_id"] == "ORD-1"
    assert records["fills"][0]["symbol"] == "600519"
    assert records["snapshots"][0]["nav"] == 20000
    assert records["operations"][0]["action"] == "daily_snapshot"
    assert store.read("signals", mode="live", strategy_name="DemoStrategy") == []


def test_live_trading_recorder_calculates_slippage_stats(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    buy_ts = datetime(2026, 6, 1, 9, 31)
    sell_ts = datetime(2026, 6, 1, 10, 1)

    recorder.record_order(
        Order(
            symbol="600519",
            quantity=100,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_id="BRK-1",
            price=10.0,
            status=OrderStatus.SUBMITTED,
            timestamp=buy_ts,
            strategy_name="DemoStrategy",
        ),
        broker_order_id="BRK-1",
        status="submitted",
    )
    recorder.record_fill(
        order_id="BRK-1",
        timestamp=buy_ts,
        strategy_name="DemoStrategy",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.10,
    )
    recorder.record_order(
        Order(
            symbol="000001",
            quantity=200,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            order_id="BRK-2",
            price=20.0,
            status=OrderStatus.SUBMITTED,
            timestamp=sell_ts,
            strategy_name="DemoStrategy",
        ),
        broker_order_id="BRK-2",
        status="submitted",
    )
    recorder.record_fill(
        order_id="BRK-2",
        timestamp=sell_ts,
        strategy_name="DemoStrategy",
        symbol="000001",
        side="SELL",
        quantity=200,
        price=19.90,
    )

    perf = recorder.get_strategy_performance("DemoStrategy")

    assert perf["slippage_sample_count"] == 2
    assert perf["median_slippage_bps"] == pytest.approx(75.0)
    assert perf["weighted_avg_slippage_bps"] == pytest.approx(60.12024)


def test_live_trading_recorder_summarizes_live_metrics(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    ts = datetime(2026, 6, 1, 9, 31)

    recorder.record_strategy_snapshot(ts, "Alpha", nav=100000, market_value=70000, cash=30000, realized_pnl=0, unrealized_pnl=0)
    recorder.record_strategy_snapshot(ts, "Beta", nav=50000, market_value=45000, cash=5000, realized_pnl=0, unrealized_pnl=0)
    recorder.record_order(
        Order(
            symbol="600519",
            quantity=100,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_id="BRK-1",
            price=10.0,
            status=OrderStatus.SUBMITTED,
            timestamp=ts,
            strategy_name="Alpha",
        ),
        broker_order_id="BRK-1",
        status="submitted",
    )
    recorder.record_fill(
        order_id="BRK-1",
        timestamp=ts,
        strategy_name="Alpha",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.05,
    )

    summary = recorder.get_live_summary()

    assert summary["total_nav"] == pytest.approx(150000)
    assert summary["cash"] == pytest.approx(35000)
    assert summary["strategy_count"] == 2
    assert summary["slippage_sample_count"] == 1
    assert summary["median_slippage_bps"] == pytest.approx(50.0)
    assert summary["weighted_avg_slippage_bps"] == pytest.approx(50.0)


def test_strategy_runtime_feed_uses_batch_hook():
    class BatchStrategy:
        context = object()

        def __init__(self):
            self.batches = []
            self.singles = []

        def on_data_batch(self, context, bars):
            self.batches.append((context, list(bars)))

        def on_data(self, context, bar):
            self.singles.append((context, bar))

    strategy = BatchStrategy()
    bars = [{"symbol": "600519"}, {"symbol": "000001"}]

    feed_strategy_bars(strategy, bars)

    assert len(strategy.batches) == 1
    assert strategy.batches[0][0] is strategy.context
    assert strategy.batches[0][1] == bars
    assert strategy.singles == []


def test_daily_snapshot_runner_requires_complete_symbol_batch():
    class Strategy:
        name = "Strategy"
        symbols = ["600519", "000001"]
        context = None

        def __init__(self):
            self.batches = []
            self.after_dates = []

        def on_data_batch(self, context, bars):
            self.batches.append([bar["symbol"] for bar in bars])

        def on_after_trading(self, context, trading_date):
            self.after_dates.append(trading_date)

    strategy = Strategy()
    trading_date = datetime(2026, 6, 1)

    snapshot = build_daily_snapshot(
        [{"symbol": "600519", "timestamp": trading_date}],
        trading_date,
        strategy.symbols,
    )
    result = run_daily_snapshot(
        strategy,
        trading_date,
        [{"symbol": "600519", "timestamp": trading_date}],
    )

    assert snapshot.missing_symbols == ("000001",)
    assert result.ran is False
    assert result.missing_symbols == ("000001",)
    assert strategy.batches == []
    assert strategy.after_dates == []


def test_trading_engine_runs_completed_daily_snapshot_on_next_market_open():
    bus = EventBus()
    order_manager = RecordingOrderManager()
    engine = Engine(
        {
            "system": {"mode": "paper", "initial_cash": 100000},
            "live_trading": {"daily_snapshot_mode": True, "strict_daily_snapshot": True},
        },
        bus,
    )
    engine.set_order_manager(order_manager)
    strategy = DailySnapshotStrategy()
    engine.add_strategy(strategy)
    day1_close = datetime(2026, 6, 1, 15, 0)
    day2_open = datetime(2026, 6, 2, 9, 30)

    bus.publish_nowait(EventType.BAR, {"symbol": "000001", "timestamp": day1_close, "close": 11.0})
    bus.publish_nowait(EventType.BAR, {"symbol": "600519", "timestamp": day1_close, "close": 10.0})
    bus.publish_nowait(EventType.MARKET_CLOSE, {"timestamp": day1_close})

    assert strategy.batches == []
    assert strategy.after_dates == []
    assert order_manager.submitted == []

    bus.publish_nowait(EventType.MARKET_OPEN, {"timestamp": day2_open})

    assert strategy.batches == [["600519", "000001"]]
    assert strategy.after_dates == [day1_close.date()]
    assert strategy.before_dates == [day2_open.date()]
    assert order_manager.submitted[-1]["symbol"] == "600519"
    assert order_manager.submitted[-1]["strategy_name"] == "DailySnapshotStrategy"


def test_trading_engine_skips_incomplete_daily_snapshot():
    bus = EventBus()
    order_manager = RecordingOrderManager()
    engine = Engine(
        {
            "system": {"mode": "paper", "initial_cash": 100000},
            "live_trading": {"daily_snapshot_mode": True, "strict_daily_snapshot": True},
        },
        bus,
    )
    engine.set_order_manager(order_manager)
    strategy = DailySnapshotStrategy()
    engine.add_strategy(strategy)
    day1_close = datetime(2026, 6, 1, 15, 0)
    day2_open = datetime(2026, 6, 2, 9, 30)

    bus.publish_nowait(EventType.BAR, {"symbol": "600519", "timestamp": day1_close, "close": 10.0})
    bus.publish_nowait(EventType.MARKET_CLOSE, {"timestamp": day1_close})
    bus.publish_nowait(EventType.MARKET_OPEN, {"timestamp": day2_open})

    assert strategy.batches == []
    assert strategy.after_dates == []
    assert strategy.before_dates == [day2_open.date()]
    assert order_manager.submitted == []


def test_trading_engine_uses_latest_completed_snapshot_after_gap():
    bus = EventBus()
    order_manager = RecordingOrderManager()
    engine = Engine(
        {
            "system": {"mode": "paper", "initial_cash": 100000},
            "live_trading": {"daily_snapshot_mode": True, "strict_daily_snapshot": True},
        },
        bus,
    )
    engine.set_order_manager(order_manager)
    strategy = DailySnapshotStrategy()
    engine.add_strategy(strategy)
    day1_close = datetime(2026, 6, 1, 15, 0)
    day3_close = datetime(2026, 6, 3, 15, 0)
    day4_open = datetime(2026, 6, 4, 9, 30)

    for close_time in (day1_close, day3_close):
        bus.publish_nowait(EventType.BAR, {"symbol": "600519", "timestamp": close_time, "close": 10.0})
        bus.publish_nowait(EventType.BAR, {"symbol": "000001", "timestamp": close_time, "close": 11.0})
        bus.publish_nowait(EventType.MARKET_CLOSE, {"timestamp": close_time})

    bus.publish_nowait(EventType.MARKET_OPEN, {"timestamp": day4_open})

    assert strategy.after_dates == [day3_close.date()]
    assert len(order_manager.submitted) == 1


def test_trading_engine_inject_daily_snapshot_uses_live_event_pipeline():
    bus = EventBus()
    order_manager = RecordingOrderManager()
    engine = Engine(
        {
            "system": {"mode": "paper", "initial_cash": 100000},
            "live_trading": {"daily_snapshot_mode": True, "strict_daily_snapshot": True},
        },
        bus,
    )
    engine.set_order_manager(order_manager)
    strategy = DailySnapshotStrategy()
    engine.add_strategy(strategy)
    signal_day = datetime(2026, 6, 1)
    execution_day = datetime(2026, 6, 2)

    results = engine.inject_daily_snapshot(
        signal_day,
        [
            {"symbol": "000001", "timestamp": signal_day, "close": 11.0},
            {"symbol": "600519", "timestamp": signal_day, "close": 10.0},
        ],
        execution_day,
    )

    assert results["DailySnapshotStrategy"].ran is True
    assert strategy.batches == [["600519", "000001"]]
    assert strategy.before_dates == [execution_day.date()]
    assert order_manager.submitted[-1]["strategy_name"] == "DailySnapshotStrategy"


def test_trading_engine_signal_gate_blocks_daily_snapshot_orders():
    bus = EventBus()
    order_manager = RecordingOrderManager()
    engine = Engine(
        {
            "system": {"mode": "live", "initial_cash": 100000},
            "live_trading": {"daily_snapshot_mode": True, "strict_daily_snapshot": True},
        },
        bus,
    )
    engine.set_order_manager(order_manager)
    engine.set_strategy_signal_gate(lambda strategy_name: False)
    strategy = DailySnapshotStrategy()
    engine.add_strategy(strategy)
    signal_day = datetime(2026, 6, 1)
    execution_day = datetime(2026, 6, 2)

    results = engine.inject_daily_snapshot(
        signal_day,
        [
            {"symbol": "000001", "timestamp": signal_day, "close": 11.0},
            {"symbol": "600519", "timestamp": signal_day, "close": 10.0},
        ],
        execution_day,
    )

    assert results == {}
    assert strategy.batches == []
    assert strategy.before_dates == []
    assert strategy.after_dates == []
    assert order_manager.submitted == []


def test_context_signal_gate_blocks_direct_order_submission():
    order_manager = RecordingOrderManager()
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        signal_gate=lambda strategy_name: False,
    )

    result = context.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    assert result is None
    assert order_manager.submitted == []


def test_context_exposes_strategy_scoped_order_surface_only():
    class RawOrderManager(RecordingOrderManager):
        def get_all_orders(self):
            return ["global-order"]

    order_manager = RawOrderManager()
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        broker=object(),
        strategy_name="Alpha",
    )

    assert context.broker is None
    assert not hasattr(context.order_manager, "get_all_orders")
    with pytest.raises(ValueError):
        context.order_manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "Beta")

    order_id = context.submit_order("600519", 100, "BUY", "LIMIT", 10.0)

    assert order_id == "ORD-1"
    assert order_manager.submitted == [{
        "symbol": "600519",
        "quantity": 100,
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 10.0,
        "strategy_name": "Alpha",
    }]


def test_trading_engine_market_clock_uses_configured_market(monkeypatch):
    from quant.shared.utils import datetime_utils

    seen = {}

    def fake_current_time(timezone):
        seen["timezone"] = timezone
        return datetime(2026, 6, 2, 10, 0)

    monkeypatch.setattr(datetime_utils, "get_current_time", fake_current_time)
    engine = Engine(
        {
            "system": {"mode": "paper", "market": "CN", "initial_cash": 100000},
            "markets": {
                "CN": {
                    "timezone": "Asia/Shanghai",
                    "open_hour": 9,
                    "open_minute": 30,
                    "close_hour": 15,
                    "close_minute": 0,
                }
            },
        },
        EventBus(),
    )

    assert engine._is_market_open() is True
    assert seen["timezone"] == "Asia/Shanghai"


def test_order_manager_records_strategy_signal_and_broker_order(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(True),
        event_bus=EventBus(),
        config={},
        live_recorder=recorder,
    )
    manager.register_broker("paper", broker)

    order_id = manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    signals = recorder.read_day("signals", datetime.now().date().isoformat())
    orders = recorder.read_day("orders", datetime.now().date().isoformat())
    assert order_id is not None
    assert signals[-1]["strategy_name"] == "DemoStrategy"
    assert signals[-1]["status"] == "submitted"
    assert orders[-1]["broker_order_id"] == "BRK-1"
    assert orders[-1]["status"] == "submitted"


def test_order_manager_skips_duplicate_live_order_already_recorded_today(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    existing_timestamp = datetime.now()
    recorder.record_order(
        Order(
            symbol="510300",
            quantity=300,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_id="1082173127",
            price=4.769,
            timestamp=existing_timestamp,
            strategy_name="ashare_broad_asset_etf_rotation",
        ),
        broker_order_id="1082173127",
        status="submitted",
        timestamp=existing_timestamp,
    )
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(True),
        event_bus=EventBus(),
        config={},
        live_recorder=recorder,
    )
    manager.register_broker("paper", broker)

    manager.submit_order("510300", 300, "BUY", "LIMIT", 4.769032, "ashare_broad_asset_etf_rotation")

    orders = recorder.read_day("orders", existing_timestamp.date().isoformat())
    assert broker.submitted == []
    assert len(orders) >= 1
    assert any(o.get("broker_order_id") == "1082173127" for o in orders)


def test_order_manager_record_pending_only_does_not_submit_to_broker(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    broker = DummyBroker()
    risk = ApprovingRisk(True)
    tracker = StrategyPositionTracker(tmp_path / "positions.json")
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=risk,
        event_bus=EventBus(),
        config={"execution": {"record_pending_only": True}},
        strategy_tracker=tracker,
        live_recorder=recorder,
    )
    manager.register_broker("paper", broker)
    manager.set_signal_timestamp(datetime(2026, 6, 3, 15, 0))
    manager.set_signal_submit_date("2026-06-04")

    order_id = manager.submit_order("159949", 100, "SELL", "LIMIT", 2.010949, "DemoStrategy")

    signals = recorder.read_day("signals", "2026-06-03")
    orders = recorder.read_day("orders", "2026-06-03")
    assert order_id is not None
    assert broker.submitted == []
    assert len(orders) == 1
    assert signals[-1]["strategy_name"] == "DemoStrategy"
    assert signals[-1]["submit_date"] == "2026-06-04"


def test_live_execution_manager_pending_only_records_cost_bps_without_execution_limit(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    broker = DummyBroker()
    risk = ApprovingRisk(True)
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=risk,
        event_bus=EventBus(),
        config={"execution": {"record_pending_only": True}},
        live_recorder=recorder,
    )
    manager.register_broker("paper", broker)
    manager.set_signal_timestamp(datetime(2026, 6, 3, 15, 0))
    executor = LiveExecutionManager(manager, default_max_cost_bps=25)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        strategy_name="DemoStrategy",
    ))

    signals = recorder.read_day("signals", "2026-06-03")
    assert order_id is not None
    assert broker.submitted == []
    assert signals[-1]["order_type"] == "LIMIT"
    assert signals[-1]["reference_price"] == pytest.approx(10.0)
    assert signals[-1]["cost_bps"] == pytest.approx(25.0)
    assert signals[-1].get("price") is None
    assert signals[-1]["symbol"] == "600519"
    assert signals[-1]["side"] == "BUY"
    assert signals[-1]["status"] == "accepted"
    assert signals[-1]["order_id"] == order_id
    assert manager.get_order_status(order_id) == OrderStatus.PENDING
    # risk check deferred to D+1 in pending-only mode


def test_order_manager_passes_side_and_pending_value_to_risk_engine(tmp_path):
    risk = ApprovingRisk(True)
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=risk,
        event_bus=EventBus(),
        config={},
    )
    manager.register_broker("paper", broker)

    manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    assert risk.last_check["side"] == "BUY"
    assert risk.last_record["kwargs"]["symbol"] == "600519"
    assert risk.last_record["kwargs"]["order_value"] == pytest.approx(1000.0)


def test_order_manager_maps_broker_order_id_to_strategy_tracker(tmp_path):
    tracker = StrategyPositionTracker(tmp_path / "positions.json")
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(True),
        event_bus=EventBus(),
        config={},
        strategy_tracker=tracker,
    )
    manager.register_broker("paper", broker)

    manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    assert tracker.get_strategy_for_order("BRK-1") == "DemoStrategy"


def test_order_manager_treats_partial_fills_as_open_orders(tmp_path):
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(True),
        event_bus=EventBus(),
        config={},
    )
    manager.register_broker("paper", broker)

    manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")
    manager.update_order_from_fill("BRK-1", 40, 10.0)

    open_orders = manager.get_open_orders()
    assert [order.order_id for order in open_orders] == ["BRK-1"]
    assert open_orders[0].status == OrderStatus.PARTIAL


def test_order_manager_uses_strategy_specific_risk_engine(tmp_path):
    default_risk = ApprovingRisk(False)
    strategy_risk = ApprovingRisk(True)
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=default_risk,
        event_bus=EventBus(),
        config={},
        risk_engine_resolver=lambda strategy_name: strategy_risk if strategy_name == "DemoStrategy" else default_risk,
    )
    manager.register_broker("paper", broker)

    order_id = manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    assert order_id is not None
    assert default_risk.last_check is None
    assert strategy_risk.last_check["symbol"] == "600519"
    assert len(broker.submitted) == 1


def test_order_manager_routes_to_single_non_paper_broker(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    broker = DummyBroker()
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(True),
        event_bus=EventBus(),
        config={},
        live_recorder=recorder,
    )
    manager.register_broker("qmt", broker)

    order_id = manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    assert order_id is not None
    assert len(broker.submitted) == 1


def test_order_manager_records_rejected_strategy_signal(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=ApprovingRisk(False),
        event_bus=EventBus(),
        config={},
        live_recorder=recorder,
    )
    manager.register_broker("paper", DummyBroker())

    order_id = manager.submit_order("600519", 100, "BUY", "LIMIT", 10.0, "DemoStrategy")

    signals = recorder.read_day("signals", datetime.now().date().isoformat())
    assert order_id is None
    assert signals[-1]["status"] == "rejected"
    assert signals[-1]["failure_reason"] == "risk_check_failed"


def test_fill_handler_records_strategy_fill(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    handler = FillHandler(
        portfolio=DummyPortfolio(),
        event_bus=EventBus(),
        config={},
        live_recorder=recorder,
    )

    handler.process_fill(
        order_id="BRK-1",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.0,
        commission=1.0,
        timestamp=datetime(2026, 6, 1, 10, 1),
        strategy_name="DemoStrategy",
    )

    fills = recorder.read_day("fills", "2026-06-01")
    assert fills[-1]["order_id"] == "BRK-1"
    assert fills[-1]["strategy_name"] == "DemoStrategy"


def test_fill_handler_passes_commission_to_strategy_tracker_cost(tmp_path):
    tracker = StrategyPositionTracker(tmp_path / "positions.json")
    handler = FillHandler(
        portfolio=DummyPortfolio(),
        event_bus=EventBus(),
        config={},
        strategy_tracker=tracker,
    )

    handler.process_fill(
        order_id="BRK-1",
        symbol="518880",
        side="BUY",
        quantity=1000,
        price=9.302,
        commission=5.0,
        timestamp=datetime(2026, 6, 3, 9, 47),
        strategy_name="DemoStrategy",
    )

    holding = tracker.get_breakdown()["DemoStrategy"]["holdings"][0]
    assert holding["avg_cost"] == pytest.approx(9.307)


def test_fill_handler_updates_strategy_specific_portfolio(tmp_path):
    default_portfolio = DummyPortfolio()
    strategy_portfolio = DummyPortfolio()
    handler = FillHandler(
        portfolio=default_portfolio,
        event_bus=EventBus(),
        config={},
        portfolio_resolver=lambda strategy_name: strategy_portfolio if strategy_name == "DemoStrategy" else default_portfolio,
    )

    handler.process_fill(
        order_id="BRK-1",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=10.0,
        timestamp=datetime(2026, 6, 1, 10, 1),
        strategy_name="DemoStrategy",
    )

    assert len(strategy_portfolio.updated) == 1
    assert default_portfolio.updated == []


def test_fill_handler_buy_deducts_strategy_shadow_cash():
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.sub_portfolio import SubPortfolio

    master = Portfolio(initial_cash=100000)
    sub = SubPortfolio("DemoStrategy", 20000, master)
    handler = FillHandler(
        portfolio=master,
        event_bus=EventBus(),
        config={},
        portfolio_resolver=lambda strategy_name: sub if strategy_name == "DemoStrategy" else master,
    )

    handler.process_fill(
        order_id="BRK-1",
        symbol="600519",
        side="BUY",
        quantity=100,
        price=50.0,
        commission=5.0,
        timestamp=datetime(2026, 6, 1, 10, 1),
        strategy_name="DemoStrategy",
    )

    assert sub.cash == pytest.approx(20000 - 5005.0)
    assert master.cash == pytest.approx(80000.0)


def test_recorder_accepts_domain_order_objects(tmp_path):
    recorder = LiveTradingRecorder(tmp_path / "live")
    order = Order(
        symbol="600519",
        quantity=100,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        order_id="ORD-1",
        price=10.0,
        status=OrderStatus.SUBMITTED,
        timestamp=datetime(2026, 6, 1, 9, 31),
        strategy_name="DemoStrategy",
    )

    recorder.record_order(order, broker_order_id="BRK-1", status="submitted")

    orders = recorder.read_day("orders", "2026-06-01")
    assert orders[-1]["order_id"] == "ORD-1"
    assert orders[-1]["broker_order_id"] == "BRK-1"


def test_paper_broker_uses_execution_open_for_limit_fill_and_callbacks():
    from quant.features.trading.portfolio import Portfolio
    portfolio = Portfolio(initial_cash=10000, currency="CNY")
    broker = PaperBroker(slippage_bps=5)
    broker.set_portfolio(portfolio)
    broker.connect()
    broker.set_execution_bars([
        {"symbol": "518880", "open": 9.302, "close": 9.295},
    ])
    seen = []
    def _track_fill(**trade):
        seen.append(trade)
        pf = portfolio
        cost = float(trade["price"]) * float(trade["quantity"])
        comm = float(trade.get("commission", 0))
        side = str(trade["side"]).upper()
        sym = str(trade["symbol"])
        qty = float(trade["quantity"])
        if side == "BUY":
            pf.update_position(sym, qty, float(trade["price"]), cost + comm)
            pf.cash -= cost + comm
        else:
            pf.update_position(sym, -qty, 0, 0)
            pf.cash += cost - comm
    broker.register_trade_callback(_track_fill)

    order_id = broker.submit_order(Order(
        symbol="518880",
        quantity=1000,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=9.307,
        strategy_name="Barbell",
    ))

    assert broker.get_order_status(order_id) == OrderStatus.FILLED
    assert broker.get_order(order_id).avg_fill_price == pytest.approx(9.302)
    assert seen == []

    broker.flush_trade_callbacks()

    assert seen[-1]["order_id"] == order_id
    assert seen[-1]["symbol"] == "518880"
    assert seen[-1]["quantity"] == pytest.approx(1000)
    assert seen[-1]["price"] == pytest.approx(9.302)
    assert seen[-1]["commission"] == pytest.approx(5.0)
    assert seen[-1]["strategy_name"] == "Barbell"
    assert portfolio.cash == pytest.approx(10000.0 - 9302.0 - 5.0)
    assert portfolio.positions["518880"].avg_cost == pytest.approx(9.307)

    rejected_id = broker.submit_order(Order(
        symbol="518880",
        quantity=1000,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=9.300,
        strategy_name="Barbell",
    ))
    broker.flush_trade_callbacks()

    assert broker.get_order_status(rejected_id) == OrderStatus.REJECTED
    assert len(seen) == 1


def test_order_manager_flushes_paper_broker_fills_after_submission():
    broker = PaperBroker(slippage_bps=5)
    broker.connect()
    broker.set_execution_bars([{"symbol": "600519", "open": 10.0, "close": 10.2}])
    risk = ApprovingRisk(True)
    manager = OrderManager(
        portfolio=DummyPortfolio(),
        risk_engine=risk,
        event_bus=EventBus(),
        config={"execution": {"max_retries": 1}},
    )
    manager.register_broker("paper", broker)
    seen = []

    def on_trade(**trade):
        seen.append(trade)
        manager.update_order_from_fill(trade["order_id"], trade["quantity"], trade["price"])

    broker.register_trade_callback(on_trade)

    manager.submit_order("600519", 100, "BUY", "LIMIT", 10.1, "DemoStrategy")

    assert seen[-1]["order_id"] == "PAPER_1"
    assert seen[-1]["price"] == pytest.approx(10.0)
    assert seen[-1]["commission"] == pytest.approx(5.03)
    assert manager.get_order_status("PAPER_1") == OrderStatus.FILLED


def test_paper_broker_commission_matches_backtest_execution_for_cn_etf():
    from quant.features.backtest.entities import BacktestDiagnostics, CommissionConfig
    from quant.features.backtest.order_executor import execute_order
    from quant.features.backtest.schemas import DeferredOrder
    from quant.features.trading.portfolio import Portfolio

    commission = {"CN": {"type": "cn_realistic"}}
    broker = PaperBroker(slippage_bps=0, commission_config=commission)
    broker_pf = Portfolio(initial_cash=10000, currency="CNY")
    broker.set_portfolio(broker_pf)
    broker.connect()
    broker.set_execution_bars([
        {"symbol": "518880", "open": 9.302, "close": 9.295, "volume": 1_000_000},
    ])
    seen = []
    def _track(**trade):
        seen.append(trade)
        cost = float(trade["price"]) * float(trade["quantity"])
        comm = float(trade.get("commission", 0))
        broker_pf.update_position(str(trade["symbol"]), float(trade["quantity"]), float(trade["price"]), cost + comm)
        broker_pf.cash -= cost + comm
    broker.register_trade_callback(_track)

    broker.submit_order(Order(
        symbol="518880",
        quantity=1000,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=9.307,
        strategy_name="Barbell",
    ))
    broker.flush_trade_callbacks()

    portfolio = Portfolio(initial_cash=10000)
    trades = execute_order(
        order=DeferredOrder(
            symbol="518880",
            quantity=1000,
            side="BUY",
            order_type="LIMIT",
            price=9.307,
            strategy="Barbell",
            signal_date=datetime(2026, 6, 8),
        ),
        portfolio=portfolio,
        symbol="518880",
        bar={
            "symbol": "518880",
            "timestamp": datetime(2026, 6, 9, 9, 31),
            "open": 9.302,
            "high": 9.35,
            "low": 9.25,
            "close": 9.295,
            "volume": 1_000_000,
        },
        entry_times={},
        entry_prices={},
        diag=BacktestDiagnostics(),
        lot_sizes={},
        ipo_dates={},
        slippage_bps=0,
        commission_config=CommissionConfig(CN=commission["CN"]),
    )

    assert seen[-1]["price"] == pytest.approx(trades[0].fill_price)
    assert seen[-1]["commission"] == pytest.approx(trades[0].commission)
    assert broker_pf.cash == pytest.approx(portfolio.cash)
    assert broker_pf.positions["518880"].avg_cost == pytest.approx(portfolio.positions["518880"].avg_cost)


def test_strategy_tracker_calibration_updates_live_market_values(tmp_path):
    tracker = StrategyPositionTracker(tmp_path / "positions.json")
    tracker.update_from_fill("DemoStrategy", "600519", "BUY", 100, 10.0)

    breakdown = tracker.calibrate([
        {
            "symbol": "600519",
            "quantity": 100,
            "avg_cost": 10.0,
            "market_value": 1200.0,
            "unrealized_pnl": 200.0,
        }
    ])

    holding = breakdown["DemoStrategy"]["holdings"][0]
    assert holding["market_value"] == pytest.approx(1200.0)
    assert holding["unrealized_pnl"] == pytest.approx(200.0)
    assert breakdown["DemoStrategy"]["total_market_value"] == pytest.approx(1200.0)


def test_quant_system_dispatches_fill_only_to_owning_strategy():
    from quant.quant_system import QuantSystem

    class DummyStrategy:
        def __init__(self, name):
            self.name = name
            self.context = object()
            self.fills = []

        def on_fill(self, context, fill):
            self.fills.append((context, fill))

    owner = DummyStrategy("Owner")
    other = DummyStrategy("Other")
    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = SimpleNamespace(strategies=[owner, other])
    fill = SimpleNamespace(strategy_name="Owner", symbol="600519")

    QuantSystem._on_fill(quant, fill)

    assert len(owner.fills) == 1
    assert other.fills == []


def test_quant_system_resolves_strategy_specific_risk_and_portfolio():
    from quant.quant_system import QuantSystem

    strategy_risk = object()
    strategy_portfolio = object()
    default_risk = object()
    default_portfolio = object()
    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = SimpleNamespace(
        risk_engine=default_risk,
        portfolio=default_portfolio,
        _sub_risk_engines={"DemoStrategy": strategy_risk},
        _sub_portfolios={"DemoStrategy": strategy_portfolio},
    )

    assert QuantSystem._risk_engine_for_strategy(quant, "DemoStrategy") is strategy_risk
    assert QuantSystem._portfolio_for_strategy(quant, "DemoStrategy") is strategy_portfolio
    assert QuantSystem._risk_engine_for_strategy(quant, "Other") is default_risk
    assert QuantSystem._portfolio_for_strategy(quant, "Other") is default_portfolio


def test_quant_system_restores_strategy_runtime_positions_from_tracker(tmp_path):
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.quant_system import QuantSystem

    tracker = StrategyPositionTracker(tmp_path / "positions.json")
    tracker.update_from_fill(
        strategy_name="DemoStrategy",
        symbol="159949",
        side="BUY",
        qty=4900,
        price=1.994,
        commission=5.0,
    )
    master = Portfolio(initial_cash=100000)
    sub = SubPortfolio("DemoStrategy", 20000, master)
    quant = QuantSystem.__new__(QuantSystem)
    quant._strategy_tracker = tracker
    quant.engine = SimpleNamespace(
        portfolio=master,
        _sub_portfolios={"DemoStrategy": sub},
    )
    strategy = SimpleNamespace(name="DemoStrategy", _positions={})

    QuantSystem._restore_strategy_runtime_positions(quant, strategy, "DemoStrategy")

    avg_cost = (1.994 * 4900 + 5.0) / 4900
    assert strategy._positions["159949"] == pytest.approx(4900)
    assert sub.get_position("159949").quantity == pytest.approx(4900)
    assert sub.get_position("159949").avg_cost == pytest.approx(avg_cost)
    assert sub.cash == pytest.approx(20000 - avg_cost * 4900)
    assert sub.settled_quantity("159949", date.today() + timedelta(days=1)) == pytest.approx(4900)


def test_quant_system_pending_only_daily_snapshot_records_signal_day_snapshot_marker():
    from quant.quant_system import QuantSystem

    class FakeOrderManager:
        def __init__(self):
            self.signal_timestamp = None
            self.signal_submit_date = None
            self.cleared = False
            self.submit_date_cleared = False

        def set_signal_timestamp(self, timestamp):
            self.signal_timestamp = timestamp

        def clear_signal_timestamp(self):
            self.cleared = True
            self.signal_timestamp = None

        def set_signal_submit_date(self, submit_date):
            self.signal_submit_date = submit_date

        def clear_signal_submit_date(self):
            self.submit_date_cleared = True
            self.signal_submit_date = None

    class FakeEngine:
        def __init__(self):
            self.order_manager = FakeOrderManager()
            self.strategies = [SimpleNamespace(name="DemoStrategy", symbols=["159949"])]

        def inject_daily_snapshot(self, signal_day, bars, execution_day):
            assert signal_day == date(2026, 6, 3)
            assert execution_day == date(2026, 6, 4)
            assert self.order_manager.signal_timestamp == datetime(2026, 6, 3, 15, 0)
            assert self.order_manager.signal_submit_date == "2026-06-04"
            assert bars == [{"symbol": "159949", "timestamp": "2026-06-03", "close": 2.017}]
            return {"DemoStrategy": SimpleNamespace(ran=True)}

    snapshots = []
    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = FakeEngine()
    quant.config = {"execution": {"record_pending_only": True}, "live_trading": {}}
    quant._assert_current_broker_safe_for_paper = lambda: None
    quant._select_snapshot_provider = lambda provider_name: object()
    quant._strategy_symbols = lambda: ["159949"]
    quant._load_snapshot_bars = (
        lambda provider, symbols, start_day, end_day=None: [
            {"symbol": "159949", "timestamp": "2026-06-03", "close": 2.017}
        ]
    )
    quant._prepare_paper_execution_context = lambda provider, symbols, execution_day: None
    quant._record_live_strategy_snapshots = lambda timestamp=None: snapshots.append(timestamp)

    results = QuantSystem.run_daily_snapshot_once(
        quant,
        "2026-06-03",
        execution_date="2026-06-04",
        provider_name="duckdb",
    )

    assert results["DemoStrategy"].ran is True
    assert quant.engine.order_manager.cleared is True
    assert quant.engine.order_manager.submit_date_cleared is True
    assert snapshots == [datetime(2026, 6, 3, 15, 0)]


def test_quant_system_paper_pending_only_daily_snapshot_does_not_require_execution_bars():
    from quant.quant_system import QuantSystem

    class FakeOrderManager:
        def __init__(self):
            self.signal_timestamp = None
            self.signal_submit_date = None

        def set_signal_timestamp(self, timestamp):
            self.signal_timestamp = timestamp

        def clear_signal_timestamp(self):
            self.signal_timestamp = None

        def set_signal_submit_date(self, submit_date):
            self.signal_submit_date = submit_date

        def clear_signal_submit_date(self):
            self.signal_submit_date = None

    class FakeEngine:
        def __init__(self):
            self.order_manager = FakeOrderManager()
            self.strategies = [SimpleNamespace(name="DemoStrategy", symbols=["159949"])]

        def inject_daily_snapshot(self, signal_day, bars, execution_day):
            assert signal_day == date(2026, 6, 11)
            assert execution_day == date(2026, 6, 12)
            assert self.order_manager.signal_timestamp == datetime(2026, 6, 11, 15, 0)
            assert self.order_manager.signal_submit_date == "2026-06-12"
            return {"DemoStrategy": SimpleNamespace(ran=True)}

    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = FakeEngine()
    quant.config = {
        "system": {"mode": "paper"},
        "execution": {"record_pending_only": True},
        "live_trading": {},
    }
    quant._assert_current_broker_safe_for_paper = lambda: None
    quant._select_snapshot_provider = lambda provider_name: object()
    quant._strategy_symbols = lambda: ["159949"]
    quant._load_snapshot_bars = (
        lambda provider, symbols, start_day, end_day=None: [
            {"symbol": "159949", "timestamp": "2026-06-11", "close": 1.848}
        ]
    )
    quant._prepare_paper_execution_context = (
        lambda provider, symbols, execution_day: pytest.fail("pending-only signal generation must not load execution-day bars")
    )
    quant._record_live_strategy_snapshots = lambda timestamp=None: None

    results = QuantSystem.run_daily_snapshot_once(
        quant,
        "2026-06-11",
        execution_date="2026-06-12",
        provider_name="duckdb",
    )

    assert results["DemoStrategy"].ran is True


def test_quant_system_paper_daily_snapshot_records_execution_day_snapshot_marker():
    from quant.quant_system import QuantSystem

    class FakeEngine:
        def __init__(self):
            self.strategies = [SimpleNamespace(name="DemoStrategy", symbols=["159949"])]

        def inject_daily_snapshot(self, signal_day, bars, execution_day):
            return {"DemoStrategy": SimpleNamespace(ran=True)}

    snapshots = []
    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = FakeEngine()
    quant.config = {"execution": {}, "live_trading": {}}
    quant._assert_current_broker_safe_for_paper = lambda: None
    quant._select_snapshot_provider = lambda provider_name: object()
    quant._strategy_symbols = lambda: ["159949"]
    quant._load_snapshot_bars = (
        lambda provider, symbols, start_day, end_day=None: [
            {"symbol": "159949", "timestamp": str(start_day), "close": 2.017}
        ]
    )
    quant._prepare_paper_execution_context = lambda provider, symbols, execution_day: None
    quant._record_live_strategy_snapshots = lambda timestamp=None: snapshots.append(timestamp)

    QuantSystem.run_daily_snapshot_once(
        quant,
        "2026-06-03",
        execution_date="2026-06-04",
        provider_name="duckdb",
    )

    assert snapshots == [datetime(2026, 6, 4, 15, 0)]


def test_quant_system_records_cash_only_subportfolio_snapshots(tmp_path):
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.quant_system import QuantSystem

    master = Portfolio(initial_cash=100000)
    invested = SubPortfolio("InvestedStrategy", 20000, master)
    cash_only = SubPortfolio("CashOnlyStrategy", 15000, master)
    invested.update_position("159949", 100, 2.0, 200.0)
    invested.cash -= 200.0
    quant = QuantSystem.__new__(QuantSystem)
    quant.live_recorder = LiveTradingRecorder(tmp_path / "paper_trading")
    quant.config = {"system": {"mode": "paper"}}
    quant.logger = SimpleNamespace(error=lambda *args, **kwargs: None)
    quant.engine = SimpleNamespace(
        strategies=[
            SimpleNamespace(name="InvestedStrategy"),
            SimpleNamespace(name="CashOnlyStrategy"),
        ],
        _sub_portfolios={
            "InvestedStrategy": invested,
            "CashOnlyStrategy": cash_only,
        },
        broker=None,
    )

    QuantSystem._record_live_strategy_snapshots(
        quant,
        timestamp=datetime(2026, 6, 4, 15, 0),
    )

    invested_rows = quant.live_recorder.read_day("snapshots", "2026-06-04", "InvestedStrategy")
    cash_rows = quant.live_recorder.read_day("snapshots", "2026-06-04", "CashOnlyStrategy")
    assert invested_rows[0]["nav"] == pytest.approx(invested.nav)
    assert invested_rows[0]["market_value"] == pytest.approx(200.0)
    assert cash_rows[0]["nav"] == pytest.approx(15000.0)
    assert cash_rows[0]["cash"] == pytest.approx(15000.0)
    assert cash_rows[0]["market_value"] == pytest.approx(0.0)


def test_quant_system_allocates_default_live_strategy_cash(monkeypatch):
    from quant.quant_system import QuantSystem

    class DummyConfigLoader:
        def load(self, name):
            if name == "strategies.yaml":
                return {"strategies": {}}
            return {}

    class DummyEngine:
        def __init__(self):
            self.added = []
            self.portfolio = SimpleNamespace(initial_cash=100000.0)

        def add_strategy(self, strategy, allocation_pct=None):
            self.added.append((strategy, allocation_pct))

    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {
        "system": {"initial_cash": 100000.0},
        "live_trading": {"strategy_initial_cash": 20000.0},
        "strategies": [
            {"name": "DemoStrategy", "enabled": True, "symbols": ["600519"]},
            {"name": "OtherStrategy", "enabled": True, "symbols": ["000001"]},
            {"name": "CashSizedStrategy", "enabled": True, "symbols": ["000002"], "allocation_cash": 50000.0},
        ],
    }
    quant.config_loader = DummyConfigLoader()
    quant.engine = DummyEngine()
    quant.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QuantSystem,
        "_create_strategy",
        lambda self, name, symbols, params: SimpleNamespace(name=name, symbols=symbols),
    )

    QuantSystem._setup_strategies(quant)

    assert [item[0].name for item in quant.engine.added] == [
        "DemoStrategy",
        "OtherStrategy",
        "CashSizedStrategy",
    ]
    assert [item[1] for item in quant.engine.added] == [
        pytest.approx(0.2),
        pytest.approx(0.2),
        pytest.approx(0.5),
    ]


def test_strategy_control_actions_gate_live_signals(tmp_path):
    from quant.infrastructure.execution.strategy_controls import (
        apply_strategy_control_action,
        get_strategy_control,
    )

    control_file = tmp_path / "strategy_controls.json"

    assert get_strategy_control(
        "DemoStrategy",
        control_file,
        default_live_enabled=True,
    ).accepts_live_signals

    paused = apply_strategy_control_action(
        "DemoStrategy",
        "pause",
        control_file,
        default_live_enabled=True,
    )
    assert paused.live_state == "paused"
    assert not paused.accepts_live_signals

    resumed = apply_strategy_control_action("DemoStrategy", "resume", control_file)
    assert resumed.live_state == "running"
    assert resumed.accepts_live_signals

    liquidating = apply_strategy_control_action("DemoStrategy", "liquidate_stop", control_file)
    assert liquidating.live_state == "liquidating"
    assert liquidating.liquidation_requested
    assert not liquidating.accepts_live_signals


def test_strategy_control_actions_are_mode_isolated(tmp_path):
    from quant.infrastructure.execution.strategy_controls import (
        apply_strategy_control_action,
        get_strategy_control,
    )

    control_file = tmp_path / "strategy_controls.json"

    apply_strategy_control_action(
        "DemoStrategy",
        "pause",
        control_file,
        default_live_enabled=True,
        mode="live",
    )
    apply_strategy_control_action(
        "DemoStrategy",
        "resume",
        control_file,
        default_live_enabled=True,
        mode="paper",
    )

    live = get_strategy_control("DemoStrategy", control_file, mode="live")
    paper = get_strategy_control("DemoStrategy", control_file, mode="paper")
    assert live.mode == "live"
    assert live.live_state == "paused"
    assert not live.accepts_live_signals
    assert paper.mode == "paper"
    assert paper.live_state == "running"
    assert paper.accepts_live_signals


def test_quant_system_live_signal_gate_reads_strategy_control_file(tmp_path):
    from quant.infrastructure.execution.strategy_controls import apply_strategy_control_action
    from quant.quant_system import QuantSystem

    control_file = tmp_path / "strategy_controls.json"
    apply_strategy_control_action(
        "DemoStrategy",
        "pause",
        control_file,
        default_live_enabled=True,
    )
    messages = []
    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {
        "system": {"mode": "live"},
        "live_trading": {"strategy_control_file": str(control_file)},
    }
    quant.logger = SimpleNamespace(info=lambda message, *args, **kwargs: messages.append(message))

    assert not QuantSystem._strategy_accepts_live_signals(quant, "DemoStrategy")

    apply_strategy_control_action("DemoStrategy", "resume", control_file)
    assert QuantSystem._strategy_accepts_live_signals(quant, "DemoStrategy")


def test_quant_system_paper_signal_gate_reads_paper_control_file(tmp_path):
    from quant.infrastructure.execution.strategy_controls import apply_strategy_control_action
    from quant.quant_system import QuantSystem

    control_file = tmp_path / "strategy_controls.json"
    apply_strategy_control_action(
        "DemoStrategy",
        "pause",
        control_file,
        default_live_enabled=True,
        mode="paper",
    )
    apply_strategy_control_action(
        "DemoStrategy",
        "resume",
        control_file,
        default_live_enabled=True,
        mode="live",
    )
    messages = []
    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {
        "system": {"mode": "paper"},
        "live_trading": {"strategy_control_file": str(control_file)},
    }
    quant.logger = SimpleNamespace(info=lambda message, *args, **kwargs: messages.append(message))

    assert not QuantSystem._strategy_accepts_live_signals(quant, "DemoStrategy")

    apply_strategy_control_action("DemoStrategy", "resume", control_file, mode="paper")
    assert QuantSystem._strategy_accepts_live_signals(quant, "DemoStrategy")


def test_quant_system_paper_mode_uses_separate_recorder_and_tracker():
    from quant.quant_system import QuantSystem

    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {"system": {"mode": "paper"}}
    quant._strategy_tracker = None

    recorder = QuantSystem._create_live_recorder(quant)
    tracker = QuantSystem._strategy_tracker_for_mode(quant)

    assert recorder.base_dir.name == "paper_trading"
    assert tracker._store is not None
    assert tracker._mode == "paper"


def test_quant_system_filters_strategy_params_and_symbols():
    from quant.features.strategies.registry import StrategyRegistry
    from quant.quant_system import QuantSystem

    class SymbolStrategy:
        def __init__(self, symbols=None, threshold=1):
            self.name = "ParamSymbolStrategy"
            self.symbols = symbols
            self.threshold = threshold

    class NoSymbolStrategy:
        def __init__(self, timing_symbol="000300"):
            self.name = "ParamNoSymbolStrategy"
            self.symbols = []
            self.timing_symbol = timing_symbol

    StrategyRegistry.register("ParamSymbolStrategy", SymbolStrategy)
    StrategyRegistry.register("ParamNoSymbolStrategy", NoSymbolStrategy)
    warnings = []
    quant = QuantSystem.__new__(QuantSystem)
    quant.logger = SimpleNamespace(
        warning=lambda message, *args, **kwargs: warnings.append((message, args)),
        error=lambda *args, **kwargs: None,
    )

    with_symbols = QuantSystem._create_strategy(
        quant,
        "ParamSymbolStrategy",
        ["600519"],
        {"threshold": 2, "ignored": True},
    )
    without_symbols = QuantSystem._create_strategy(
        quant,
        "ParamNoSymbolStrategy",
        ["510300"],
        {"timing_symbol": "000300", "ignored": True},
    )

    assert with_symbols.symbols == ["600519"]
    assert with_symbols.threshold == 2
    assert without_symbols.timing_symbol == "000300"
    assert without_symbols.symbols == []
    assert len(warnings) == 2


def test_quant_system_resolves_cn_stock_universe_as_of_signal_date():
    from quant.quant_system import QuantSystem

    class FakeProvider:
        def __init__(self):
            self.requested_symbols = None

        def list_available_symbols(self, timeframe="1d", market="hk"):
            assert market == "cn"
            return ["600001", "600002", "600003"]

        def get_bars_for_symbols(self, symbols, start, end, timeframe="1d"):
            self.requested_symbols = symbols
            return [{"symbol": "600002"}, {"symbol": "600001"}]

    provider = FakeProvider()
    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {
        "system": {"daily_signal_date": "2026-06-02"},
        "data": {"default_timeframe": "1d"},
    }
    quant.engine = SimpleNamespace(data_providers={"default": provider})

    symbols = QuantSystem._load_universe_symbols(quant, "cn_stock", {})

    assert provider.requested_symbols == ["600001", "600002", "600003"]
    assert symbols == ["600002", "600001"]


def test_quant_system_rejects_real_broker_in_paper_mode():
    from quant.quant_system import QuantSystem

    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {"system": {"mode": "paper"}}

    with pytest.raises(RuntimeError, match="trade_mode=REAL"):
        QuantSystem._assert_broker_mode_allowed(quant, "qmt", "REAL")


def test_quant_system_rejects_external_broker_adapter_in_paper_mode():
    from quant.quant_system import QuantSystem

    quant = QuantSystem.__new__(QuantSystem)
    quant.config = {"system": {"mode": "paper"}}

    with pytest.raises(RuntimeError, match="PaperBroker"):
        QuantSystem._assert_broker_adapter_allowed(quant, "qmt")

    quant.engine = SimpleNamespace(broker=SimpleNamespace(name="qmt", _trade_mode="SIMULATE"))
    with pytest.raises(RuntimeError, match="PaperBroker"):
        QuantSystem._assert_current_broker_safe_for_paper(quant)


def test_quant_system_simulate_daily_mode_preserves_explicit_live():
    from quant.quant_system import _resolve_cli_mode

    assert _resolve_cli_mode(SimpleNamespace(backtest=False, simulate_daily=True, mode=None)) == "paper"
    assert _resolve_cli_mode(SimpleNamespace(backtest=False, simulate_daily=True, mode="live")) == "live"
    with pytest.raises(ValueError, match="paper or live"):
        _resolve_cli_mode(SimpleNamespace(backtest=False, simulate_daily=True, mode="backtest"))


def test_quant_system_order_manager_and_fill_handler_use_strategy_resolvers(monkeypatch):
    from quant.quant_system import QuantSystem

    strategy_risk = ApprovingRisk(True)
    default_risk = ApprovingRisk(True)
    strategy_portfolio = DummyPortfolio()
    default_portfolio = DummyPortfolio()

    class DummyEngine:
        def __init__(self):
            self.portfolio = default_portfolio
            self.risk_engine = default_risk
            self.event_bus = EventBus()
            self.broker = None
            self._sub_risk_engines = {"DemoStrategy": strategy_risk}
            self._sub_portfolios = {"DemoStrategy": strategy_portfolio}
            self.order_manager = None

        def set_order_manager(self, manager):
            self.order_manager = manager

    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = DummyEngine()
    quant.config = {}
    quant.live_recorder = None
    quant.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    monkeypatch.setattr("quant.quant_system.get_tracker", lambda: None)

    QuantSystem._setup_order_manager(quant)

    assert quant.engine.order_manager._risk_engine_for("DemoStrategy") is strategy_risk
    assert quant._fill_handler._portfolio_for("DemoStrategy") is strategy_portfolio


def test_live_execution_manager_caps_buy_limit_by_execution_open_plus_cost_budget():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        execution_reference_price=12.0,
        strategy_name="DemoStrategy",
    ))

    assert order_id == "ORD-1"
    assert order_manager.submitted[-1]["order_type"] == "LIMIT"
    assert order_manager.submitted[-1]["price"] == pytest.approx(12.03)
    assert order_manager.submitted[-1]["signal_metadata"]["reference_price"] == pytest.approx(10.0)
    assert order_manager.submitted[-1]["signal_metadata"]["execution_reference_price"] == pytest.approx(12.0)
    assert order_manager.submitted[-1]["signal_metadata"]["execution_cost_bps"] == pytest.approx(25.0)
    assert order_manager.submitted[-1]["strategy_name"] == "DemoStrategy"


def test_live_execution_manager_caps_sell_limit_by_execution_open_minus_cost_budget():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)

    executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="SELL",
        reference_price=10.0,
        execution_reference_price=12.0,
        strategy_name="DemoStrategy",
    ))

    assert order_manager.submitted[-1]["price"] == pytest.approx(11.97)


def test_live_execution_manager_uses_historical_cost_model_signal_bar():
    order_manager = RecordingOrderManager()
    model = {
        "enabled": True,
        "markets": ["CN"],
        "tick_size": 0.01,
        "half_spread_ticks": 0.5,
        "min_slippage_bps": 5,
        "impact_coefficient": 0.15,
        "volatility_fallback": 0.01,
    }
    executor = LiveExecutionManager(
        order_manager,
        default_max_cost_bps=30,
        base_slippage_bps=5,
        execution_cost_model=model,
        default_market="CN",
    )
    executor.set_signal_bars([{
        "symbol": "600519",
        "close": 10.0,
        "adv20_value": 1_000_000.0,
        "volatility20": 0.04,
    }])

    executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        execution_reference_price=12.0,
        strategy_name="DemoStrategy",
    ))

    assert order_manager.submitted[-1]["price"] == pytest.approx(12.0132)
    assert order_manager.submitted[-1]["signal_metadata"]["execution_cost_bps"] == pytest.approx(11.0)


def test_live_execution_manager_drops_expired_targets():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    deadline = datetime(2026, 6, 1, 14, 50)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        execution_reference_price=10.0,
        strategy_name="DemoStrategy",
        deadline=deadline,
    ))
    dropped = executor.drop_expired_targets(now=deadline + timedelta(seconds=1))

    assert dropped == [order_id]
    assert order_manager.cancelled == [order_id]
    assert executor.get_target_state(order_id).status == "dropped"


def test_live_execution_manager_does_not_drop_filled_targets():
    class FilledOrderManager(RecordingOrderManager):
        def get_order_status(self, order_id):
            return OrderStatus.FILLED

    order_manager = FilledOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    deadline = datetime(2026, 6, 1, 14, 50)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        execution_reference_price=10.0,
        strategy_name="DemoStrategy",
        deadline=deadline,
    ))
    dropped = executor.drop_expired_targets(now=deadline + timedelta(seconds=1))

    assert dropped == []
    assert order_manager.cancelled == []
    assert executor.get_target_state(order_id).status == "filled"


def test_live_execution_manager_applies_default_intraday_deadline():
    order_manager = RecordingOrderManager()
    current_time = datetime(2026, 6, 1, 9, 31)
    executor = LiveExecutionManager(
        order_manager,
        default_max_cost_bps=25,
        default_deadline="14:50",
        clock=lambda: current_time,
    )

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        execution_reference_price=10.0,
        strategy_name="DemoStrategy",
    ))

    assert executor.get_target_state(order_id).target.deadline == datetime(2026, 6, 1, 14, 50)


def test_trading_context_routes_priced_orders_through_live_execution_manager():
    from quant.features.trading.engine import Context

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", 10.0, "DemoStrategy")

    assert order_id is None
    assert order_manager.submitted == []


def test_trading_context_uses_strategy_reference_price_for_market_targets():
    from quant.features.trading.engine import Context

    class QuoteBroker:
        def get_execution_reference_price(self, symbol, side=None):
            return {"open_price": 12.0, "last_price": 11.5}

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    resolver = ExecutionReferencePriceResolver(mode="live", broker=QuoteBroker())
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
        execution_reference_resolver=resolver,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", 10.0, "DemoStrategy")

    assert order_id == "ORD-1"
    assert order_manager.submitted[-1]["price"] == pytest.approx(12.03)
    assert order_manager.submitted[-1]["signal_metadata"]["reference_price"] == pytest.approx(10.0)
    assert order_manager.submitted[-1]["signal_metadata"]["execution_reference_price"] == pytest.approx(12.0)
    assert order_manager.submitted[-1]["signal_metadata"]["execution_cost_bps"] == pytest.approx(25.0)


def test_trading_context_does_not_use_execution_reference_as_cost_reference():
    from quant.features.trading.engine import Context

    class QuoteBroker:
        def get_execution_reference_price(self, symbol, side=None):
            return {"open_price": 12.0, "last_price": 11.5}

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    resolver = ExecutionReferencePriceResolver(mode="live", broker=QuoteBroker())
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
        execution_reference_resolver=resolver,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", None, "DemoStrategy")

    assert order_id is None
    assert order_manager.submitted == []


def test_trading_context_uses_signal_bar_reference_when_price_missing():
    from quant.features.trading.engine import Context

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    executor.set_signal_bars([{"symbol": "600519", "close": 10.0}])
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", None, "DemoStrategy")

    assert order_id is None
    assert order_manager.submitted == []


def test_trading_context_drops_market_target_when_reference_price_missing():
    from quant.features.trading.engine import Context

    class EmptyBroker:
        def get_execution_reference_price(self, symbol, side=None):
            return None

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    resolver = ExecutionReferencePriceResolver(mode="live", broker=EmptyBroker())
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
        execution_reference_resolver=resolver,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", None, "DemoStrategy")

    assert order_id is None
    assert order_manager.submitted == []


def test_trading_context_keeps_explicit_limit_orders_direct():
    from quant.features.trading.engine import Context

    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    context = Context(
        portfolio=None,
        risk_engine=None,
        event_bus=None,
        order_manager=order_manager,
        execution_manager=executor,
    )

    order_id = context.submit_order("600519", 1000, "BUY", "LIMIT", 9.9, "DemoStrategy")

    assert order_id == "ORD-1"
    assert order_manager.submitted[-1]["order_type"] == "LIMIT"
    assert order_manager.submitted[-1]["price"] == pytest.approx(9.9)


def test_quant_system_setup_order_manager_creates_live_execution_manager(monkeypatch):
    from quant.quant_system import QuantSystem

    class DummyEngine:
        def __init__(self):
            self.portfolio = DummyPortfolio()
            self.risk_engine = ApprovingRisk(True)
            self.event_bus = EventBus()
            self.broker = None
            self._sub_risk_engines = {}
            self._sub_portfolios = {}
            self.order_manager = None

        def set_order_manager(self, manager):
            self.order_manager = manager

    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = DummyEngine()
    quant.config = {
        "system": {"mode": "live"},
        "live_trading": {"execution": {"max_cost_bps": 18, "deadline": "14:50"}},
    }
    quant.live_recorder = None
    quant.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    monkeypatch.setattr("quant.quant_system.get_tracker", lambda: None)

    QuantSystem._setup_order_manager(quant)

    assert quant.engine.execution_manager.default_max_cost_bps == pytest.approx(18)
    assert quant.engine.execution_manager.default_deadline.strftime("%H:%M") == "14:50"


def test_quant_system_setup_order_manager_creates_cost_protection_manager_for_paper(monkeypatch):
    from quant.quant_system import QuantSystem

    class DummyEngine:
        def __init__(self):
            self.portfolio = DummyPortfolio()
            self.risk_engine = ApprovingRisk(True)
            self.event_bus = EventBus()
            self.broker = None
            self._sub_risk_engines = {}
            self._sub_portfolios = {}
            self.order_manager = None
            self.execution_manager = "existing"

        def set_order_manager(self, manager):
            self.order_manager = manager

    quant = QuantSystem.__new__(QuantSystem)
    quant.engine = DummyEngine()
    quant.config = {
        "system": {"mode": "paper"},
        "live_trading": {"execution": {"max_cost_bps": 18, "deadline": "14:50"}},
    }
    quant.live_recorder = None
    quant.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    monkeypatch.setattr("quant.quant_system.get_tracker", lambda: None)

    QuantSystem._setup_order_manager(quant)

    assert isinstance(quant.engine.execution_manager, LiveExecutionManager)
    assert quant.engine.execution_manager.default_max_cost_bps == pytest.approx(18)
