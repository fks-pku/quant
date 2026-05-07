"""Invariant-driven tests based on backtest-invariants.md (CASE-1 through CASE-8)."""
from datetime import datetime, date, timedelta

import pandas as pd
import pytest

from quant.features.backtest.engine import Backtester
from quant.features.backtest.data_provider import DataFrameProvider
from quant.tests.conftest import make_backtester


START = datetime(2024, 6, 3)


def _make_bars(symbol, rows):
    """rows: list of (date, open, close, volume)"""
    data = []
    for dt, o, c, v in rows:
        data.append({
            "symbol": symbol, "timestamp": dt,
            "open": o, "high": max(o, c) + 0.5, "low": min(o, c) - 0.5,
            "close": c, "volume": v,
        })
    return pd.DataFrame(data)


def _make_dividends(symbol, ex_dates, amounts):
    return pd.DataFrame({
        "symbol": [symbol] * len(ex_dates),
        "ex_date": ex_dates,
        "cash_dividend": amounts,
        "stock_dividend": [0.0] * len(ex_dates),
    })


def _signal_strategy(name, symbol, buy_on, sell_on, qty=100):
    class S:
        pass
    S.name = name
    S.context = None
    S._positions = {}
    S._day = -1

    def on_start(self, ctx):
        self.context = ctx

    def on_before_trading(self, ctx, td):
        pass

    def on_data(self, ctx, data):
        pass

    def on_after_trading(self, ctx, td):
        self._day += 1
        om = ctx.order_manager
        if self._day in buy_on:
            om.submit_order(symbol, qty, "BUY", "MARKET", None, name)
        if self._day in sell_on:
            om.submit_order(symbol, qty, "SELL", "MARKET", None, name)

    def on_fill(self, ctx, fill):
        q = fill.quantity if fill.side == "BUY" else -fill.quantity
        self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

    def on_stop(self, ctx):
        pass

    S.on_start = on_start
    S.on_before_trading = on_before_trading
    S.on_data = on_data
    S.on_after_trading = on_after_trading
    S.on_fill = on_fill
    S.on_stop = on_stop
    return S()


# ---------------------------------------------------------------------------
# CASE-1: US zero-friction
# ---------------------------------------------------------------------------

CASE1_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}

CASE1_BARS = [
    (datetime(2024, 6, 3), 180.00, 182.50, 5_000_000),
    (datetime(2024, 6, 4), 182.50, 184.00, 6_000_000),
    (datetime(2024, 6, 5), 184.50, 185.50, 4_500_000),
    (datetime(2024, 6, 6), 185.00, 186.00, 5_500_000),
    (datetime(2024, 6, 7), 186.50, 187.00, 7_000_000),
]


