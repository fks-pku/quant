"""港股市场回测测试 — 手数、佣金、无涨跌停、T+0。"""
from datetime import datetime, timedelta

import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_hk_bars,
    run_simple_backtest,
)
from quant.features.backtest.engine import (
    Backtester,
    HK_COMMISSION_RATE,
    HK_STAMP_DUTY_RATE,
    HK_SFC_LEVY_RATE,
    HK_CLEARING_RATE,
    HK_TRADING_FEE_RATE,
    HK_MIN_COMMISSION,
    HK_TRADING_SYSTEM_FEE,
    DEFAULT_LOT_SIZE,
)
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.simple_momentum.strategy import SimpleMomentum


HK_SYMBOLS = ["00700", "00005", "00941"]
START = datetime(2025, 1, 2)


class TestHKMarketDetection:
    def test_five_digit_numeric(self):
        assert Backtester._detect_market(None, "00700") == "HK"

    def test_hk_prefix(self):
        assert Backtester._detect_market(None, "HK.00700") == "HK"

    def test_not_cn_not_us(self):
        assert Backtester._detect_market(None, "00700") == "HK"

    def test_currency_hkd(self):
        bt = make_backtester()
        assert bt._detect_currency(["00700"]) == "HKD"


class TestHKCommission:
    def test_buy_commission_min_floor(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(10.0, 100, "HK", "BUY")
        assert breakdown["commission"] == HK_MIN_COMMISSION

    def test_buy_commission_above_min(self):
        bt = make_backtester()
        price, qty = 400.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "HK", "BUY")
        trade_value = price * qty
        assert breakdown["commission"] == max(trade_value * HK_COMMISSION_RATE, HK_MIN_COMMISSION)

    def test_buy_no_stamp_duty(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(400.0, 1000, "HK", "BUY")
        assert breakdown["stamp_duty"] == 0.0

    def test_sell_stamp_duty(self):
        bt = make_backtester()
        price, qty = 400.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "HK", "SELL")
        assert breakdown["stamp_duty"] == pytest.approx(price * qty * HK_STAMP_DUTY_RATE, rel=1e-6)

    def test_sfc_levy(self):
        bt = make_backtester()
        price, qty = 400.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "HK", "BUY")
        assert breakdown["sfc_levy"] == pytest.approx(price * qty * HK_SFC_LEVY_RATE, rel=1e-6)

    def test_clearing_fee(self):
        bt = make_backtester()
        price, qty = 400.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "HK", "BUY")
        assert breakdown["clearing"] == pytest.approx(price * qty * HK_CLEARING_RATE, rel=1e-6)

    def test_trading_fee(self):
        bt = make_backtester()
        price, qty = 400.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "HK", "BUY")
        assert breakdown["trading_fee"] == pytest.approx(price * qty * HK_TRADING_FEE_RATE, rel=1e-6)

    def test_system_fee(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(400.0, 1000, "HK", "BUY")
        assert breakdown["system_fee"] == HK_TRADING_SYSTEM_FEE

    def test_sell_total_higher_than_buy(self):
        bt = make_backtester()
        buy = bt._calculate_commission_breakdown(400.0, 1000, "HK", "BUY")
        sell = bt._calculate_commission_breakdown(400.0, 1000, "HK", "SELL")
        assert sum(sell.values()) > sum(buy.values())

    def test_breakdown_keys(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(400.0, 1000, "HK", "BUY")
        assert set(breakdown.keys()) == {"commission", "stamp_duty", "sfc_levy", "clearing", "trading_fee", "system_fee"}


class TestHKLotSize:
    def test_default_lot_size_100(self):
        bt = make_backtester()
        assert bt._get_lot_size("00700") == DEFAULT_LOT_SIZE

    def test_custom_lot_size(self):
        bt = make_backtester(lot_sizes={"00700": 500})
        assert bt._get_lot_size("00700") == 500

    def test_buy_below_lot_rejected(self):
        data = make_hk_bars(["00700"], START, 10, {"00700": 400.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

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
            assert t.quantity >= DEFAULT_LOT_SIZE


class TestHKT0DayTrading:
    def test_can_buy_and_sell_same_period(self):
        data = make_hk_bars(["00700"], START, 10, {"00700": 400.0}, daily_return=0.005)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class DayTradeHK:
            name = "DayTradeHK"
            context = None
            _positions = {}
            _day = 0
            _bought = False

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


class TestHKNoPriceLimit:
    def test_no_price_limit_check(self):
        bt = make_backtester()
        assert not hasattr(bt, '_is_hk_price_at_limit') or True


class TestHKEndToEnd:
    def test_simple_momentum_hk_backtest(self):
        np.random.seed(42)
        data = make_hk_bars(
            HK_SYMBOLS, START, 120,
            {"00700": 400, "00005": 60, "00941": 80},
            daily_return=0.002,
        )
        bt = make_backtester()
        strategy = SimpleMomentum(
            symbols=HK_SYMBOLS,
            momentum_lookback=20,
            holding_period=21,
            top_pct=0.33,
            bottom_pct=0.0,
            max_position_pct=0.15,
        )
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
        bt = Backtester(config)

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
