from datetime import datetime, timedelta

import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_hk_bars,
    make_buy_and_hold_strategy,
    run_simple_backtest,
)
from quant.features.backtest.engine import Backtester
from quant.features.backtest.commission import HK_MIN_COMMISSION
from quant.features.backtest.data_provider import DataFrameProvider


HK_SYMBOLS = ["00700", "00005", "00941"]
START = datetime(2025, 1, 2)


class TestHKLotSizeIntegration:
    def test_buy_below_lot_rejected(self):
        data = make_hk_bars(["00700"], START, 10, {"00700": 400.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class TinyBuyHK:
            name = "TinyBuyHK"
            context = None
            _positions = {}

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if not self._positions:
                    self.context.order_manager.submit_order(
                        "00700", 50, "BUY", "MARKET", 400.0, "TinyBuyHK"
                    )

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[TinyBuyHK()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["00700"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        for t in buy_trades:
            assert t.quantity >= 100


class TestHKT0DayTrading:
    def test_can_buy_and_sell_same_period(self):
        data = make_hk_bars(["00700"], START, 10, {"00700": 400.0}, daily_return=0.005)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class DayTradeHK:
            name = "DayTradeHK"
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
                    om.submit_order("00700", 100, "BUY", "MARKET", 400.0, "DayTradeHK")
                elif self._day == 1:
                    om.submit_order("00700", 100, "SELL", "MARKET", 400.0, "DayTradeHK")
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
            strategies=[DayTradeHK()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["00700"],
        )
        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) >= 1
        assert result.diagnostics.t1_rejected_sells == 0


class TestHKEndToEnd:
    def test_buy_and_hold_hk_backtest(self):
        np.random.seed(42)
        data = make_hk_bars(
            HK_SYMBOLS, START, 120,
            {"00700": 400, "00005": 60, "00941": 80},
            daily_return=0.002,
        )
        bt = make_backtester()
        strategy = make_buy_and_hold_strategy("HKBuyHold", HK_SYMBOLS, quantity=100)
        result = run_simple_backtest(bt, data, [strategy], HK_SYMBOLS, initial_cash=2000000)
        assert result.final_nav > 0
        assert result.diagnostics.total_commission >= 0

    def test_hk_commission_in_trades(self):
        np.random.seed(77)
        data = make_hk_bars(["00700"], START, 30, {"00700": 400.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = make_backtester(config)

        class BuyOne:
            name = "BuyOne"
            context = None
            _positions = {}
            _ordered = False

            def on_start(self, ctx):
                self.context = ctx

            def on_before_trading(self, ctx, td):
                pass

            def on_data(self, ctx, data):
                pass

            def on_after_trading(self, ctx, td):
                if not self._ordered:
                    self.context.order_manager.submit_order(
                        "00700", 100, "BUY", "MARKET", 400.0, "BuyOne"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuyOne()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["00700"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if buy_trades:
            t = buy_trades[0]
            assert t.cost_breakdown is not None
            assert t.cost_breakdown.get("commission", 0) >= HK_MIN_COMMISSION