@pytest.fixture
def case1_result():
    data = _make_bars("AAPL", CASE1_BARS)
    bt = make_backtester(CASE1_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case1", "AAPL", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase1USZeroFriction:
    def test_c1_01_nav_equals_cash_plus_market_value(self, case1_result):
        ec = case1_result.equity_curve
        assert len(ec) >= 5
        assert ec.iloc[0] == pytest.approx(100_000, rel=1e-6)
        assert ec.iloc[1] == pytest.approx(100_150, rel=1e-4)
        assert ec.iloc[4] == pytest.approx(100_400, rel=1e-4)

    def test_c1_02_cash_unchanged_between_trades(self, case1_result):
        trades = case1_result.trades
        buy_trade = [t for t in trades if t.side == "BUY"][0]
        sell_trade = [t for t in trades if t.side == "SELL"][0]
        assert buy_trade.fill_price == pytest.approx(182.50, rel=1e-6)
        assert sell_trade.fill_price == pytest.approx(186.50, rel=1e-6)

    def test_c1_03_equity_curve(self, case1_result):
        expected = [100000, 100150, 100300, 100350, 100400]
        for i, v in enumerate(expected):
            assert case1_result.equity_curve.iloc[i] == pytest.approx(v, rel=1e-4)

    def test_c1_05_final_nav_equals_initial_plus_pnl(self, case1_result):
        trades = case1_result.trades
        pnl_sum = sum(t.pnl for t in trades)
        assert case1_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c1_06_total_return(self, case1_result):
        assert case1_result.total_return == pytest.approx(0.004, rel=1e-2)

    def test_c1_07_buy_trade(self, case1_result):
        buy = [t for t in case1_result.trades if t.side == "BUY"][0]
        assert buy.quantity == 100
        assert buy.entry_price == pytest.approx(182.50, rel=1e-4)
        assert buy.commission == pytest.approx(0.0, abs=0.01)

    def test_c1_08_sell_trade(self, case1_result):
        sell = [t for t in case1_result.trades if t.side == "SELL"][0]
        assert sell.realized_pnl == pytest.approx(400.0, rel=1e-2)
        assert sell.entry_price == pytest.approx(182.50, rel=1e-4)
        assert sell.exit_price == pytest.approx(186.50, rel=1e-4)

    def test_c1_09_diagnostics(self, case1_result):
        d = case1_result.diagnostics
        assert d.fill_count == 2
        assert d.total_gross_pnl == pytest.approx(400.0, rel=1e-2)

    def test_c1_10_position_state(self, case1_result):
        open_pos = case1_result.open_positions
        assert len(open_pos) == 0


# ---------------------------------------------------------------------------
# CASE-2: US with commission + slippage
# ---------------------------------------------------------------------------

CASE2_CONFIG = {
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case2_result():
    data = _make_bars("AAPL", CASE1_BARS)
    bt = make_backtester(CASE2_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case2", "AAPL", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase2USCommissionSlippage:
    def test_c2_01_equity_curve(self, case2_result):
        expected = [100000, 100139.875, 100289.875, 100339.875, 100379.01519]
        for i, v in enumerate(expected):
            assert case2_result.equity_curve.iloc[i] == pytest.approx(v, rel=1e-3)

    def test_c2_03_final_nav_equals_initial_plus_pnl(self, case2_result):
        pnl_sum = sum(t.pnl for t in case2_result.trades)
        assert case2_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c2_04_buy_trade_commission(self, case2_result):
        buy = [t for t in case2_result.trades if t.side == "BUY"][0]
        assert buy.entry_price == pytest.approx(182.59125, rel=1e-4)
        assert buy.cost_breakdown is not None
        assert buy.cost_breakdown.get("commission", 0) > 0

    def test_c2_05_sell_trade_prices(self, case2_result):
        sell = [t for t in case2_result.trades if t.side == "SELL"][0]
        assert sell.entry_price == pytest.approx(182.59125, rel=1e-4)
        assert sell.exit_price == pytest.approx(186.40675, rel=1e-4)

    def test_c2_07_sell_cost_breakdown(self, case2_result):
        sell = [t for t in case2_result.trades if t.side == "SELL"][0]
        cb = sell.cost_breakdown
        assert cb is not None
        assert "commission" in cb
        assert "sec_fee" in cb
        assert "finra_taf" in cb

    def test_c2_08_all_costs_non_negative(self, case2_result):
        sell = [t for t in case2_result.trades if t.side == "SELL"][0]
        assert all(v >= 0 for v in sell.cost_breakdown.values())

    def test_c2_09_total_commission_consistency(self, case2_result):
        d = case2_result.diagnostics
        trades = case2_result.trades
        assert d.total_commission == pytest.approx(
            sum(t.commission for t in trades), rel=1e-4
        )

    def test_c2_10_gross_pnl_invariant(self, case2_result):
        d = case2_result.diagnostics
        trades = case2_result.trades
        expected = sum(t.pnl for t in trades) + d.total_commission
        assert d.total_gross_pnl == pytest.approx(expected, rel=1e-2)


# ---------------------------------------------------------------------------
# CASE-3: CN A-share
# ---------------------------------------------------------------------------

CASE3_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}

CASE3_BARS = [
    (datetime(2024, 6, 3), 50.00, 51.00, 10_000_000),
    (datetime(2024, 6, 4), 52.00, 53.00, 12_000_000),
    (datetime(2024, 6, 5), 53.00, 52.00, 8_000_000),
    (datetime(2024, 6, 6), 52.50, 54.00, 10_000_000),
    (datetime(2024, 6, 7), 55.00, 56.00, 15_000_000),
]


@pytest.fixture
def case3_result():
    data = _make_bars("600519", CASE3_BARS)
    bt = make_backtester(CASE3_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case3", "600519", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["600519"],
    )


class TestCase3CNAShare:
    def test_c3_01_equity_curve(self, case3_result):
        expected = [100000, 100094.844, 99994.844, 100194.844, 100286.929]
        for i, v in enumerate(expected):
            assert case3_result.equity_curve.iloc[i] == pytest.approx(v, rel=5e-3)

    def test_c3_03_final_nav_equals_initial_plus_pnl(self, case3_result):
        pnl_sum = sum(t.pnl for t in case3_result.trades)
        assert case3_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c3_04_buy_no_stamp_duty(self, case3_result):
        buy = [t for t in case3_result.trades if t.side == "BUY"][0]
        assert buy.pnl < 0
        if buy.cost_breakdown:
            assert buy.cost_breakdown.get("stamp_duty", 0) == 0

    def test_c3_05_sell_has_stamp_duty(self, case3_result):
        sell = [t for t in case3_result.trades if t.side == "SELL"][0]
        cb = sell.cost_breakdown
        assert cb is not None
        assert cb.get("stamp_duty", 0) > 0

    def test_c3_07_t1_not_rejected(self, case3_result):
        assert case3_result.diagnostics.t1_rejected_sells == 0

    def test_c3_08_lot_not_adjusted(self, case3_result):
        assert case3_result.diagnostics.lot_adjusted_trades == 0


# ---------------------------------------------------------------------------
# CASE-4: HK market
# ---------------------------------------------------------------------------

CASE4_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"HK": {"type": "hk_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}

CASE4_BARS = [
    (datetime(2024, 6, 3), 300.00, 305.00, 3_000_000),
    (datetime(2024, 6, 4), 310.00, 315.00, 4_000_000),
    (datetime(2024, 6, 5), 312.00, 308.00, 2_500_000),
    (datetime(2024, 6, 6), 315.00, 320.00, 3_500_000),
    (datetime(2024, 6, 7), 325.00, 330.00, 5_000_000),
]


@pytest.fixture
def case4_result():
    data = _make_bars("00700", CASE4_BARS)
    bt = make_backtester(CASE4_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case4", "00700", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["00700"],
    )


class TestCase4HKMarket:
    def test_c4_01_equity_curve_length(self, case4_result):
        assert len(case4_result.equity_curve) >= 5

    def test_c4_03_final_nav_equals_initial_plus_pnl(self, case4_result):
        pnl_sum = sum(t.pnl for t in case4_result.trades)
        assert case4_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c4_04_buy_has_stamp(self, case4_result):
        buy = [t for t in case4_result.trades if t.side == "BUY"][0]
        assert buy.pnl < 0
        if buy.cost_breakdown:
            assert buy.cost_breakdown.get("stamp_duty", 0) > 0

    def test_c4_05_sell_has_stamp_and_system_fee(self, case4_result):
        sell = [t for t in case4_result.trades if t.side == "SELL"][0]
        cb = sell.cost_breakdown
        assert cb is not None
        assert cb.get("stamp_duty", 0) > 0
        assert cb.get("system_fee", 0) == pytest.approx(0.50, abs=0.01)

    def test_c4_06_sell_cost_breakdown_keys(self, case4_result):
        sell = [t for t in case4_result.trades if t.side == "SELL"][0]
        cb = sell.cost_breakdown
        assert cb is not None
        expected_keys = {"commission", "stamp_duty", "sfc_levy", "clearing", "trading_fee", "system_fee"}
        assert expected_keys.issubset(set(cb.keys()))

    def test_c4_07_all_costs_non_negative(self, case4_result):
        sell = [t for t in case4_result.trades if t.side == "SELL"][0]
        assert all(v >= 0 for v in sell.cost_breakdown.values())

    def test_c4_08_t1_not_rejected(self, case4_result):
        assert case4_result.diagnostics.t1_rejected_sells == 0


# ---------------------------------------------------------------------------
# CASE-5: SubPortfolio shared position
# ---------------------------------------------------------------------------

CASE5_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case5_result():
    data = _make_bars("AAPL", CASE1_BARS)
    bt = make_backtester(CASE5_CONFIG)
    provider = DataFrameProvider(data)

    class StratA:
        name = "StratA"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("AAPL", 50, "BUY", "MARKET", None, "StratA")
            if self._day == 3:
                om.submit_order("AAPL", 30, "SELL", "MARKET", None, "StratA")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    class StratB:
        name = "StratB"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("AAPL", 50, "BUY", "MARKET", None, "StratB")
            if self._day == 3:
                om.submit_order("AAPL", 20, "SELL", "MARKET", None, "StratB")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[StratA(), StratB()],
        initial_cash=100_000,
        data_provider=provider,
        symbols=["AAPL"],
        strategy_allocations={"StratA": 0.4, "StratB": 0.6},
    )


class TestCase5SubPortfolio:
    def test_c5_01_equity_curve(self, case5_result):
        expected = [100000, 100150, 100300, 100350, 100425]
        for i, v in enumerate(expected):
            assert case5_result.equity_curve.iloc[i] == pytest.approx(v, rel=5e-3)

    def test_c5_03_total_qty_per_symbol(self, case5_result):
        open_pos = case5_result.open_positions
        aapl_qty = sum(p["quantity"] for p in open_pos if p["symbol"] == "AAPL")
        assert aapl_qty == 50

    def test_c5_04_allocation_within_cash(self, case5_result):
        assert case5_result.final_nav > 0

    def test_c5_06_open_positions_count(self, case5_result):
        open_pos = case5_result.open_positions
        aapl = [p for p in open_pos if p["symbol"] == "AAPL"]
        total_qty = sum(p["quantity"] for p in aapl)
        assert total_qty == 50

    def test_c5_07_final_nav_equals_initial_plus_pnl(self, case5_result):
        pnl_sum = sum(t.pnl for t in case5_result.trades)
        assert case5_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)


# ---------------------------------------------------------------------------
# CASE-6: Multi-lot FIFO sell
# ---------------------------------------------------------------------------

CASE6_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 105.00, 108.00, 1_200_000),
    (datetime(2024, 6, 5), 110.00, 112.00, 1_500_000),
    (datetime(2024, 6, 6), 115.00, 118.00, 1_300_000),
    (datetime(2024, 6, 7), 120.00, 122.00, 2_000_000),
]


