from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from quant.domain.events.base import EventType
from quant.domain.models.order import Order, OrderSide, OrderStatus, OrderType
from quant.features.trading.engine import Engine
from quant.infrastructure.events.event_bus import EventBus
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

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name=None):
        self.submitted.append({
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": order_type,
            "price": price,
            "strategy_name": strategy_name,
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
    recorder = LiveTradingRecorder(tmp_path)
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
    assert signals[0]["status"] == "accepted"
    assert len(fills) == 3
    assert perf["total_trades"] == 2
    assert perf["total_pnl"] == pytest.approx(96.0)
    assert perf["realized_pnl"] == pytest.approx(96.0)
    assert perf["win_rate"] == pytest.approx(0.5)
    assert perf["profit_factor"] == pytest.approx(198.0 / 102.0)
    assert "sortino_ratio" in perf
    assert "calmar_ratio" in perf
    assert "total_return" in perf
    assert perf["pnl_curve"][-1]["nav"] == pytest.approx(100020)
    assert perf["recent_trades"][-1]["symbol"] == "000001"


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


def test_order_manager_records_strategy_signal_and_broker_order(tmp_path):
    recorder = LiveTradingRecorder(tmp_path)
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
    assert signals[-1]["status"] == "accepted"
    assert orders[-1]["broker_order_id"] == "BRK-1"
    assert orders[-1]["status"] == "submitted"


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
    recorder = LiveTradingRecorder(tmp_path)
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
    recorder = LiveTradingRecorder(tmp_path)
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
    assert signals[-1]["reason"] == "risk_check_failed"


def test_fill_handler_records_strategy_fill(tmp_path):
    recorder = LiveTradingRecorder(tmp_path)
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


def test_recorder_accepts_domain_order_objects(tmp_path):
    recorder = LiveTradingRecorder(tmp_path)
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

    assert [item[0].name for item in quant.engine.added] == ["DemoStrategy", "OtherStrategy"]
    assert [item[1] for item in quant.engine.added] == [pytest.approx(0.2), pytest.approx(0.2)]


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


def test_live_execution_manager_caps_buy_limit_by_cost_budget():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
        strategy_name="DemoStrategy",
    ))

    assert order_id == "ORD-1"
    assert order_manager.submitted[-1]["order_type"] == "LIMIT"
    assert order_manager.submitted[-1]["price"] == pytest.approx(10.025)
    assert order_manager.submitted[-1]["strategy_name"] == "DemoStrategy"


def test_live_execution_manager_caps_sell_limit_by_cost_budget():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)

    executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="SELL",
        reference_price=10.0,
        strategy_name="DemoStrategy",
    ))

    assert order_manager.submitted[-1]["price"] == pytest.approx(9.975)


def test_live_execution_manager_drops_expired_targets():
    order_manager = RecordingOrderManager()
    executor = LiveExecutionManager(order_manager, default_max_cost_bps=25)
    deadline = datetime(2026, 6, 1, 14, 50)

    order_id = executor.submit_target(TargetOrder(
        symbol="600519",
        quantity=1000,
        side="BUY",
        reference_price=10.0,
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

    assert order_id == "ORD-1"
    assert order_manager.submitted[-1]["order_type"] == "LIMIT"
    assert order_manager.submitted[-1]["price"] == pytest.approx(10.025)


def test_trading_context_uses_broker_reference_price_for_market_targets():
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

    order_id = context.submit_order("600519", 1000, "BUY", "MARKET", 10.0, "DemoStrategy")

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
    quant.config = {"live_trading": {"execution": {"max_cost_bps": 18, "deadline": "14:50"}}}
    quant.live_recorder = None
    quant.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    monkeypatch.setattr("quant.quant_system.get_tracker", lambda: None)

    QuantSystem._setup_order_manager(quant)

    assert quant.engine.execution_manager.default_max_cost_bps == pytest.approx(18)
    assert quant.engine.execution_manager.default_deadline.strftime("%H:%M") == "14:50"
