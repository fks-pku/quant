import sys
import types
from types import SimpleNamespace

import pytest

from quant.domain.models.order import Order, OrderSide, OrderType
from quant.infrastructure.execution.brokers.qmt import (
    QMTBroker,
    _cn_symbol_to_qmt,
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


@pytest.fixture(autouse=True)
def fake_xtquant(monkeypatch):
    xtquant = types.ModuleType("xtquant")
    xttrader = types.ModuleType("xtquant.xttrader")
    xttype = types.ModuleType("xtquant.xttype")
    xtconstant = types.ModuleType("xtquant.xtconstant")

    xttrader.XtQuantTrader = FakeTrader
    xttrader.XtQuantTraderCallback = FakeCallback
    xttype.StockAccount = FakeStockAccount
    xtconstant.STOCK_BUY = 23
    xtconstant.STOCK_SELL = 24
    xtconstant.LATEST_PRICE = 5
    xtconstant.FIX_PRICE = 11

    xtquant.xttrader = xttrader
    xtquant.xttype = xttype
    xtquant.xtconstant = xtconstant

    monkeypatch.setitem(sys.modules, "xtquant", xtquant)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", xttrader)
    monkeypatch.setitem(sys.modules, "xtquant.xttype", xttype)
    monkeypatch.setitem(sys.modules, "xtquant.xtconstant", xtconstant)
    FakeTrader.instances.clear()


def test_qmt_symbol_conversion_accepts_internal_and_qmt_formats():
    assert _cn_symbol_to_qmt("600519") == "600519.SH"
    assert _cn_symbol_to_qmt("000001") == "000001.SZ"
    assert _cn_symbol_to_qmt("920002") == "920002.BJ"
    assert _cn_symbol_to_qmt("600519.SH") == "600519.SH"
    assert _cn_symbol_to_qmt("SZ.000001") == "000001.SZ"
    assert _qmt_symbol_to_cn("600519.SH") == "600519"
    assert _qmt_symbol_to_cn("000001.SZ") == "000001"


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
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
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


def test_qmt_trade_callback_notifies_registered_fill_callback():
    broker = QMTBroker(userdata_mini_path="D:/QMT/userdata_mini", account="123456")
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
