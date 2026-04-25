import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

from quant.domain.models.position import Position
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.backtest.engine import Backtester, BacktestDiagnostics
from quant.infrastructure.events import EventBus
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.execution.fill_handler import FillHandler


class TestPositionSettledQuantity:
    def test_empty_position_has_zero_settled(self):
        pos = Position(symbol="600519")
        assert pos.settled_quantity(date(2024, 1, 15)) == 0.0

    def test_bought_today_not_settled(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 15), 100)
        assert pos.settled_quantity(date(2024, 1, 15)) == 0.0

    def test_bought_yesterday_is_settled(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 14), 100)
        assert pos.settled_quantity(date(2024, 1, 15)) == 100.0

    def test_partial_settled_from_multiple_lots(self):
        pos = Position(symbol="600519", quantity=300, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 10), 100)
        pos.add_buy_lot(date(2024, 1, 14), 100)
        pos.add_buy_lot(date(2024, 1, 15), 100)
        assert pos.settled_quantity(date(2024, 1, 15)) == 200.0

    def test_remove_sell_lots_fifo(self):
        pos = Position(symbol="600519", quantity=300, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 10), 100)
        pos.add_buy_lot(date(2024, 1, 14), 200)
        pos.remove_sell_lots(150)
        assert date(2024, 1, 10) not in pos._lots
        assert pos._lots[date(2024, 1, 14)] == 150.0

    def test_settled_quantity_after_partial_sell(self):
        pos = Position(symbol="600519", quantity=200, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 10), 100)
        pos.add_buy_lot(date(2024, 1, 14), 200)
        pos.remove_sell_lots(100)
        pos.quantity = 200
        assert pos.settled_quantity(date(2024, 1, 15)) == 200.0

    def test_flat_position_clears_lots(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 10), 100)
        pos.quantity = 0
        pos.remove_sell_lots(100)
        assert len(pos._lots) == 0


class TestPortfolioSettledQuantity:
    def test_cn_symbol_detection(self):
        assert Portfolio.is_cn_symbol("600519") is True
        assert Portfolio.is_cn_symbol("000001") is True
        assert Portfolio.is_cn_symbol("300750") is True
        assert Portfolio.is_cn_symbol("AAPL") is False
        assert Portfolio.is_cn_symbol("00700") is False

    def test_settled_quantity_after_buy_today(self):
        portfolio = Portfolio(initial_cash=100000)
        today = date(2024, 1, 15)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=today)
        assert portfolio.settled_quantity("600519", today) == 0.0

    def test_settled_quantity_after_buy_yesterday(self):
        portfolio = Portfolio(initial_cash=100000)
        yesterday = date(2024, 1, 14)
        today = date(2024, 1, 15)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=yesterday)
        assert portfolio.settled_quantity("600519", today) == 100.0

    def test_settled_quantity_no_position(self):
        portfolio = Portfolio(initial_cash=100000)
        assert portfolio.settled_quantity("600519", date(2024, 1, 15)) == 0.0


class TestRiskEngineT1:
    def _make_risk_engine(self, portfolio=None):
        portfolio = portfolio or Portfolio(initial_cash=100000)
        config = {"risk": {
            "max_position_pct": 0.05,
            "max_sector_pct": 0.25,
            "max_daily_loss_pct": 0.02,
            "max_leverage": 1.5,
            "max_orders_minute": 30,
        }}
        bus = EventBus()
        return RiskEngine(config, portfolio, bus)

    def test_cn_sell_rejected_when_bought_today(self):
        portfolio = Portfolio(initial_cash=100000)
        today = date.today()
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=today)
        engine = self._make_risk_engine(portfolio)
        approved, results = engine.check_order("600519", 100, 50.0, 5000.0, side="SELL")
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert len(t1_checks) == 1
        assert t1_checks[0].passed is False
        assert approved is False

    def test_cn_sell_passes_when_bought_yesterday(self):
        portfolio = Portfolio(initial_cash=100000)
        yesterday = date.today() - timedelta(days=1)
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=yesterday)
        engine = self._make_risk_engine(portfolio)
        approved, results = engine.check_order("600519", 100, 50.0, 5000.0, side="SELL")
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert t1_checks[0].passed is True

    def test_us_sell_no_t1_check(self):
        portfolio = Portfolio(initial_cash=100000)
        engine = self._make_risk_engine(portfolio)
        approved, results = engine.check_order("AAPL", 10, 100.0, 1000.0, side="SELL")
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert t1_checks[0].passed is True

    def test_cn_sell_partial_settled(self):
        portfolio = Portfolio(initial_cash=100000)
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        portfolio.update_position("600519", quantity=100, price=50.0, cost=5000.0, trade_date=yesterday)
        portfolio.update_position("600519", quantity=100, price=51.0, cost=5100.0, trade_date=today)
        engine = self._make_risk_engine(portfolio)
        approved, results = engine.check_order("600519", 150, 50.0, 7500.0, side="SELL")
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert t1_checks[0].passed is False