@pytest.fixture
def case6_result():
    data = _make_bars("AAPL", CASE6_BARS)
    config = {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
        "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
    }
    bt = make_backtester(config)
    provider = DataFrameProvider(data)

    class FIFOStrat:
        name = "FIFO"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("AAPL", 40, "BUY", "MARKET", None, "FIFO")
            elif self._day == 1:
                om.submit_order("AAPL", 60, "BUY", "MARKET", None, "FIFO")
            elif self._day == 3:
                om.submit_order("AAPL", 100, "SELL", "MARKET", None, "FIFO")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[FIFOStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase6FIFO:
    def test_c6_01_sell_produces_multiple_trades(self, case6_result):
        sells = [t for t in case6_result.trades if t.side == "SELL"]
        assert len(sells) == 2

    def test_c6_02_first_lot(self, case6_result):
        sells = sorted(
            [t for t in case6_result.trades if t.side == "SELL"],
            key=lambda t: t.quantity,
        )
        t40 = sells[0]
        assert t40.quantity == pytest.approx(40, rel=1e-4)
        assert t40.entry_price == pytest.approx(105.0, rel=1e-4)
        assert t40.realized_pnl == pytest.approx(600.0, rel=1e-2)

    def test_c6_03_second_lot(self, case6_result):
        sells = sorted(
            [t for t in case6_result.trades if t.side == "SELL"],
            key=lambda t: t.quantity,
        )
        t60 = sells[1]
        assert t60.quantity == pytest.approx(60, rel=1e-4)
        assert t60.entry_price == pytest.approx(110.0, rel=1e-4)
        assert t60.realized_pnl == pytest.approx(600.0, rel=1e-2)

    def test_c6_05_total_realized_pnl(self, case6_result):
        sells = [t for t in case6_result.trades if t.side == "SELL"]
        total = sum(t.realized_pnl for t in sells)
        assert total == pytest.approx(1200.0, rel=1e-2)

    def test_c6_08_final_nav(self, case6_result):
        assert case6_result.final_nav == pytest.approx(101200.0, rel=1e-2)

    def test_c6_09_fill_count(self, case6_result):
        assert case6_result.diagnostics.fill_count == 3


# ---------------------------------------------------------------------------
# CASE-7A: US dividend (no tax)
# ---------------------------------------------------------------------------

CASE7A_BARS = [
    (datetime(2024, 6, 3), 180.00, 182.00, 5_000_000),
    (datetime(2024, 6, 4), 182.00, 184.00, 6_000_000),
    (datetime(2024, 6, 5), 183.00, 183.50, 4_000_000),
    (datetime(2024, 6, 6), 183.00, 185.00, 5_000_000),
    (datetime(2024, 6, 7), 186.00, 187.00, 7_000_000),
]


@pytest.fixture
def case7a_result():
    data = _make_bars("AAPL", CASE7A_BARS)
    divs = _make_dividends("AAPL", [datetime(2024, 6, 5)], [1.0])
    config = {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
        "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
    }
    bt = make_backtester(config)
    provider = DataFrameProvider(data, dividends=divs)
    strat = _signal_strategy("Case7A", "AAPL", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase7AUSDividend:
    def test_c7_01_equity_curve(self, case7a_result):
        expected = [100000, 100200, 100250, 100400, 100600]
        for i, v in enumerate(expected):
            assert case7a_result.equity_curve.iloc[i] == pytest.approx(v, rel=5e-3)

    def test_c7_02_realized_pnl_adjusted_basis(self, case7a_result):
        sell = [t for t in case7a_result.trades if t.side == "SELL"][0]
        assert sell.realized_pnl == pytest.approx(500.0, rel=1e-2)

    def test_c7_03_final_nav(self, case7a_result):
        assert case7a_result.final_nav == pytest.approx(100600.0, rel=5e-3)


# ---------------------------------------------------------------------------
# CASE-7B: CN dividend with tax
# ---------------------------------------------------------------------------

CASE7B_BARS = [
    (datetime(2024, 6, 3), 50.00, 51.00, 10_000_000),
    (datetime(2024, 6, 4), 52.00, 53.00, 12_000_000),
    (datetime(2024, 6, 5), 53.00, 52.80, 8_000_000),
    (datetime(2024, 6, 6), 53.00, 54.00, 10_000_000),
    (datetime(2024, 6, 7), 55.00, 56.00, 15_000_000),
]


@pytest.fixture
def case7b_result():
    data = _make_bars("600519", CASE7B_BARS)
    divs = _make_dividends("600519", [datetime(2024, 6, 5)], [0.50])
    config = {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
        "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
    }
    bt = make_backtester(config)
    provider = DataFrameProvider(data, dividends=divs)
    strat = _signal_strategy("Case7B", "600519", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["600519"],
    )


class TestCase7BCNDividendTax:
    def test_c7b_01_net_dividend_after_tax(self, case7b_result):
        ec = case7b_result.equity_curve
        d1_nav = ec.iloc[1]
        d2_nav = ec.iloc[2]
        dividend_net = d2_nav - d1_nav
        assert dividend_net > 0

    def test_c7b_02_realized_pnl_adjusted_basis(self, case7b_result):
        sell = [t for t in case7b_result.trades if t.side == "SELL"][0]
        assert sell.realized_pnl == pytest.approx(350.0, rel=5e-2)

    def test_c7b_03_total_reward(self, case7b_result):
        ec = case7b_result.equity_curve
        total_reward = ec.iloc[-1] - ec.iloc[0]
        assert total_reward == pytest.approx(326.929, rel=5e-2)


# ---------------------------------------------------------------------------
# CASE-8: Comprehensive integration
# ---------------------------------------------------------------------------

CASE8_US_BARS = [
    (datetime(2024, 6, 3), 180.00, 182.00, 5_000_000),
    (datetime(2024, 6, 4), 182.00, 184.00, 6_000_000),
    (datetime(2024, 6, 5), 183.00, 183.50, 4_000_000),
    (datetime(2024, 6, 6), 183.00, 185.00, 5_000_000),
    (datetime(2024, 6, 7), 186.00, 187.00, 7_000_000),
    (datetime(2024, 6, 10), 188.00, 189.00, 6_000_000),
    (datetime(2024, 6, 11), 190.00, 191.00, 8_000_000),
]

CASE8_CN_BARS = [
    (datetime(2024, 6, 3), 50.00, 51.00, 10_000_000),
    (datetime(2024, 6, 4), 52.00, 53.00, 12_000_000),
    (datetime(2024, 6, 5), 53.00, 52.00, 8_000_000),
    (datetime(2024, 6, 6), 52.50, 54.00, 10_000_000),
    (datetime(2024, 6, 7), 55.00, 56.00, 15_000_000),
    (datetime(2024, 6, 10), 56.00, 57.00, 12_000_000),
    (datetime(2024, 6, 11), 57.00, 58.00, 14_000_000),
]


@pytest.fixture
def case8_result():
    us_data = _make_bars("AAPL", CASE8_US_BARS)
    cn_data = _make_bars("600519", CASE8_CN_BARS)
    data = pd.concat([us_data, cn_data], ignore_index=True)
    divs = _make_dividends("AAPL", [datetime(2024, 6, 5)], [1.0])
    config = {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {
            "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
            "CN": {"type": "cn_realistic"},
        }},
        "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
    }
    bt = make_backtester(config)
    provider = DataFrameProvider(data, dividends=divs)

    class StratA:
        name = "StratA"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("AAPL", 100, "BUY", "MARKET", None, "StratA")
            elif self._day == 3:
                om.submit_order("AAPL", 40, "SELL", "MARKET", None, "StratA")
            elif self._day == 5:
                om.submit_order("AAPL", 60, "SELL", "MARKET", None, "StratA")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    class StratB:
        name = "StratB"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("600519", 100, "BUY", "MARKET", None, "StratB")
            elif self._day == 4:
                om.submit_order("AAPL", 50, "BUY", "MARKET", None, "StratB")
            elif self._day == 5:
                om.submit_order("600519", 100, "SELL", "MARKET", None, "StratB")
                om.submit_order("AAPL", 50, "SELL", "MARKET", None, "StratB")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    with pytest.raises(ValueError, match="Mixed currencies") as exc:
        bt.run(
            start=data["timestamp"].min(), end=data["timestamp"].max(),
            strategies=[StratA(), StratB()],
            initial_cash=100_000,
            data_provider=provider,
            symbols=["AAPL", "600519"],
            strategy_allocations={"StratA": 0.5, "StratB": 0.5},
        )
    return exc.value


class TestCase8Comprehensive:
    def test_c8_01_mixed_currency_comprehensive_case_rejected(self, case8_result):
        assert "Mixed currencies" in str(case8_result)


# ---------------------------------------------------------------------------
# CASE-9: Position realized_pnl vs Trade realized_pnl (multi-price partial sell)
# ---------------------------------------------------------------------------

class TestCase9PositionRealizedPnlConsistency:
    def test_c9_01_multi_price_partial_sell(self):
        from quant.features.trading.portfolio import Portfolio
        from quant.features.backtest.entities import BacktestDiagnostics, CommissionConfig
        from quant.features.backtest.order_executor import execute_order
        from quant.features.backtest.schemas import DeferredOrder

        pf = Portfolio(initial_cash=100_000)
        diag = BacktestDiagnostics()
        config = CommissionConfig(
            US={"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}
        )

        buy1 = DeferredOrder(
            symbol="AAPL", quantity=40, side="BUY",
            order_type="MARKET", price=100.0, strategy="test",
            signal_date=datetime(2024, 6, 3),
        )
        bar1 = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 4),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 101.0, "volume": 1_000_000,
        }
        entry_times = {}
        entry_prices = {}

        execute_order(
            order=buy1, portfolio=pf, symbol="AAPL", bar=bar1,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        buy2 = DeferredOrder(
            symbol="AAPL", quantity=60, side="BUY",
            order_type="MARKET", price=110.0, strategy="test",
            signal_date=datetime(2024, 6, 4),
        )
        bar2 = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 5),
            "open": 110.0, "high": 111.0, "low": 109.0,
            "close": 111.0, "volume": 1_000_000,
        }

        execute_order(
            order=buy2, portfolio=pf, symbol="AAPL", bar=bar2,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        sell_order = DeferredOrder(
            symbol="AAPL", quantity=50, side="SELL",
            order_type="MARKET", price=130.0, strategy="test",
            signal_date=datetime(2024, 6, 5),
        )
        sell_bar = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 6),
            "open": 130.0, "high": 131.0, "low": 129.0,
            "close": 131.0, "volume": 1_000_000,
        }

        sell_trades = execute_order(
            order=sell_order, portfolio=pf, symbol="AAPL", bar=sell_bar,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        trade_realized_sum = sum(t.realized_pnl for t in sell_trades)
        pos = pf.get_position("AAPL")

        assert len(sell_trades) == 2
        assert pos.quantity == pytest.approx(50)
        assert trade_realized_sum == pytest.approx(1400.0, rel=1e-2)
        assert pos.realized_pnl == pytest.approx(trade_realized_sum, rel=1e-6)

    def test_c9_02_full_close_consistency(self):
        from quant.features.trading.portfolio import Portfolio
        from quant.features.backtest.entities import BacktestDiagnostics, CommissionConfig
        from quant.features.backtest.order_executor import execute_order
        from quant.features.backtest.schemas import DeferredOrder

        pf = Portfolio(initial_cash=100_000)
        diag = BacktestDiagnostics()
        config = CommissionConfig(
            US={"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}
        )

        buy1 = DeferredOrder(
            symbol="AAPL", quantity=40, side="BUY",
            order_type="MARKET", price=100.0, strategy="test",
            signal_date=datetime(2024, 6, 3),
        )
        bar1 = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 4),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 101.0, "volume": 1_000_000,
        }
        entry_times = {}
        entry_prices = {}

        execute_order(
            order=buy1, portfolio=pf, symbol="AAPL", bar=bar1,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        buy2 = DeferredOrder(
            symbol="AAPL", quantity=60, side="BUY",
            order_type="MARKET", price=110.0, strategy="test",
            signal_date=datetime(2024, 6, 4),
        )
        bar2 = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 5),
            "open": 110.0, "high": 111.0, "low": 109.0,
            "close": 111.0, "volume": 1_000_000,
        }

        execute_order(
            order=buy2, portfolio=pf, symbol="AAPL", bar=bar2,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        sell_order = DeferredOrder(
            symbol="AAPL", quantity=100, side="SELL",
            order_type="MARKET", price=130.0, strategy="test",
            signal_date=datetime(2024, 6, 5),
        )
        sell_bar = {
            "symbol": "AAPL", "timestamp": datetime(2024, 6, 6),
            "open": 130.0, "high": 131.0, "low": 129.0,
            "close": 131.0, "volume": 1_000_000,
        }

        sell_trades = execute_order(
            order=sell_order, portfolio=pf, symbol="AAPL", bar=sell_bar,
            entry_times=entry_times, entry_prices=entry_prices, diag=diag,
            lot_sizes={}, ipo_dates={}, slippage_bps=0, commission_config=config,
        )

        trade_realized_sum = sum(t.realized_pnl for t in sell_trades)
        pos = pf.get_position("AAPL")

        assert pos.quantity == pytest.approx(0, abs=1e-6)
        assert pos.realized_pnl == pytest.approx(trade_realized_sum, rel=1e-6)
        assert trade_realized_sum == pytest.approx(
            (130.0 - 100.0) * 40 + (130.0 - 110.0) * 60, rel=1e-2
        )


# ---------------------------------------------------------------------------
# CASE-10: SubPortfolio capital isolation
# ---------------------------------------------------------------------------

CASE10_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}

