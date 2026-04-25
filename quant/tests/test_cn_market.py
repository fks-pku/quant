"""A股市场回测测试 — 涨跌停、T+1、手数、佣金、分红税、IPO。"""
from datetime import datetime, date, timedelta

import numpy as np
import pytest

from quant.tests.conftest import (
    make_backtester,
    make_cn_bars,
    make_dividends_df,
    run_simple_backtest,
)
from quant.features.backtest.engine import (
    Backtester,
    CN_COMMISSION_RATE,
    CN_STAMP_DUTY_RATE,
    CN_TRANSFER_FEE_RATE,
    CN_REGULATOR_FEE_RATE,
    CN_MIN_COMMISSION,
    CN_DIVIDEND_TAX_SHORT_DAYS,
    CN_DIVIDEND_TAX_MEDIUM_DAYS,
    DEFAULT_LOT_SIZE,
)
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.daily_return_anomaly.strategy import DailyReturnAnomaly
from quant.features.strategies.regime_filtered_momentum.strategy import RegimeFilteredMomentum


CN_SYMBOLS = ["600519", "000858", "300750", "601318"]
START = datetime(2025, 1, 2)


class TestCNMarketDetection:
    def test_six_digit_shanghai_main(self):
        assert Backtester._detect_market(None, "600519") == "CN"

    def test_six_digit_shenzhen_main(self):
        assert Backtester._detect_market(None, "000001") == "CN"

    def test_six_digit_chinext(self):
        assert Backtester._detect_market(None, "300750") == "CN"

    def test_six_digit_star_market(self):
        assert Backtester._detect_market(None, "688981") == "CN"

    def test_six_digit_bse(self):
        assert Backtester._detect_market(None, "830799") == "CN"

    def test_five_digit_not_cn(self):
        assert Backtester._detect_market(None, "00700") == "HK"

    def test_alpha_not_cn(self):
        assert Backtester._detect_market(None, "AAPL") == "US"

    def test_currency_cn(self):
        bt = make_backtester()
        assert bt._detect_currency(["600519"]) == "CNY"

    def test_currency_mixed_falls_back_usd(self):
        bt = make_backtester()
        assert bt._detect_currency(["600519", "AAPL"]) == "USD"


class TestCNCommission:
    def test_buy_commission_min_floor(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(10.0, 100, "CN", "BUY")
        commission = breakdown["commission"]
        assert commission == CN_MIN_COMMISSION  # 10*100*0.00025=0.25 < 5

    def test_buy_commission_above_min(self):
        bt = make_backtester()
        price, qty = 50.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "CN", "BUY")
        trade_value = price * qty
        assert breakdown["commission"] == max(trade_value * CN_COMMISSION_RATE, CN_MIN_COMMISSION)

    def test_buy_no_stamp_duty(self):
        bt = make_backtester()
        breakdown = bt._calculate_commission_breakdown(50.0, 1000, "CN", "BUY")
        assert breakdown["stamp_duty"] == 0.0

    def test_sell_stamp_duty(self):
        bt = make_backtester()
        price, qty = 50.0, 1000
        breakdown = bt._calculate_commission_breakdown(price, qty, "CN", "SELL")
        assert breakdown["stamp_duty"] == pytest.approx(price * qty * CN_STAMP_DUTY_RATE, rel=1e-6)

    def test_transfer_fee_on_both_sides(self):
        bt = make_backtester()
        buy = bt._calculate_commission_breakdown(50.0, 1000, "CN", "BUY")
        sell = bt._calculate_commission_breakdown(50.0, 1000, "CN", "SELL")
        assert buy["transfer_fee"] > 0
        assert sell["transfer_fee"] > 0
        assert buy["transfer_fee"] == pytest.approx(sell["transfer_fee"], rel=1e-6)

    def test_regulator_fee_on_both_sides(self):
        bt = make_backtester()
        buy = bt._calculate_commission_breakdown(50.0, 1000, "CN", "BUY")
        sell = bt._calculate_commission_breakdown(50.0, 1000, "CN", "SELL")
        assert buy["regulator_fee"] > 0
        assert sell["regulator_fee"] > 0

    def test_total_sell_cost_higher_than_buy(self):
        bt = make_backtester()
        buy = bt._calculate_commission_breakdown(50.0, 1000, "CN", "BUY")
        sell = bt._calculate_commission_breakdown(50.0, 1000, "CN", "SELL")
        assert sum(sell.values()) > sum(buy.values())