class TestBacktestT1:
    def _make_backtester(self):
        config = {"backtest": {"slippage_bps": 0}, "execution": {"commission": {}}}
        return Backtester(config=config)

    def _make_bar(self, price=100.0, volume=100000, ts=None):
        return {
            "open": price, "high": price, "low": price, "close": price,
            "volume": volume,
            "timestamp": ts or datetime(2024, 1, 15),
        }

    def test_cn_buy_and_same_day_sell_rejected(self):
        bt = self._make_backtester()
        from quant.features.trading.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=100000.0)
        entry_times = {}
        entry_prices = {}
        diag = BacktestDiagnostics()

        buy_order = {
            "symbol": "600519", "quantity": 100, "side": "BUY",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }

        bar_day1 = self._make_bar(price=50.0, ts=datetime(2024, 1, 15))
        trade = bt._execute_order(buy_order, portfolio, "600519", bar_day1, entry_times, entry_prices, diag)
        assert trade is not None
        assert portfolio.get_position("600519").quantity == 100

        sell_order = {
            "symbol": "600519", "quantity": 100, "side": "SELL",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }

        sell_trade = bt._execute_order(sell_order, portfolio, "600519", bar_day1, entry_times, entry_prices, diag)
        assert sell_trade is None
        assert diag.t1_rejected_sells == 1

    def test_cn_buy_today_sell_tomorrow_passes(self):
        bt = self._make_backtester()
        from quant.features.trading.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=100000.0)
        entry_times = {}
        entry_prices = {}
        diag = BacktestDiagnostics()

        buy_order = {
            "symbol": "600519", "quantity": 100, "side": "BUY",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }
        bar_day1 = self._make_bar(price=50.0, ts=datetime(2024, 1, 15))
        bt._execute_order(buy_order, portfolio, "600519", bar_day1, entry_times, entry_prices, diag)

        sell_order = {
            "symbol": "600519", "quantity": 100, "side": "SELL",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 15), "_deferred_days": 0,
        }
        bar_day2 = self._make_bar(price=55.0, ts=datetime(2024, 1, 16))
        sell_trade = bt._execute_order(sell_order, portfolio, "600519", bar_day2, entry_times, entry_prices, diag)
        assert sell_trade is not None
        assert sell_trade.side == "SELL"
        assert sell_trade.quantity == 100

    def test_cn_partial_sell_only_settled(self):
        bt = self._make_backtester()
        from quant.features.trading.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=200000.0)
        entry_times = {}
        entry_prices = {}
        diag = BacktestDiagnostics()

        buy1 = {
            "symbol": "600519", "quantity": 100, "side": "BUY",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 13), "_deferred_days": 0,
        }
        bar_day1 = self._make_bar(price=50.0, ts=datetime(2024, 1, 14))
        bt._execute_order(buy1, portfolio, "600519", bar_day1, entry_times, entry_prices, diag)

        buy2 = {
            "symbol": "600519", "quantity": 100, "side": "BUY",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }
        bar_day2 = self._make_bar(price=52.0, ts=datetime(2024, 1, 15))
        bt._execute_order(buy2, portfolio, "600519", bar_day2, entry_times, entry_prices, diag)

        assert portfolio.get_position("600519").quantity == 200

        sell_order = {
            "symbol": "600519", "quantity": 200, "side": "SELL",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }
        sell_trade = bt._execute_order(sell_order, portfolio, "600519", bar_day2, entry_times, entry_prices, diag)
        assert sell_trade is not None
        assert sell_trade.quantity == 100
        assert diag.t1_rejected_sells == 0

    def test_us_sell_same_day_passes(self):
        bt = self._make_backtester()
        from quant.features.trading.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=100000.0)
        entry_times = {}
        entry_prices = {}
        diag = BacktestDiagnostics()

        buy_order = {
            "symbol": "AAPL", "quantity": 10, "side": "BUY",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }
        bar = self._make_bar(price=100.0, ts=datetime(2024, 1, 15))
        bt._execute_order(buy_order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)

        sell_order = {
            "symbol": "AAPL", "quantity": 10, "side": "SELL",
            "order_type": "MARKET", "price": None, "strategy": "test",
            "_signal_date": datetime(2024, 1, 14), "_deferred_days": 0,
        }
        sell_trade = bt._execute_order(sell_order, portfolio, "AAPL", bar, entry_times, entry_prices, diag)
        assert sell_trade is not None