CASE10_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 2_000_000),
    (datetime(2024, 6, 4), 105.00, 106.00, 2_500_000),
    (datetime(2024, 6, 5), 110.00, 111.00, 3_000_000),
    (datetime(2024, 6, 6), 115.00, 116.00, 2_800_000),
    (datetime(2024, 6, 7), 120.00, 121.00, 3_200_000),
]


@pytest.fixture
def case10_result():
    data = _make_bars("AAPL", CASE10_BARS)
    bt = make_backtester(CASE10_CONFIG)
    provider = DataFrameProvider(data)

    class StratA:
        name = "StratA"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 0:
                om.submit_order("AAPL", 400, "BUY", "MARKET", None, "StratA")
            elif self._day == 3:
                om.submit_order("AAPL", 400, "SELL", "MARKET", None, "StratA")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    class StratB:
        name = "StratB"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            om = ctx.order_manager
            if self._day == 1:
                om.submit_order("AAPL", 100, "BUY", "MARKET", None, "StratB")
            elif self._day == 3:
                om.submit_order("AAPL", 100, "SELL", "MARKET", None, "StratB")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[StratA(), StratB()],
        initial_cash=100_000,
        data_provider=provider,
        symbols=["AAPL"],
        strategy_allocations={"StratA": 0.6, "StratB": 0.4},
    )


