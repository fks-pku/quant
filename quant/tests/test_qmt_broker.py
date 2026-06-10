import sys
import threading
import types
from types import SimpleNamespace

import pytest

from quant.domain.models.order import Order, OrderSide, OrderType
from quant.infrastructure.execution.brokers.qmt import (
    QMTBroker,
    _cn_symbol_to_qmt,
    _normalize_qmt_limit_price,
    _qmt_trade_callback,
    _qmt_symbol_to_cn,
)


class FakeStockAccount:
    def __init__(self, account_id, account_type="STOCK"):
        self.account_id = account_id
        self.account_type = account_type


class FakeCallback:
    pass


class FakeAsset:
    account_id = "123456"
    cash = 88_000.0
    frozen_cash = 2_000.0
    market_value = 12_000.0
    total_asset = 100_000.0


class FakePosition:
    stock_code = "600519.SH"
    volume = 200
    avg_price = 101.5
    market_value = 21_000.0
    position_profit = 700.0


class FakeTrade:
    order_id = 9001
    trade_id = "T-9001-1"
    stock_code = "600519.SH"
    traded_price = 101.5
    traded_volume = 100
    order_type = 23
    commission = 1.25
    traded_time = "2026-06-03 09:35:00"
    strategy_name = "DemoStrategy"


class FakeOrderHistory:
    order_id = 9002
    stock_code = "510300.SH"
    order_type = 23
    order_volume = 300
    order_price = 4.769
    order_status = 56
    order_time = 1780969187
    strategy_name = "ashare_broad_asset_etf_"


class FakeTrader:
    instances = []

    def __init__(self, path, session_id):
        self.path = path
        self.session_id = session_id
        self.callback = None
        self.started = False
        self.connected = False
        self.subscribed_account = None
        self.orders = []
        self.cancelled = []
        FakeTrader.instances.append(self)

    def register_callback(self, callback):
        self.callback = callback

    def start(self):
        self.started = True

    def connect(self):
        self.connected = True
        return 0

    def subscribe(self, account):
        self.subscribed_account = account
        return 0

    def stop(self):
        self.connected = False

    def order_stock(self, account, stock_code, order_type, volume, price_type, price, strategy_name, order_remark):
        self.orders.append((account, stock_code, order_type, volume, price_type, price, strategy_name, order_remark))
        return 9001

    def cancel_order_stock(self, account, order_id):
        self.cancelled.append((account, order_id))
        return 0

    def query_stock_asset(self, account):
        self.asset_account = account
        return FakeAsset()

    def query_stock_positions(self, account):
        self.positions_account = account
        return [FakePosition()]

    def query_stock_trades(self, account):
        self.trades_account = account
        return [FakeTrade()]

    def query_stock_orders(self, account):
        self.orders_account = account
        return [FakeOrderHistory()]


@pytest.fixture(autouse=True)
def fake_xtquant(monkeypatch):
    xtquant = types.ModuleType("xtquant")
    xttrader = types.ModuleType("xtquant.xttrader")
    xttype = types.ModuleType("xtquant.xttype")
    xtconstant = types.ModuleType("xtquant.xtconstant")
    xtdata = types.ModuleType("xtquant.xtdata")

    xttrader.XtQuantTrader = FakeTrader
    xttrader.XtQuantTraderCallback = FakeCallback
    xttype.StockAccount = FakeStockAccount
    xtconstant.STOCK_BUY = 23
    xtconstant.STOCK_SELL = 24
    xtconstant.LATEST_PRICE = 5
    xtconstant.FIX_PRICE = 11
    xtdata.full_ticks = {
        "600519.SH": {
            "openPrice": 102.0,
            "lastPrice": 103.0,
            "bidPrice": [101.9],
            "askPrice": [102.1],
        }
    }

    def get_full_tick(symbols):
        return {symbol: xtdata.full_ticks.get(symbol, {}) for symbol in symbols}

    xtdata.get_full_tick = get_full_tick

    xtquant.xttrader = xttrader
    xtquant.xttype = xttype
    xtquant.xtconstant = xtconstant
    xtquant.xtdata = xtdata

    monkeypatch.setitem(sys.modules, "xtquant", xtquant)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", xttrader)
    monkeypatch.setitem(sys.modules, "xtquant.xttype", xttype)
    monkeypatch.setitem(sys.modules, "xtquant.xtconstant", xtconstant)
    monkeypatch.setitem(sys.modules, "xtquant.xtdata", xtdata)
    FakeTrader.instances.clear()