class TestPaperBrokerT1:
    def test_cn_sell_same_day_rejected(self):
        broker = PaperBroker(initial_cash=100000, slippage_bps=0)
        broker.connect()
        broker.update_price("600519", 50.0)

        buy_order = Order(
            symbol="600519", quantity=100, side=OrderSide.BUY,
            order_type=OrderType.MARKET, price=50.0,
        )
        broker.submit_order(buy_order)
        assert broker.positions["600519"].quantity == 100

        sell_order = Order(
            symbol="600519", quantity=100, side=OrderSide.SELL,
            order_type=OrderType.MARKET, price=55.0,
        )
        broker.submit_order(sell_order)
        result = broker.get_order_status(list(broker.orders.keys())[-1])
        assert result == OrderStatus.REJECTED
        assert broker.positions["600519"].quantity == 100

    def test_us_sell_same_day_passes(self):
        broker = PaperBroker(initial_cash=100000, slippage_bps=0)
        broker.connect()
        broker.update_price("AAPL", 100.0)

        buy_order = Order(
            symbol="AAPL", quantity=10, side=OrderSide.BUY,
            order_type=OrderType.MARKET, price=100.0,
        )
        broker.submit_order(buy_order)
        assert broker.positions["AAPL"].quantity == 10

        sell_order = Order(
            symbol="AAPL", quantity=10, side=OrderSide.SELL,
            order_type=OrderType.MARKET, price=105.0,
        )
        broker.submit_order(sell_order)
        result = broker.get_order_status(list(broker.orders.keys())[-1])
        assert result == OrderStatus.FILLED


class TestFillHandlerT1:
    def test_cn_sell_same_day_reduced_to_zero(self):
        portfolio = Portfolio(initial_cash=100000)
        bus = EventBus()
        handler = FillHandler(portfolio, bus, {})

        today = datetime(2024, 1, 15)
        handler.process_fill("ord1", "600519", "BUY", 100, 50.0, commission=1.0, timestamp=today)

        fill = handler.process_fill("ord2", "600519", "SELL", 100, 55.0, commission=1.0, timestamp=today)
        pos = portfolio.get_position("600519")
        assert pos.quantity == 100

    def test_cn_sell_yesterday_passes(self):
        portfolio = Portfolio(initial_cash=100000)
        bus = EventBus()
        handler = FillHandler(portfolio, bus, {})

        yesterday = datetime(2024, 1, 14)
        handler.process_fill("ord1", "600519", "BUY", 100, 50.0, commission=1.0, timestamp=yesterday)

        today = datetime(2024, 1, 15)
        fill = handler.process_fill("ord2", "600519", "SELL", 100, 55.0, commission=1.0, timestamp=today)
        pos = portfolio.get_position("600519")
        assert pos.quantity == 0

    def test_us_sell_same_day_passes(self):
        portfolio = Portfolio(initial_cash=100000)
        bus = EventBus()
        handler = FillHandler(portfolio, bus, {})

        today = datetime(2024, 1, 15)
        handler.process_fill("ord1", "AAPL", "BUY", 10, 100.0, commission=1.0, timestamp=today)
        handler.process_fill("ord2", "AAPL", "SELL", 10, 105.0, commission=1.0, timestamp=today)
        pos = portfolio.get_position("AAPL")
        assert pos.quantity == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