class TestCase10CapitalIsolation:
    def test_c10_01_total_nav_invariant(self, case10_result):
        pnl_sum = sum(t.pnl for t in case10_result.trades)
        assert case10_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c10_02_final_nav_matches_initial_plus_profit(self, case10_result):
        # StratA: buy 400 @ ~102, sell 400 @ ~116 → profit ~5600
        # StratB: buy 200 @ ~106, hold → market value 200*121=24200
        # Both use zero-slippage US comm, expected NAV > 100K
        assert case10_result.final_nav > 100_000

    def test_c10_03_strata_fully_liquidated(self, case10_result):
        aapl_pos = [p for p in case10_result.open_positions if p.get("strategy") == "StratA"]
        assert len(aapl_pos) == 0

    def test_c10_04_fills_occurred(self, case10_result):
        assert case10_result.diagnostics.fill_count >= 3


# ============================================================================
# CASE-11: CN price limit rejection (涨停/跌停)
# ============================================================================

CASE11_BARS = [
    (datetime(2024, 6, 3), 10.00, 10.00, 10_000_000),
    (datetime(2024, 6, 4), 11.00, 10.50, 10_000_000),
    (datetime(2024, 6, 5), 10.00, 10.00, 10_000_000),
]

CASE11_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case11_result():
    data = _make_bars("600519", CASE11_BARS)
    bt = make_backtester(CASE11_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case11", "600519", buy_on={0}, sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["600519"],
    )


class TestCase11CNPriceLimit:
    def test_c11_01_limit_rejected(self, case11_result):
        assert case11_result.diagnostics.limit_rejected_orders == 1

    def test_c11_02_no_fills(self, case11_result):
        assert case11_result.diagnostics.fill_count == 0

    def test_c11_03_nav_unchanged(self, case11_result):
        for v in case11_result.equity_curve:
            assert v == pytest.approx(100_000, rel=1e-6)

    def test_c11_04_discarded(self, case11_result):
        assert case11_result.diagnostics.discarded_orders >= 1


# ============================================================================
# CASE-12: Suspension day (停牌日)
# ============================================================================

CASE12_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 102.00, 102.00, 0),           # volume=0 → suspended
    (datetime(2024, 6, 5), 105.00, 106.00, 1_000_000),
    (datetime(2024, 6, 6), 107.00, 108.00, 1_000_000),
]

CASE12_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case12_result():
    data = _make_bars("AAPL", CASE12_BARS)
    bt = make_backtester(CASE12_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case12", "AAPL", buy_on={0, 2}, sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase12Suspension:
    def test_c12_01_suspended_days(self, case12_result):
        assert case12_result.diagnostics.suspended_days >= 1

    def test_c12_02_discarded_orders(self, case12_result):
        assert case12_result.diagnostics.discarded_orders >= 1

    def test_c12_03_nav_unchanged_on_suspension(self, case12_result):
        ec = case12_result.equity_curve
        assert ec.iloc[0] == pytest.approx(ec.iloc[1], rel=1e-4)

    def test_c12_04_only_post_suspension_fill(self, case12_result):
        assert case12_result.diagnostics.fill_count == 1

    def test_c12_05_final_nav_positive(self, case12_result):
        assert case12_result.equity_curve.iloc[-1] > case12_result.equity_curve.iloc[0]


# ============================================================================
# CASE-13: Risk rejection (仓位上限风控)
# ============================================================================

CASE13_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 105.00, 106.00, 1_000_000),
]

CASE13_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 0.05, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case13_result():
    data = _make_bars("AAPL", CASE13_BARS)
    bt = make_backtester(CASE13_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case13", "AAPL", buy_on={0}, sell_on=set(), qty=200)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase13RiskRejection:
    def test_c13_01_risk_skipped(self, case13_result):
        assert case13_result.diagnostics.risk_skipped_orders >= 1

    def test_c13_02_no_fills(self, case13_result):
        assert case13_result.diagnostics.fill_count == 0

    def test_c13_03_nav_unchanged(self, case13_result):
        assert case13_result.final_nav == pytest.approx(100_000, rel=1e-4)


# ============================================================================
# CASE-14: on_stop close-out
# ============================================================================

CASE14_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 105.00, 108.00, 1_000_000),
    (datetime(2024, 6, 5), 110.00, 112.00, 1_000_000),
]

CASE14_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case14_result():
    data = _make_bars("AAPL", CASE14_BARS)
    bt = make_backtester(CASE14_CONFIG)
    provider = DataFrameProvider(data)

    class OnStopStrat:
        name = "OnStop"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", None, "OnStop")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            ctx.order_manager.submit_order("AAPL", 100, "SELL", "MARKET", None, "OnStop")

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[OnStopStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase14OnStopCloseOut:
    def test_c14_01_two_fills(self, case14_result):
        assert case14_result.diagnostics.fill_count == 2

    def test_c14_02_all_positions_closed(self, case14_result):
        assert len(case14_result.open_positions) == 0

    def test_c14_03_final_nav_invariant(self, case14_result):
        pnl_sum = sum(t.pnl for t in case14_result.trades)
        assert case14_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c14_04_profitable(self, case14_result):
        assert case14_result.final_nav > 100_000


# ============================================================================
# CASE-15: Stock dividend (送股)
# ============================================================================

CASE15_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 105.00, 108.00, 1_000_000),
    (datetime(2024, 6, 5), 106.00, 106.00, 1_000_000),
    (datetime(2024, 6, 6), 110.00, 112.00, 1_000_000),
]

CASE15_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case15_result():
    data = _make_bars("AAPL", CASE15_BARS)
    dividends = pd.DataFrame({
        "symbol": ["AAPL"],
        "ex_date": [datetime(2024, 6, 5)],
        "cash_dividend": [0.0],
        "stock_dividend": [0.5],
    })
    bt = make_backtester(CASE15_CONFIG)
    provider = DataFrameProvider(data, dividends=dividends)

    class StockDivStrat:
        name = "StockDiv"
        context = None
        _positions = {}
        _position_history = []
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", None, "StockDiv")
            elif self._day == 2:
                ctx.order_manager.submit_order("AAPL", 150, "SELL", "MARKET", None, "StockDiv")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q
            self._position_history.append(self._positions.get(fill.symbol, 0))

        def get_position(self, symbol):
            return self._positions.get(symbol, 0)

        def on_stop(self, ctx):
            pass

    strat = StockDivStrat()
    result = bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )
    result._strategy_positions = strat._positions.copy()
    result._strategy_position_history = list(strat._position_history)
    return result


class TestCase15StockDividend:
    def test_c15_01_two_fills(self, case15_result):
        assert case15_result.diagnostics.fill_count == 2

    def test_c15_02_sell_trade_quantity(self, case15_result):
        sells = [t for t in case15_result.trades if t.side == "SELL"]
        assert len(sells) == 1
        assert sells[0].quantity == pytest.approx(150, rel=1e-4)

    def test_c15_03_adjusted_entry_price(self, case15_result):
        sells = [t for t in case15_result.trades if t.side == "SELL"]
        assert sells[0].entry_price == pytest.approx(70.00, rel=5e-2)

    def test_c15_04_strategy_positions_synced(self, case15_result):
        assert 150 in case15_result._strategy_position_history

    def test_c15_05_final_nav_profitable(self, case15_result):
        assert case15_result.final_nav > 100_000