def test_qmt_symbol_conversion_accepts_internal_and_qmt_formats():
    assert _cn_symbol_to_qmt("600519") == "600519.SH"
    assert _cn_symbol_to_qmt("000001") == "000001.SZ"
    assert _cn_symbol_to_qmt("920002") == "920002.BJ"
    assert _cn_symbol_to_qmt("600519.SH") == "600519.SH"
    assert _cn_symbol_to_qmt("SZ.000001") == "000001.SZ"
    assert _qmt_symbol_to_cn("600519.SH") == "600519"
    assert _qmt_symbol_to_cn("000001.SZ") == "000001"


def test_qmt_limit_price_rounds_to_exchange_tick_preserving_side_bound():
    assert _normalize_qmt_limit_price("518880.SH", OrderSide.BUY, 9.307839999999999) == pytest.approx(9.307)
    assert _normalize_qmt_limit_price("518880.SH", OrderSide.SELL, 9.307839999999999) == pytest.approx(9.308)
    assert _normalize_qmt_limit_price("159949.SZ", OrderSide.BUY, 2.000985) == pytest.approx(2.000)
    assert _normalize_qmt_limit_price("600519.SH", OrderSide.BUY, 101.239) == pytest.approx(101.23)
    assert _normalize_qmt_limit_price("600519.SH", OrderSide.SELL, 101.231) == pytest.approx(101.24)


def test_connect_registers_callback_and_subscribes_stock_account():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", account_type="STOCK")

    broker.connect()

    trader = FakeTrader.instances[-1]
    assert trader.path == "D:/QMT/userdata_mini"
    assert trader.started is True
    assert trader.connected is True
    assert isinstance(trader.callback, FakeCallback)
    assert isinstance(trader.subscribed_account, FakeStockAccount)
    assert trader.subscribed_account.account_id == "123456"


def test_submit_order_uses_stock_account_and_xtconstant_values():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", trade_mode="REAL")
    broker.connect()

    order_id = broker.submit_order(
        Order(
            symbol="600519.SH",
            quantity=200,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=101.23,
            strategy_name="demo",
        )
    )

    trader = FakeTrader.instances[-1]
    account, stock_code, order_type, volume, price_type, price, strategy_name, remark = trader.orders[-1]
    assert order_id == "9001"
    assert isinstance(account, FakeStockAccount)
    assert stock_code == "600519.SH"
    assert order_type == 23
    assert volume == 200
    assert price_type == 11
    assert price == pytest.approx(101.23)
    assert strategy_name == "demo"
    assert remark == ""


def test_submit_order_normalizes_limit_price_before_qmt_call():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", trade_mode="REAL")
    broker.connect()

    broker.submit_order(
        Order(
            symbol="518880.SH",
            quantity=1000,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=9.307839999999999,
            strategy_name="barbell",
        )
    )

    trader = FakeTrader.instances[-1]
    account, stock_code, order_type, volume, price_type, price, strategy_name, remark = trader.orders[-1]
    assert isinstance(account, FakeStockAccount)
    assert stock_code == "518880.SH"
    assert order_type == 23
    assert volume == 1000
    assert price_type == 11
    assert price == pytest.approx(9.307)
    assert strategy_name == "barbell"
    assert remark == ""


def test_submit_order_does_not_hold_lock_during_qmt_order_stock_callback_probe():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", trade_mode="REAL")
    broker.connect()
    trader = FakeTrader.instances[-1]

    def order_stock_with_callback_probe(account, stock_code, order_type, volume, price_type, price, strategy_name, order_remark):
        acquired = []

        def probe_callback_thread():
            with broker._lock:
                acquired.append(True)

        thread = threading.Thread(target=probe_callback_thread)
        thread.start()
        thread.join(timeout=0.5)
        assert acquired == [True]
        trader.orders.append((account, stock_code, order_type, volume, price_type, price, strategy_name, order_remark))
        return 9002

    trader.order_stock = order_stock_with_callback_probe

    order_id = broker.submit_order(
        Order(
            symbol="510300",
            quantity=300,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=4.769,
            strategy_name="ashare_broad_asset_etf_rotation",
        )
    )

    assert order_id == "9002"


def test_qmt_simulate_trade_mode_refuses_order_submission():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", trade_mode="SIMULATE")
    broker.connect()

    with pytest.raises(RuntimeError, match="not a verified sandbox"):
        broker.submit_order(
            Order(
                symbol="600519.SH",
                quantity=200,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=101.23,
                strategy_name="demo",
            )
        )

    trader = FakeTrader.instances[-1]
    assert trader.orders == []


