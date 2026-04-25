"""基础设施测试 — EventBus, Portfolio, RiskEngine。"""
from datetime import datetime, date

import pytest

from quant.infrastructure.events import EventBus
from quant.domain.events.base import EventType, Event
from quant.features.trading.portfolio import Portfolio
from quant.domain.models.position import Position
from quant.domain.models.bar import Bar
from quant.domain.models.fill import Fill
from quant.domain.models.order import Order


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR, lambda e: received.append(e))
        event = Event(event_type=EventType.BAR, data={"symbol": "AAPL"})
        bus.publish(event)
        assert len(received) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        handler = lambda e: received.append(e)
        bus.subscribe(EventType.BAR, handler)
        bus.unsubscribe(EventType.BAR, handler)
        bus.publish(Event(event_type=EventType.BAR, data={}))
        assert len(received) == 0

    def test_multiple_subscribers(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe(EventType.BAR, lambda e: r1.append(e))
        bus.subscribe(EventType.BAR, lambda e: r2.append(e))
        bus.publish(Event(event_type=EventType.BAR, data={}))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_publish_nowait(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR, lambda e: received.append(e))
        bus.publish_nowait(EventType.BAR, data={"symbol": "AAPL"})
        assert len(received) == 1


class TestPortfolio:
    def test_initial_nav(self):
        p = Portfolio(initial_cash=100000)
        assert p.nav == 100000

    def test_update_position_buy(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        pos = p.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == pytest.approx(150.0, rel=1e-4)

    def test_update_position_sell(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=date(2025, 1, 2))
        p.update_position("AAPL", quantity=-50, price=155.0, cost=0, trade_date=date(2025, 1, 3))
        pos = p.get_position("AAPL")
        assert pos.quantity == 50

    def test_close_position(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        pnl = p.close_position("AAPL", 160.0)
        assert pnl == pytest.approx(1000.0, rel=1e-4)
        pos = p.get_position("AAPL")
        assert pos.quantity == 0

    def test_nav_with_position(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.cash -= 15000.0
        assert p.nav == pytest.approx(100000.0, rel=1e-4)

    def test_get_all_positions(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.update_position("MSFT", quantity=50, price=400.0, cost=20000.0)
        all_pos = p.get_all_positions()
        assert len(all_pos) == 2

    def test_is_cn_symbol(self):
        assert Portfolio.is_cn_symbol("600519") is True
        assert Portfolio.is_cn_symbol("AAPL") is False
        assert Portfolio.is_cn_symbol("00700") is False

    def test_sector_exposure(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, sector="Tech")
        p.update_position("JPM", quantity=50, price=200.0, cost=10000.0, sector="Finance")
        p.cash -= 25000.0
        exposure = p.get_sector_exposure()
        assert "Tech" in exposure
        assert "Finance" in exposure

    def test_check_daily_loss(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        p.cash = 94000
        assert p.check_daily_loss(0.05) is True

    def test_check_daily_loss_ok(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        assert p.check_daily_loss(0.05) is False

    def test_reset_daily(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.cash -= 15000.0
        p.reset_daily()
        assert p.starting_nav == p.nav

    def test_to_dict(self):
        p = Portfolio(initial_cash=100000)
        d = p.to_dict()
        assert d["nav"] == 100000
        assert d["cash"] == 100000
        assert d["currency"] == "USD"


class TestPositionModel:
    def test_is_long(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.is_long is True

    def test_is_short(self):
        pos = Position(symbol="AAPL", quantity=-100, avg_cost=150.0)
        assert pos.is_short is True

    def test_cost_basis(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.cost_basis == 15000.0

    def test_update_market_price(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.update_market_price(160.0)
        assert pos.market_value == 16000.0
        assert pos.unrealized_pnl == 1000.0


class TestBarModel:
    def test_valid_bar(self):
        bar = Bar(
            symbol="AAPL", timestamp=datetime(2025, 1, 2),
            open=150.0, high=155.0, low=148.0, close=152.0,
            volume=1000000,
        )
        assert bar.is_bullish is True
        assert bar.range == 7.0

    def test_invalid_bar_raises(self):
        with pytest.raises(ValueError):
            Bar(
                symbol="AAPL", timestamp=datetime(2025, 1, 2),
                open=150.0, high=140.0, low=148.0, close=152.0,
            )

    def test_typical_price(self):
        bar = Bar(
            symbol="AAPL", timestamp=datetime(2025, 1, 2),
            open=150.0, high=155.0, low=145.0, close=152.0,
        )
        assert bar.typical_price == pytest.approx((155 + 145 + 152) / 3, rel=1e-4)


class TestFillModel:
    def test_fill_creation(self):
        fill = Fill(
            order_id="o1", symbol="AAPL", side="BUY",
            quantity=100, price=150.0, commission=1.0,
            timestamp=datetime(2025, 1, 2),
        )
        assert fill.symbol == "AAPL"
        assert fill.quantity == 100


class TestOrderModel:
    def test_order_creation(self):
        order = Order(
            symbol="AAPL", quantity=100, side="BUY",
            order_type="MARKET",
        )
        assert order.symbol == "AAPL"
        assert order.side == "BUY"
