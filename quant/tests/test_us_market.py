"""美股市场回测测试 — 无手数限制、每股佣金、SEC/FINRA费用、T+0。"""
from datetime import datetime, timedelta

import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_us_bars,
    run_simple_backtest,
)
from quant.features.backtest.engine import (
    Backtester,
    US_SEC_FEE_RATE,
    US_FINRA_TAF_PER_SHARE,
)
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.simple_momentum.strategy import SimpleMomentum
from quant.features.strategies.volatility_regime.strategy import VolatilityRegime


US_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
START = datetime(2025, 1, 2)


class TestUSMarketDetection:
    def test_alpha_symbol(self):
        assert Backtester._detect_market(None, "AAPL") == "US"

    def test_etf_symbol(self):
        assert Backtester._detect_market(None, "SPY") == "US"

    def test_currency_usd(self):
        bt = make_backtester()
        assert bt._detect_currency(["AAPL"]) == "USD"

    def test_mixed_market_fallback_usd(self):
        bt = make_backtester()
        assert bt._detect_currency(["AAPL", "00700"]) == "USD"


class TestUSCommission:
    def test_buy_per_share_commission(self):
        bt = make_backtester()
        price, qty = 150.0, 100
        breakdown = bt._calculate_commission_breakdown(price, qty, "US", "BUY")
        assert breakdown["commission"] == max(qty * 0.005, 1.0)

    def test_min_commission_floor(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(150.0, 10, "US", "BUY")
        assert breakdown["commission"] == 1.0

    def test_buy_no_sec_fee(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(150.0, 100, "US", "BUY")
        assert "sec_fee" not in breakdown
        assert "finra_taf" not in breakdown

    def test_sell_sec_fee(self):
        bt = make_backtester()
        price, qty = 150.0, 100
        breakdown = bt._calculate_commission_breakdown(price, qty, "US", "SELL")
        assert breakdown["sec_fee"] == pytest.approx(price * qty * US_SEC_FEE_RATE, rel=1e-6)

    def test_sell_finra_taf(self):
        bt = make_backtester()
        price, qty = 150.0, 100
        breakdown = bt._calculate_commission_breakdown(price, qty, "US", "SELL")
        assert breakdown["finra_taf"] == pytest.approx(qty * US_FINRA_TAF_PER_SHARE, rel=1e-6)

    def test_sell_total_higher_than_buy(self):
        bt = make_backtester()
        buy = bt._calculate_commission_breakdown(150.0, 100, "US", "BUY")
        sell = bt._calculate_commission_breakdown(150.0, 100, "US", "SELL")
        assert sum(sell.values()) > sum(buy.values())

    def test_breakdown_keys_buy(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(150.0, 100, "US", "BUY")
        assert set(breakdown.keys()) == {"commission"}

    def test_breakdown_keys_sell(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(150.0, 100, "US", "SELL")
        assert set(breakdown.keys()) == {"commission", "sec_fee", "finra_taf"}


class TestUSNoLotSize:
    def test_buy_single_share(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyOneShare:
            name = "BuyOneShare"
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
                        "AAPL", 1, "BUY", "MARKET", 150.0, "BuyOneShare"
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
            strategies=[BuyOneShare()],
            initial_cash=100000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        assert buy_trades[0].quantity == 1


class TestUST0DayTrading:
    def test_can_day_trade(self):
        data = make_us_bars(["AAPL"], START, 10, {"AAPL": 150.0}, daily_return=0.005)
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class DayTradeUS:
            name = "DayTradeUS"
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
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "DayTradeUS")
                elif self._day == 1:
                    om.submit_order("AAPL", 100, "SELL", "MARKET", 150.0, "DayTradeUS")
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
            strategies=[DayTradeUS()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) >= 1
        assert result.diagnostics.t1_rejected_sells == 0


class TestUSSlippage:
    def test_slippage_applied_to_buy(self):
        config = {
            "backtest": {"slippage_bps": 10},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuySlippage:
            name = "BuySlippage"
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
                        "AAPL", 100, "BUY", "MARKET", 150.0, "BuySlippage"
                    )
                    self._ordered = True

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[BuySlippage()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if buy_trades:
            fill_ts = buy_trades[0].fill_date
            bar = provider.get_bar_for_date("AAPL", fill_ts)
            if bar:
                raw_open = bar["open"]
                assert buy_trades[0].fill_price >= raw_open

    def test_slippage_applied_to_sell(self):
        config = {
            "backtest": {"slippage_bps": 10},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class SellSlippage:
            name = "SellSlippage"
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
                    om.submit_order("AAPL", 100, "BUY", "MARKET", 150.0, "SellSlippage")
                elif self._day == 1:
                    om.submit_order("AAPL", 100, "SELL", "MARKET", 150.0, "SellSlippage")
                self._day += 1

            def on_fill(self, ctx, fill):
                qty = fill.quantity if fill.side == "BUY" else -fill.quantity
                self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

            def on_stop(self, ctx):
                pass

        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        provider = DataFrameProvider(data)
        result = bt.run(
            start=data["timestamp"].min(),
            end=data["timestamp"].max(),
            strategies=[SellSlippage()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        sell_trades = [t for t in result.trades if t.side == "SELL"]
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        if sell_trades and buy_trades:
            sell_bar = data[data["timestamp"].dt.date == sell_trades[0].fill_date.date()]
            if not sell_bar.empty:
                raw_open = sell_bar.iloc[0]["open"]
                assert sell_trades[0].fill_price <= raw_open


class TestUSEndToEnd:
    def test_simple_momentum_us_backtest(self):
        np.random.seed(42)
        data = make_us_bars(
            US_SYMBOLS, START, 120,
            {"AAPL": 180, "MSFT": 400, "GOOGL": 140, "AMZN": 185, "TSLA": 250},
            daily_return=0.002,
        )
        bt = make_backtester()
        strategy = SimpleMomentum(
            symbols=US_SYMBOLS,
            momentum_lookback=20,
            holding_period=21,
            top_pct=0.2,
            bottom_pct=0.0,
            max_position_pct=0.10,
        )
        result = run_simple_backtest(bt, data, [strategy], US_SYMBOLS, initial_cash=1000000)
        assert result.final_nav > 0
        assert result.diagnostics.total_commission >= 0

    def test_volatility_regime_us_backtest(self):
        np.random.seed(123)
        data = make_us_bars(
            US_SYMBOLS, START, 120,
            {"AAPL": 180, "MSFT": 400, "GOOGL": 140, "AMZN": 185, "TSLA": 250},
            daily_return=0.001,
        )
        bt = make_backtester()
        strategy = VolatilityRegime(
            symbols=US_SYMBOLS,
            max_position_pct=0.10,
        )
        result = run_simple_backtest(bt, data, [strategy], US_SYMBOLS, initial_cash=1000000)
        assert result.final_nav > 0

    def test_us_buy_trade_commission_cost_breakdown(self):
        data = make_us_bars(["AAPL"], START, 5, {"AAPL": 150.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

        class BuyOnly:
            name = "BuyOnly"
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
                        "AAPL", 100, "BUY", "MARKET", 150.0, "BuyOnly"
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
            strategies=[BuyOnly()],
            initial_cash=1000000,
            data_provider=provider,
            symbols=["AAPL"],
        )
        buy_trades = [t for t in result.trades if t.side == "BUY"]
        assert len(buy_trades) >= 1
        t = buy_trades[0]
        assert t.cost_breakdown is not None
        assert "commission" in t.cost_breakdown
        assert t.pnl < 0