def test_queries_map_xtquant_asset_and_position_objects():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
    broker.connect()

    account = broker.get_account_info()
    positions = broker.get_positions()

    trader = FakeTrader.instances[-1]
    assert isinstance(trader.asset_account, FakeStockAccount)
    assert isinstance(trader.positions_account, FakeStockAccount)
    assert account.account_id == "123456"
    assert account.cash == pytest.approx(88_000.0)
    assert account.buying_power == pytest.approx(88_000.0)
    assert account.equity == pytest.approx(100_000.0)
    assert len(positions) == 1
    assert positions[0].symbol == "600519"
    assert positions[0].quantity == pytest.approx(200)
    assert positions[0].avg_cost == pytest.approx(101.5)
    assert positions[0].market_value == pytest.approx(21_000.0)
    assert positions[0].unrealized_pnl == pytest.approx(700.0)


def test_qmt_trade_history_maps_xtquant_trade_records():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
    broker.connect()

    trades = broker.get_trade_history()

    trader = FakeTrader.instances[-1]
    assert isinstance(trader.trades_account, FakeStockAccount)
    assert trades == [{
        "order_id": "9001",
        "trade_id": "T-9001-1",
        "timestamp": "2026-06-03T09:35:00",
        "strategy_name": "DemoStrategy",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100.0,
        "price": 101.5,
        "commission": 1.25,
    }]


def test_qmt_order_history_maps_filled_status_and_epoch_timestamp():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
    broker.connect()

    orders = broker.get_order_history()

    trader = FakeTrader.instances[-1]
    assert isinstance(trader.orders_account, FakeStockAccount)
    assert orders == [{
        "order_id": "9002",
        "timestamp": "2026-06-09T09:39:47",
        "strategy_name": "ashare_broad_asset_etf_",
        "symbol": "510300",
        "side": "BUY",
        "quantity": 300.0,
        "price": 4.769,
        "status": "56",
    }]


def test_qmt_quote_reference_price_prefers_open_price():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
    broker.connect()

    quote = broker.get_quote("600519")
    reference = broker.get_execution_reference_price("600519", "BUY")

    assert quote["open_price"] == pytest.approx(102.0)
    assert quote["last_price"] == pytest.approx(103.0)
    assert reference["open_price"] == pytest.approx(102.0)


def test_qmt_trade_callback_notifies_registered_fill_callback():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456", trade_mode="REAL")
    broker.connect()
    seen = []
    broker.register_trade_callback(lambda **kwargs: seen.append(kwargs))

    broker.submit_order(
        Order(
            symbol="600519",
            quantity=200,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=101.23,
            strategy_name="DemoStrategy",
        )
    )

    _qmt_trade_callback(
        broker,
        SimpleNamespace(
            order_id=9001,
            traded_price=101.5,
            traded_volume=100,
            stock_code="600519.SH",
        ),
    )

    assert seen[-1]["order_id"] == "9001"
    assert seen[-1]["strategy_name"] == "DemoStrategy"
    assert seen[-1]["symbol"] == "600519"
    assert seen[-1]["side"] == "BUY"
    assert seen[-1]["quantity"] == pytest.approx(100)
    assert seen[-1]["price"] == pytest.approx(101.5)


def test_qmt_trade_callback_estimates_cn_etf_minimum_commission():
    broker = QMTBroker(
        userdata_mini_path="D:/QMT/userdata_mini",
        account="123456",
        trade_mode="REAL",
        commission_config={"CN": {"type": "cn_realistic"}},
    )
    broker.connect()
    seen = []
    broker.register_trade_callback(lambda **kwargs: seen.append(kwargs))

    broker.submit_order(
        Order(
            symbol="518880",
            quantity=1000,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=9.307,
            strategy_name="DemoStrategy",
        )
    )

    _qmt_trade_callback(
        broker,
        SimpleNamespace(
            order_id=9001,
            traded_price=9.302,
            traded_volume=1000,
            stock_code="518880.SH",
        ),
    )

    assert seen[-1]["symbol"] == "518880"
    assert seen[-1]["commission"] == pytest.approx(5.0)


def test_qmt_commission_keeps_broker_minimum_when_config_has_zero_fund_min():
    broker = QMTBroker(
        userdata_mini_path="D:/QMT/userdata_mini",
        account="123456",
        commission_config={"CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0}},
    )

    commission = broker.estimate_commission("518880", "BUY", 1000, 9.302)

    assert commission == pytest.approx(5.0)
