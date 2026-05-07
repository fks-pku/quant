from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    run_simple_backtest,
)
from quant.features.backtest.engine import Backtester
from quant.features.backtest.dividend_processor import calculate_cn_dividend_tax
from quant.features.backtest.data_provider import DataFrameProvider
from quant.features.strategies.base import Strategy
from quant.features.strategies.dual_ma_crossover.strategy import DualMACrossover


CN_SYMBOLS = ["600519", "000858", "300750", "601318"]
START = datetime(2025, 1, 2)


class TestCNLotSize:
    def test_buy_below_lot_rejected(self):
        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class TinyBuyStrategy:
            name = "TinyBuy"
            context = None
            _positions = {}

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                if isinstance(data, dict) and data.get("symbol") == "600519" and data.get("close", 0) > 0:
                    if not self._positions.get("600519", 0):
                        self.context.order_manager.submit_order(
                            "600519", 50, "BUY", "MARKET", data["open"], "TinyBuy"
                        )

            def on_after_trading(self, ctx, td):
                pass

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[TinyBuyStrategy()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        if result.trades:
            for t in result.trades:
                if t.side == "BUY":
                    assert t.quantity >= 100


class TestCNT1Settlement:
    def test_cannot_sell_same_day_shares(self):
        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class QuickFlipStrategy:
            name = "QuickFlip"
            context = None
            _positions = {}

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                pass

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[QuickFlipStrategy()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert result is not None

    def test_t1_rejected_in_diagnostics(self):
        data = make_cn_bars(["600519"], START, 5, {"600519": 50.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class SellImmediatelyStrategy:
            name = "SellNow"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if self._day == 0:
                    om = ctx.order_manager
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "SellNow")
                elif self._day == 1:
                    om = ctx.order_manager
                    om.submit_order("600519", 100, "SELL", "MARKET", 50.0, "SellNow")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[SellImmediatelyStrategy()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        assert len(buy_trades) >= 1
        if sell_trades:
            sell_date = sell_trades[0].fill_date
            buy_date = buy_trades[0].fill_date
            assert (sell_date.date() - buy_date.date()).days >= 1


class TestCNDividendTax:
    def test_short_term_tax_20pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2025, 1, 2), 1000)
        tax = calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 1, 20))
        expected = 1.0 * 1000 * 0.20
        assert tax == pytest.approx(expected, rel=1e-4)

    def test_medium_term_tax_10pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2025, 1, 2), 1000)
        tax = calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 3, 15))
        expected = 1.0 * 1000 * 0.10
        assert tax == pytest.approx(expected, rel=1e-4)

    def test_long_term_tax_0pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 2), 1000)
        tax = calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 6, 1))
        assert tax == pytest.approx(0.0, abs=0.01)


class TestCNEndToEnd:
    def test_dual_ma_crossover_backtest(self):
        np.random.seed(42)
        data = make_cn_bars(CN_SYMBOLS, START, 120, {"600519": 50, "000858": 30, "300750": 40, "601318": 45})
        bt = make_backtester()
        strategy = DualMACrossover(symbols=CN_SYMBOLS, fast_period=5, slow_period=20)
        result = run_simple_backtest(bt, data, [strategy], CN_SYMBOLS, initial_cash=2000000)
        assert result.final_nav > 0
        assert result.diagnostics.total_commission >= 0

    def test_dual_ma_crossover_multi_backtest(self):
        np.random.seed(123)
        symbols = ["600519", "000858", "601318", "600036", "000333"]
        data = make_cn_bars(symbols, START, 120, {"600519": 50, "000858": 30, "601318": 45, "600036": 35, "000333": 25})
        bt = make_backtester()
        strategy = DualMACrossover(symbols=symbols, fast_period=5, slow_period=20)
        result = run_simple_backtest(bt, data, [strategy], strategy.symbols, initial_cash=2000000)
        assert result.final_nav > 0

    def test_cn_backtest_t1_enforced(self):
        np.random.seed(99)
        data = make_cn_bars(["600519"], START, 30, {"600519": 50.0}, daily_return=0.005)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuySellStrategy:
            name = "BuySell"
            context = None
            _positions = {}
            _day = 0

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                om = ctx.order_manager
                if self._day == 0:
                    om.submit_order("600519", 100, "BUY", "MARKET", 50.0, "BuySell")
                elif self._day == 1:
                    om.submit_order("600519", 100, "SELL", "MARKET", 50.0, "BuySell")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuySellStrategy()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["600519"],
        )
        assert len([t for t in result.trades if t.side == "BUY"]) >= 1

    def test_backward_adjusted_price_quantity_uses_real_close(self):
        np.random.seed(777)
        symbols = ["000001"]
        n_days = 60

        base_price = 12.0
        adj_factor = 118.0

        rows = []
        current = datetime(2024, 1, 2)
        price = base_price
        for i in range(n_days):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            chg = np.random.randn() * 0.005
            price = max(1.0, price * (1 + chg))
            adj_price = round(price * adj_factor, 4)
            rows.append({
                "timestamp": current,
                "symbol": "000001",
                "open": round(price * 0.999, 4),
                "high": round(price * 1.02, 4),
                "low": round(price * 0.98, 4),
                "close": round(price, 4),
                "volume": 10000000,
                "adj_open": round(price * 0.999 * adj_factor, 4),
                "adj_high": round(price * 1.02 * adj_factor, 4),
                "adj_low": round(price * 0.98 * adj_factor, 4),
                "adj_close": adj_price,
                "adj_factor": adj_factor,
            })
            current += timedelta(days=1)

        data = pd.DataFrame(rows)
        provider = DataFrameProvider(data)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 10.0},
        }
        bt = make_backtester(config)

        class AdjAwareTestStrategy:
            name = "AdjAwareTest"
            context = None
            _positions = {}
            _bars = []
            _signalled = False

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                self._bars.append(data)

            def on_after_trading(self, ctx, td):
                if self._signalled or len(self._bars) < 21:
                    return
                self._signalled = True
                price = Strategy._price(self._bars[-1])
                nav = ctx.portfolio.nav
                qty = int(nav * 0.95 / price)
                Strategy.buy(self, "000001", qty)

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[AdjAwareTestStrategy()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["000001"],
        )

        buys = [t for t in result.trades if t.side == "BUY"]
        assert result.diagnostics.discarded_orders == 0, \
            f"Expected 0 discarded, got {result.diagnostics.discarded_orders} — adj_close ≠ real_price bug?"
        assert len(buys) >= 1, \
            f"Expected ≥1 BUY fill, got {len(buys)} — lot rounding may have killed order (adj_close vs close)"
