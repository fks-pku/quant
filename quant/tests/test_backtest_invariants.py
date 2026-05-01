"""Invariant-driven tests based on backtest-invariants.md (CASE-1 through CASE-8)."""
from datetime import datetime, date, timedelta

import pandas as pd
import pytest

from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider


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
    bt = Backtester(CASE1_CONFIG)
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
        assert len(ec) == 5
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
    bt = Backtester(CASE2_CONFIG)
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
    bt = Backtester(CASE3_CONFIG)
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
    bt = Backtester(CASE4_CONFIG)
    provider = DataFrameProvider(data)
    strat = _signal_strategy("Case4", "00700", buy_on={0}, sell_on={3}, qty=100)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=100_000,
        data_provider=provider, symbols=["00700"],
    )


class TestCase4HKMarket:
    def test_c4_01_equity_curve_length(self, case4_result):
        assert len(case4_result.equity_curve) == 5

    def test_c4_03_final_nav_equals_initial_plus_pnl(self, case4_result):
        pnl_sum = sum(t.pnl for t in case4_result.trades)
        assert case4_result.final_nav == pytest.approx(100_000 + pnl_sum, rel=1e-2)

    def test_c4_04_buy_no_stamp(self, case4_result):
        buy = [t for t in case4_result.trades if t.side == "BUY"][0]
        assert buy.pnl < 0
        if buy.cost_breakdown:
            assert buy.cost_breakdown.get("stamp_duty", 0) == 0

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
    bt = Backtester(CASE5_CONFIG)
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
    bt = Backtester(config)
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
    bt = Backtester(config)
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
    bt = Backtester(config)
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
    bt = Backtester(config)
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

    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[StratA(), StratB()],
        initial_cash=100_000,
        data_provider=provider,
        symbols=["AAPL", "600519"],
        strategy_allocations={"StratA": 0.5, "StratB": 0.5},
    )


class TestCase8Comprehensive:
    def test_c8_01_equity_curve_final(self, case8_result):
        assert case8_result.equity_curve.iloc[-1] == pytest.approx(101204.929, rel=1e-2)

    def test_c8_02_strata_aapl_fifo(self, case8_result):
        a_trades = [t for t in case8_result.trades if t.strategy_name == "StratA" and t.side == "SELL"]
        total_sold = sum(t.quantity for t in a_trades)
        assert total_sold == pytest.approx(100, rel=1e-4)

    def test_c8_03_stratb_cn_t1_passes(self, case8_result):
        assert case8_result.diagnostics.t1_rejected_sells == 0

    def test_c8_07_final_nav_invariant(self, case8_result):
        pnl_sum = sum(t.pnl for t in case8_result.trades)
        assert case8_result.final_nav == pytest.approx(100_000 + pnl_sum, abs=50)

    def test_c8_08_all_positions_closed(self, case8_result):
        assert len(case8_result.open_positions) == 0

    def test_c8_09_fill_count(self, case8_result):
        assert case8_result.diagnostics.fill_count == 7


# ---------------------------------------------------------------------------
# CASE-9: Position realized_pnl vs Trade realized_pnl (multi-price partial sell)
# ---------------------------------------------------------------------------

class TestCase9PositionRealizedPnlConsistency:
    def test_c9_01_multi_price_partial_sell(self):
        from quant.features.trading.portfolio import Portfolio
        from quant.features.backtest.entities import BacktestDiagnostics, CommissionConfig
        from quant.features.backtest.order_executor import execute_order

        pf = Portfolio(initial_cash=100_000)
        diag = BacktestDiagnostics()
        config = CommissionConfig(
            US={"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}
        )

        buy1 = {
            "symbol": "AAPL", "quantity": 40, "side": "BUY",
            "order_type": "MARKET", "price": 100.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 3),
        }
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

        buy2 = {
            "symbol": "AAPL", "quantity": 60, "side": "BUY",
            "order_type": "MARKET", "price": 110.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 4),
        }
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

        sell_order = {
            "symbol": "AAPL", "quantity": 50, "side": "SELL",
            "order_type": "MARKET", "price": 130.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 5),
        }
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

        pf = Portfolio(initial_cash=100_000)
        diag = BacktestDiagnostics()
        config = CommissionConfig(
            US={"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}
        )

        buy1 = {
            "symbol": "AAPL", "quantity": 40, "side": "BUY",
            "order_type": "MARKET", "price": 100.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 3),
        }
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

        buy2 = {
            "symbol": "AAPL", "quantity": 60, "side": "BUY",
            "order_type": "MARKET", "price": 110.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 4),
        }
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

        sell_order = {
            "symbol": "AAPL", "quantity": 100, "side": "SELL",
            "order_type": "MARKET", "price": 130.0, "strategy": "test",
            "_signal_date": datetime(2024, 6, 5),
        }
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