# ============================================================================
# CASE-16: CN T+1 same-day BUY+SELL rejection
# ============================================================================

CASE16_BARS = [
    (datetime(2024, 6, 3), 50.00, 51.00, 10_000_000),
    (datetime(2024, 6, 4), 52.00, 53.00, 12_000_000),
    (datetime(2024, 6, 5), 53.00, 54.00, 10_000_000),
]

CASE16_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case16_result():
    data = _make_bars("600519", CASE16_BARS)
    bt = make_backtester(CASE16_CONFIG)
    provider = DataFrameProvider(data)

    class T1SameDayStrat:
        name = "T1SameDay"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("600519", 100, "BUY", "MARKET", None, "T1SameDay")
                ctx.order_manager.submit_order("600519", 100, "SELL", "MARKET", None, "T1SameDay")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def get_position(self, symbol):
            return self._positions.get(symbol, 0)

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[T1SameDayStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["600519"],
    )


class TestCase16CNT1SameDayRejection:
    def test_c16_01_sell_blocked_by_risk(self, case16_result):
        assert case16_result.diagnostics.risk_skipped_orders >= 1

    def test_c16_02_only_buy_filled(self, case16_result):
        assert case16_result.diagnostics.fill_count == 1
        assert len(case16_result.trades) == 1
        assert case16_result.trades[0].side == "BUY"

    def test_c16_03_open_position_remains(self, case16_result):
        cn_pos = [p for p in case16_result.open_positions if p["symbol"] == "600519"]
        assert len(cn_pos) == 1
        assert cn_pos[0]["quantity"] == pytest.approx(100)

    def test_c16_04_nav_increased(self, case16_result):
        assert case16_result.equity_curve.iloc[-1] > case16_result.equity_curve.iloc[0]


# ============================================================================
# CASE-17: Volume participation limit
# ============================================================================

CASE17_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1000),
    (datetime(2024, 6, 4), 105.00, 106.00, 1000),
]