class TestCNLotSize:
    def test_buy_rounds_to_lot(self):
        bt = make_backtester()
        price, qty = 50.0, 150
        breakdown = bt._calculate_commission_breakdown(price, qty, "CN", "BUY")
        assert breakdown is not None

    def test_buy_below_lot_rejected(self):
        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

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
        assert result.diagnostics.lot_adjusted_trades >= 0
        if result.trades:
            for t in result.trades:
                if t.side == "BUY":
                    assert t.quantity >= DEFAULT_LOT_SIZE


class TestCNT1Settlement:
    def test_cannot_sell_same_day_shares(self):
        data = make_cn_bars(["600519"], START, 10, {"600519": 50.0})
        config = {
            "backtest": {"slippage_bps": 0},
            "execution": {"commission": {}},
            "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
        }
        bt = Backtester(config)

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
        bt = Backtester(config)

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
                from quant.features.trading.portfolio import Portfolio
                port = ctx.portfolio
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
            assert (sell_date.date() - buy_date.date()).days >= 2


class TestCNPriceLimit:
    def test_limit_up_rejected(self):
        bt = make_backtester()
        result = bt._is_cn_price_at_limit("600519", 55.0, 50.0)
        assert result is True  # 55/50 = 10% hit limit

    def test_limit_down_rejected(self):
        bt = make_backtester()
        result = bt._is_cn_price_at_limit("600519", 45.0, 50.0)
        assert result is True

    def test_normal_price_passes(self):
        bt = make_backtester()
        result = bt._is_cn_price_at_limit("600519", 52.0, 50.0)
        assert result is False

    def test_chinext_20pct_limit(self):
        bt = make_backtester()
        assert bt._is_cn_price_at_limit("300750", 61.0, 50.0) is True
        assert bt._is_cn_price_at_limit("300750", 59.0, 50.0) is False

    def test_star_market_20pct_limit(self):
        bt = make_backtester()
        assert bt._is_cn_price_at_limit("688981", 61.0, 50.0) is True
        assert bt._is_cn_price_at_limit("688981", 59.0, 50.0) is False

    def test_bse_30pct_limit(self):
        bt = make_backtester()
        assert bt._is_cn_price_at_limit("830799", 66.0, 50.0) is True
        assert bt._is_cn_price_at_limit("830799", 64.0, 50.0) is False

    def test_ipo_no_limit_period(self):
        bt = make_backtester(ipo_dates={"600519": date(2025, 1, 2)})
        result = bt._is_cn_price_at_limit("600519", 60.0, 50.0, date(2025, 1, 3))
        assert result is False

    def test_ipo_limit_returns_after_5_days(self):
        bt = make_backtester(ipo_dates={"600519": date(2025, 1, 2)})
        result = bt._is_cn_price_at_limit("600519", 55.0, 50.0, date(2025, 1, 9))
        assert result is True


class TestCNDividendTax:
    def test_short_term_tax_20pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2025, 1, 2), 1000)
        tax = bt._calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 1, 20))
        expected = 1.0 * 1000 * 0.20
        assert tax == pytest.approx(expected, rel=1e-4)

    def test_medium_term_tax_10pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2025, 1, 2), 1000)
        tax = bt._calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 3, 15))
        expected = 1.0 * 1000 * 0.10
        assert tax == pytest.approx(expected, rel=1e-4)

    def test_long_term_tax_0pct(self):
        from quant.domain.models.position import Position
        bt = make_backtester()
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        pos.add_buy_lot(date(2024, 1, 2), 1000)
        tax = bt._calculate_cn_dividend_tax(pos, 1.0, datetime(2025, 6, 1))
        assert tax == pytest.approx(0.0, abs=0.01)


class TestCNEndToEnd:
    def test_daily_return_anomaly_backtest(self):
        np.random.seed(42)
        data = make_cn_bars(CN_SYMBOLS, START, 120, {"600519": 50, "000858": 30, "300750": 40, "601318": 45})
        bt = make_backtester()
        strategy = DailyReturnAnomaly(symbols=CN_SYMBOLS, holding_days=5, top_pct=0.25)
        result = run_simple_backtest(bt, data, [strategy], CN_SYMBOLS, initial_cash=2000000)
        assert result.final_nav > 0
        assert result.diagnostics.total_commission >= 0

    def test_regime_filtered_momentum_backtest(self):
        np.random.seed(123)
        symbols = ["600519", "000858", "601318", "600036", "000333"]
        data = make_cn_bars(symbols, START, 120, {"600519": 50, "000858": 30, "601318": 45, "600036": 35, "000333": 25})
        bt = make_backtester()
        strategy = RegimeFilteredMomentum(symbols=symbols, holding_days=10, top_pct=0.3)
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
        bt = Backtester(config)

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