CASE17_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case17_result():
    data = _make_bars("AAPL", CASE17_BARS)
    bt = make_backtester(CASE17_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case17", "AAPL", buy_on={0}, sell_on=set(), qty=500)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase17VolumeLimit:
    def test_c17_01_volume_limited(self, case17_result):
        assert case17_result.diagnostics.volume_limited_trades >= 1

    def test_c17_02_quantity_capped(self, case17_result):
        t = case17_result.trades[0]
        assert t.quantity == pytest.approx(50, rel=1e-4)

    def test_c17_03_intended_qty_preserved(self, case17_result):
        assert case17_result.trades[0].intended_qty == 500


# ============================================================================
# CASE-18: Price deviation rejection
# ============================================================================

CASE18_BARS = [
    (datetime(2024, 6, 3), 100.00, 100.00, 1_000_000),
    (datetime(2024, 6, 4), 120.00, 120.00, 1_000_000),
]

CASE18_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case18_result():
    data = _make_bars("AAPL", CASE18_BARS)
    bt = make_backtester(CASE18_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case18", "AAPL", buy_on={0}, sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase18PriceDeviation:
    def test_c18_01_deviation_rejected(self, case18_result):
        assert case18_result.diagnostics.rejection_counts.get("price_deviation", 0) >= 1

    def test_c18_02_no_fills(self, case18_result):
        assert case18_result.diagnostics.fill_count == 0

    def test_c18_03_nav_unchanged(self, case18_result):
        assert case18_result.final_nav == pytest.approx(100_000, rel=1e-4)


# ============================================================================
# CASE-19: No-trade backtest (空回测)
# ============================================================================

CASE19_BARS = CASE1_BARS
CASE19_CONFIG = CASE1_CONFIG


@pytest.fixture
def case19_result():
    data = _make_bars("AAPL", CASE19_BARS)
    bt = make_backtester(CASE19_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case19", "AAPL", buy_on=set(), sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase19NoTrade:
    def test_c19_01_flat_equity(self, case19_result):
        for v in case19_result.equity_curve:
            assert v == pytest.approx(100_000, rel=1e-6)

    def test_c19_02_no_fills(self, case19_result):
        assert case19_result.diagnostics.fill_count == 0

    def test_c19_03_no_trades(self, case19_result):
        assert len(case19_result.trades) == 0

    def test_c19_04_final_nav(self, case19_result):
        assert case19_result.final_nav == pytest.approx(100_000, rel=1e-4)

    def test_c19_05_zero_return(self, case19_result):
        assert case19_result.total_return == 0.0

    def test_c19_06_zero_drawdown(self, case19_result):
        assert case19_result.max_drawdown == 0.0


# ============================================================================
# CASE-20: CN odd-lot sell (碎股卖出通过)
# ============================================================================

CASE20_BARS = [
    (datetime(2024, 6, 3), 50.00, 51.00, 10_000_000),
    (datetime(2024, 6, 4), 52.00, 53.00, 10_000_000),
    (datetime(2024, 6, 5), 53.00, 53.00, 10_000_000),
    (datetime(2024, 6, 6), 54.00, 55.00, 10_000_000),
    (datetime(2024, 6, 7), 56.00, 57.00, 10_000_000),
]

CASE20_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case20_result():
    data = _make_bars("600519", CASE20_BARS)
    dividends = pd.DataFrame({
        "symbol": ["600519"],
        "ex_date": [datetime(2024, 6, 5)],
        "cash_dividend": [0.0],
        "stock_dividend": [0.5],
    })
    bt = make_backtester(CASE20_CONFIG)
    provider = DataFrameProvider(data, dividends=dividends)

    class CNOddLotStrat:
        name = "CNOddLot"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("600519", 100, "BUY", "MARKET", None, "CNOddLot")
            elif self._day == 2:
                ctx.order_manager.submit_order("600519", 50, "SELL", "MARKET", None, "CNOddLot")
            elif self._day == 3:
                ctx.order_manager.submit_order("600519", 100, "SELL", "MARKET", None, "CNOddLot")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def get_position(self, symbol):
            return self._positions.get(symbol, 0)

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[CNOddLotStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["600519"],
    )


class TestCase20CNOddLotSell:
    def test_c20_01_three_fills(self, case20_result):
        assert case20_result.diagnostics.fill_count == 3

    def test_c20_02_odd_lot_passes(self, case20_result):
        sells = [t for t in case20_result.trades if t.side == "SELL"]
        quantities = sorted([s.quantity for s in sells])
        assert quantities[0] == pytest.approx(50, rel=1e-4)

    def test_c20_03_normal_lot_sells(self, case20_result):
        sells = [t for t in case20_result.trades if t.side == "SELL"]
        quantities = sorted([s.quantity for s in sells])
        assert quantities[1] == pytest.approx(100, rel=1e-4)

    def test_c20_04_all_positions_closed(self, case20_result):
        assert len(case20_result.open_positions) == 0


# ============================================================================
# CASE-21: HK odd-lot sell rejection (碎股卖出拒绝)
# ============================================================================

CASE21_BARS = [
    (datetime(2024, 6, 3), 300.00, 305.00, 1_000_000),
    (datetime(2024, 6, 4), 310.00, 315.00, 1_000_000),
    (datetime(2024, 6, 5), 312.00, 312.00, 1_000_000),
    (datetime(2024, 6, 6), 315.00, 320.00, 1_000_000),
]

CASE21_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"HK": {"type": "hk_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case21_result():
    data = _make_bars("00700", CASE21_BARS)
    bt = make_backtester(CASE21_CONFIG)
    provider = DataFrameProvider(data)

    class HKOddLotStrat:
        name = "HKOddLot"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("00700", 100, "BUY", "MARKET", None, "HKOddLot")
            elif self._day == 2:
                ctx.order_manager.submit_order("00700", 50, "SELL", "MARKET", None, "HKOddLot")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[HKOddLotStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["00700"],
    )


class TestCase21HKOddLotRejection:
    def test_c21_01_lot_impossible_rejected(self, case21_result):
        assert case21_result.diagnostics.rejection_counts.get("lot_impossible", 0) >= 1

    def test_c21_02_only_buy_filled(self, case21_result):
        assert case21_result.diagnostics.fill_count == 1

    def test_c21_03_position_remains(self, case21_result):
        hk_pos = [p for p in case21_result.open_positions if "00700" in p["symbol"]]
        assert len(hk_pos) == 1
        assert hk_pos[0]["quantity"] == pytest.approx(100, rel=1e-4)


# ============================================================================
# CASE-27: BUY dedup rejection (DUPLICATE_BUY)
# ============================================================================

CASE27_BARS = CASE1_BARS
CASE27_CONFIG = CASE1_CONFIG


@pytest.fixture
def case27_result():
    data = _make_bars("AAPL", CASE27_BARS)
    bt = make_backtester(CASE27_CONFIG)
    provider = DataFrameProvider(data)

    class DupStrat:
        name = "DupTest"
        context = None
        _positions = {}
        _day = -1

        def on_start(self, ctx):
            self.context = ctx

        def on_before_trading(self, ctx, td):
            pass

        def on_data(self, ctx, data):
            pass

        def on_after_trading(self, ctx, td):
            self._day += 1
            if self._day == 0:
                ctx.order_manager.submit_order("AAPL", 100, "BUY", "MARKET", None, "DupTest")
                ctx.order_manager.submit_order("AAPL", 50, "BUY", "MARKET", None, "DupTest")

        def on_fill(self, ctx, fill):
            q = fill.quantity if fill.side == "BUY" else -fill.quantity
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + q

        def on_stop(self, ctx):
            pass

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[DupStrat()], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase27BUYDedup:
    def test_c27_01_only_one_buy_filled(self, case27_result):
        assert case27_result.diagnostics.fill_count == 1

    def test_c27_02_buy_quantity_correct(self, case27_result):
        assert case27_result.trades[0].quantity == pytest.approx(100, rel=1e-4)
        assert case27_result.trades[0].intended_qty == 100

    def test_c27_03_not_150_shares(self, case27_result):
        assert case27_result.trades[0].quantity != 150


# ============================================================================
# CASE-28: Insufficient cash rejection
# ============================================================================

CASE28_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 10_000_000),
    (datetime(2024, 6, 4), 105.00, 106.00, 10_000_000),
]

CASE28_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 3.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def case28_result():
    data = _make_bars("AAPL", CASE28_BARS)
    bt = make_backtester(CASE28_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case28", "AAPL", buy_on={0}, sell_on=set(), qty=2000)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase28InsufficientCash:
    def test_c28_01_cash_rejected(self, case28_result):
        assert case28_result.diagnostics.rejection_counts.get("insufficient_cash", 0) >= 1

    def test_c28_02_no_fills(self, case28_result):
        assert case28_result.diagnostics.fill_count == 0

    def test_c28_03_nav_unchanged(self, case28_result):
        assert case28_result.final_nav == pytest.approx(100_000, rel=1e-4)


# ============================================================================
# CASE-29: Limit order marketability
# ============================================================================


def _execute_direct_order(order, symbol, bar, portfolio, prev_bar=None):
    from quant.features.backtest.entities import BacktestDiagnostics, CommissionConfig
    from quant.features.backtest.order_executor import execute_order

    diag = BacktestDiagnostics()
    trades = execute_order(
        order=order,
        portfolio=portfolio,
        symbol=symbol,
        bar=bar,
        entry_times={},
        entry_prices={},
        diag=diag,
        lot_sizes={},
        ipo_dates={},
        slippage_bps=0,
        commission_config=CommissionConfig(
            US={"type": "percent", "percent": 0.0, "min_per_order": 0.0},
            CN={"type": "cn_realistic"},
            HK={"type": "hk_realistic"},
        ),
        prev_bar=prev_bar,
        risk_price_deviation_limit=1.0,
    )
    return trades, diag


class TestCase29LimitOrders:
    def test_c29_01_buy_limit_above_open_fills_at_open(self):
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000)
        order = DeferredOrder(
            symbol="AAPL", quantity=100, side="BUY", order_type="LIMIT",
            price=120.0, strategy="LimitBuy", signal_date=datetime(2024, 6, 3),
            risk_check_price=120.0,
        )
        trades, _ = _execute_direct_order(
            order, "AAPL",
            {"symbol": "AAPL", "open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
            pf,
        )
        assert trades[0].fill_price == pytest.approx(110.0)

    def test_c29_02_buy_limit_below_open_rejected(self):
        from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000)
        order = DeferredOrder(
            symbol="AAPL", quantity=100, side="BUY", order_type="LIMIT",
            price=100.0, strategy="LimitBuy", signal_date=datetime(2024, 6, 3),
            risk_check_price=100.0,
        )
        with pytest.raises(OrderRejectedError) as exc:
            _execute_direct_order(
                order, "AAPL",
                {"symbol": "AAPL", "open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
                pf,
            )
        assert exc.value.reason == OrderRejectionReason.LIMIT_NOT_MARKETABLE

    def test_c29_03_sell_limit_below_open_fills_at_open(self):
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", quantity=100, price=100.0, cost=10_000.0, trade_date=date(2024, 6, 3))
        pf.cash -= 10_000.0
        order = DeferredOrder(
            symbol="AAPL", quantity=100, side="SELL", order_type="LIMIT",
            price=105.0, strategy="LimitSell", signal_date=datetime(2024, 6, 4),
            risk_check_price=105.0,
        )
        trades, _ = _execute_direct_order(
            order, "AAPL",
            {"symbol": "AAPL", "open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 5)},
            pf,
        )
        assert trades[0].fill_price == pytest.approx(110.0)

    def test_c29_04_sell_limit_above_open_rejected(self):
        from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", quantity=100, price=100.0, cost=10_000.0, trade_date=date(2024, 6, 3))
        pf.cash -= 10_000.0
        order = DeferredOrder(
            symbol="AAPL", quantity=100, side="SELL", order_type="LIMIT",
            price=120.0, strategy="LimitSell", signal_date=datetime(2024, 6, 4),
            risk_check_price=120.0,
        )
        with pytest.raises(OrderRejectedError) as exc:
            _execute_direct_order(
                order, "AAPL",
                {"symbol": "AAPL", "open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 5)},
                pf,
            )
        assert exc.value.reason == OrderRejectionReason.LIMIT_NOT_MARKETABLE


# ============================================================================
# CASE-30: CN price limit side specificity
# ============================================================================


class TestCase30CNPriceLimitSide:
    def test_c30_01_limit_up_rejects_buy(self):
        from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        order = DeferredOrder(
            symbol="600519", quantity=100, side="BUY", order_type="MARKET",
            price=100.0, strategy="LimitUpBuy", signal_date=datetime(2024, 6, 3),
            risk_check_price=100.0,
        )
        with pytest.raises(OrderRejectedError) as exc:
            _execute_direct_order(
                order, "600519",
                {"symbol": "600519", "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
                Portfolio(initial_cash=100_000),
                prev_bar={"close": 100.0},
            )
        assert exc.value.reason == OrderRejectionReason.PRICE_AT_LIMIT

    def test_c30_02_limit_up_allows_sell(self):
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000, currency="CNY")
        pf.update_position("600519", quantity=100, price=100.0, cost=10_000.0, trade_date=date(2024, 6, 1))
        pf.cash -= 10_000.0
        order = DeferredOrder(
            symbol="600519", quantity=100, side="SELL", order_type="MARKET",
            price=100.0, strategy="LimitUpSell", signal_date=datetime(2024, 6, 3),
            risk_check_price=100.0,
        )
        trades, _ = _execute_direct_order(
            order, "600519",
            {"symbol": "600519", "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
            pf,
            prev_bar={"close": 100.0},
        )
        assert trades[0].side == "SELL"

    def test_c30_03_limit_down_allows_buy(self):
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        order = DeferredOrder(
            symbol="600519", quantity=100, side="BUY", order_type="MARKET",
            price=100.0, strategy="LimitDownBuy", signal_date=datetime(2024, 6, 3),
            risk_check_price=100.0,
        )
        trades, _ = _execute_direct_order(
            order, "600519",
            {"symbol": "600519", "open": 90.0, "high": 90.0, "low": 90.0, "close": 90.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
            Portfolio(initial_cash=100_000, currency="CNY"),
            prev_bar={"close": 100.0},
        )
        assert trades[0].side == "BUY"

    def test_c30_04_limit_down_rejects_sell(self):
        from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason
        from quant.features.backtest.schemas import DeferredOrder
        from quant.features.trading.portfolio import Portfolio

        pf = Portfolio(initial_cash=100_000, currency="CNY")
        pf.update_position("600519", quantity=100, price=100.0, cost=10_000.0, trade_date=date(2024, 6, 1))
        pf.cash -= 10_000.0
        order = DeferredOrder(
            symbol="600519", quantity=100, side="SELL", order_type="MARKET",
            price=100.0, strategy="LimitDownSell", signal_date=datetime(2024, 6, 3),
            risk_check_price=100.0,
        )
        with pytest.raises(OrderRejectedError) as exc:
            _execute_direct_order(
                order, "600519",
                {"symbol": "600519", "open": 90.0, "high": 90.0, "low": 90.0, "close": 90.0, "volume": 1_000_000, "timestamp": datetime(2024, 6, 4)},
                pf,
                prev_bar={"close": 100.0},
            )
        assert exc.value.reason == OrderRejectionReason.PRICE_AT_LIMIT


# ============================================================================
# CASE-31: Reject mixed currencies
# ============================================================================


class TestCase31RejectMixedCurrencies:
    def test_c31_01_mixed_us_cn_symbols_rejected(self):
        data = pd.concat([
            _make_bars("AAPL", CASE1_BARS),
            _make_bars("600519", CASE3_BARS),
        ], ignore_index=True)
        bt = make_backtester(CASE1_CONFIG)
        provider = DataFrameProvider(data)
        strat = _signal_strategy("Mixed", "AAPL", buy_on=set(), sell_on=set(), qty=100)
        with pytest.raises(ValueError, match="Mixed currencies"):
            bt.run(
                start=data["timestamp"].min(), end=data["timestamp"].max(),
                strategies=[strat], initial_cash=100_000,
                data_provider=provider, symbols=["AAPL", "600519"],
            )


# ============================================================================
# CASE-32: Suspended bars keep last valid price
# ============================================================================


CASE32_BARS = [
    (datetime(2024, 6, 3), 100.00, 100.00, 1_000_000),
    (datetime(2024, 6, 4), 100.00, 100.00, 1_000_000),
    (datetime(2024, 6, 5), 130.00, 130.00, 0),
    (datetime(2024, 6, 6), 100.00, 100.00, 1_000_000),
]


@pytest.fixture
def case32_result():
    data = _make_bars("AAPL", CASE32_BARS)
    bt = make_backtester(CASE1_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case32", "AAPL", buy_on={0}, sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestCase32SuspendedBarsKeepLastPrice:
    def test_c32_01_suspended_day_does_not_revalue_position(self, case32_result):
        assert case32_result.diagnostics.suspended_days == 1
        assert case32_result.equity_curve.iloc[1] == pytest.approx(100_000)
        assert case32_result.equity_curve.iloc[2] == pytest.approx(100_000)


# ============================================================================
# CASE-33: Multi-strategy default isolation
# ============================================================================


@pytest.fixture
def case33_result():
    data = _make_bars("AAPL", CASE1_BARS)
    bt = make_backtester(CASE1_CONFIG)
    provider = DataFrameProvider(data)
    strat_a = _signal_strategy("IsoA", "AAPL", buy_on={0}, sell_on=set(), qty=100)
    strat_b = _signal_strategy("IsoB", "AAPL", buy_on=set(), sell_on={1}, qty=100)
    result = bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat_a, strat_b], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )
    result._strategy_positions = {
        "IsoA": strat_a._positions.copy(),
        "IsoB": strat_b._positions.copy(),
    }
    return result


class TestCase33MultiStrategyDefaultIsolation:
    def test_c33_01_second_strategy_cannot_sell_first_strategy_position(self, case33_result):
        assert case33_result.diagnostics.rejection_counts.get("no_position", 0) >= 1
        assert case33_result._strategy_positions["IsoA"].get("AAPL", 0) == pytest.approx(100)
        assert case33_result._strategy_positions["IsoB"].get("AAPL", 0) == pytest.approx(0)

    def test_c33_02_open_position_has_strategy_owner(self, case33_result):
        positions = [p for p in case33_result.open_positions if p["symbol"] == "AAPL"]
        assert len(positions) == 1
        assert positions[0]["strategy"] == "IsoA"


# ============================================================================
# Regression: B1 — last trading day deferred orders must expire
# ============================================================================

REGRESSION_B1_BARS = [
    (datetime(2024, 6, 3), 100.00, 102.00, 1_000_000),
    (datetime(2024, 6, 4), 105.00, 108.00, 1_000_000),
    (datetime(2024, 6, 5), 110.00, 112.00, 1_000_000),
]

REGRESSION_B1_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}


@pytest.fixture
def regression_b1_result():
    data = _make_bars("AAPL", REGRESSION_B1_BARS)
    bt = make_backtester(REGRESSION_B1_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("B1Regr", "AAPL", buy_on={2}, sell_on=set(), qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["AAPL"],
    )


class TestRegressionB1FinalDayOrder:
    def test_b1_fill_count_zero(self, regression_b1_result):
        assert regression_b1_result.diagnostics.fill_count == 0
        assert regression_b1_result.diagnostics.expired_orders == 1

    def test_b1_position_does_not_exist(self, regression_b1_result):
        aapl_pos = [p for p in regression_b1_result.open_positions if p["symbol"] == "AAPL"]
        assert len(aapl_pos) == 0

    def test_b1_trade_not_recorded(self, regression_b1_result):
        assert len(regression_b1_result.trades) == 0
