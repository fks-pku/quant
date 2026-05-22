"""Invariant tests for strategies module — Registry, Strategy base, _adj."""
from datetime import date
import math
from types import SimpleNamespace

import pytest

from quant.features.strategies.base import Strategy
from quant.features.strategies.joinquant_value_rsrs_timing.strategy import JoinquantValueRsrsTimingStrategy
from quant.features.strategies.registry import StrategyRegistry, strategy


# ---------------------------------------------------------------------------
# CASE-1: Registry CRUD
# ---------------------------------------------------------------------------

class TestCase1RegistryCRUD:
    def test_s1_01_registered(self):
        @strategy("TestInvS1")
        class Dummy:
            pass
        assert StrategyRegistry.is_registered("TestInvS1") is True

    def test_s1_02_create_instance(self):
        @strategy("TestInvS1b")
        class DummyB:
            def __init__(self, val=0):
                self.val = val
        inst = StrategyRegistry.create("TestInvS1b", val=42)
        assert isinstance(inst, DummyB)
        assert inst.val == 42

    def test_s1_03_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            StrategyRegistry.create("NonExistentStrategy999")

    def test_s1_04_list_contains(self):
        @strategy("TestInvS1c")
        class DummyC:
            pass
        assert "TestInvS1c" in StrategyRegistry.list_strategies()


# ---------------------------------------------------------------------------
# CASE-2: _adj helper priority
# ---------------------------------------------------------------------------

class TestCase2AdjHelper:
    """_adj() is for signals/indicators — returns backward-adjusted price as-is."""

    def test_s2_01_prefers_adj_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._adj(bar, "close") == pytest.approx(105.0)

    def test_s2_02_falls_back_to_close(self):
        bar = {"close": 100.0}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_03_nan_fallback(self):
        bar = {"close": 100.0, "adj_close": float("nan")}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_04_none_fallback(self):
        bar = {"close": 100.0, "adj_close": None}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_05_cn_backward_adjusted_preserved_for_signals(self):
        """CN adj_close = close * adj_factor. _adj keeps it for MA continuity."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._adj(bar, "close") == pytest.approx(1160.0)


# ---------------------------------------------------------------------------
# CASE-2b: _price helper — real market price for quantity/order sizing
# ---------------------------------------------------------------------------

class TestCase2bPriceHelper:
    """_price() is for order sizing — returns actual market close."""

    def test_s2b_01_returns_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._price(bar) == pytest.approx(100.0)

    def test_s2b_02_cn_real_price(self):
        """Even with high adj_close, _price returns actual market price."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._price(bar) == pytest.approx(10.0)

    def test_s2b_03_zero_close(self):
        bar = {"close": 0.0}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_04_missing_close(self):
        bar = {}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_05_object_bar(self):
        bar = type("Bar", (), {"close": 55.5, "adj_close": 999.0})()
        assert Strategy._price(bar) == pytest.approx(55.5)


# ---------------------------------------------------------------------------
# CASE-3: buy/sell no-context silent failure
# ---------------------------------------------------------------------------

class TestCase3NoContext:
    def test_s3_01_buy_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx")

        s = TestStrat()
        assert s.buy("AAPL", 100) is None

    def test_s3_02_sell_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx2")

        s = TestStrat()
        assert s.sell("AAPL", 100) is None


# ---------------------------------------------------------------------------
# CASE-4: on_fill updates internal positions
# ---------------------------------------------------------------------------

class TestCase4OnFill:
    def test_s4_01_buy_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill1")

        s = TestStrat()

        class Fill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, Fill())
        assert s.get_position("AAPL") == 100

    def test_s4_02_sell_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill2")

        s = TestStrat()

        class FillBuy:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        class FillSell:
            symbol = "AAPL"
            quantity = 40
            side = "SELL"

        s.on_fill(None, FillBuy())
        s.on_fill(None, FillSell())
        assert s.get_position("AAPL") == 60


# ---------------------------------------------------------------------------
# CASE-5: Daily strategy risk-exit/rebalance state machine
# ---------------------------------------------------------------------------


def _value_rsrs_bar(symbol: str, close: float, **overrides):
    bar = {
        "timestamp": date(2024, 1, 2),
        "symbol": symbol,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "turnover": 100000.0,
        "pe_ttm": 8.0,
        "pb": 1.0,
        "ps_ttm": 1.0,
        "dv_ttm": 1.0,
        "total_mv": 1000.0,
        "circ_mv": 1000.0,
        "is_st": False,
        "tradable": True,
        "has_daily_bar": True,
        "is_listed": True,
        "list_status": "L",
    }
    bar.update(overrides)
    return bar


class _InvariantPortfolio:
    nav = 100000.0

    def get_position(self, symbol):
        return None


class _InvariantContext:
    portfolio = _InvariantPortfolio()


class TestCase5DailyRiskExitStateMachine:
    def test_s5_01_pending_risk_exit_is_not_sold_again_by_same_day_rebalance(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(
            symbols=["000001", "000002"],
            holding_days=1,
            min_turnover=0.0,
            stop_loss_pct=0.10,
        )
        strategy._positions["000001"] = 100
        strategy._entry_prices["000001"] = 10.0
        strategy._risk_on = True
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)
        monkeypatch.setattr(strategy, "_check_rebalance_gate", lambda trading_date: True)
        strategy.on_data(None, _value_rsrs_bar("000001", 8.9))
        strategy.on_data(None, _value_rsrs_bar("000002", 10.0, pb=0.8))
        sells = []
        monkeypatch.setattr(
            strategy,
            "sell",
            lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)),
        )
        monkeypatch.setattr(strategy, "buy", lambda *args, **kwargs: "order-buy")

        strategy.on_after_trading(_InvariantContext(), date(2024, 1, 2))

        assert sells == [("000001", 100, 8.9)]

    def test_s5_02_risk_on_reentry_bypasses_stale_rebalance_gate(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
        strategy._last_rebalance_date = date(2024, 1, 1)
        strategy._days_since_rebalance = 0
        strategy._risk_on = False
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)
        called = []
        monkeypatch.setattr(
            strategy,
            "_execute_rebalance",
            lambda context, trading_date, pending_exit_symbols=None: called.append(trading_date) or True,
        )

        strategy.on_after_trading(_InvariantContext(), date(2024, 2, 1))

        assert called == [date(2024, 2, 1)]

    def test_s5_03_empty_candidate_pool_does_not_refresh_rebalance_gate(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)

        strategy.on_after_trading(_InvariantContext(), date(2024, 1, 2))

        assert strategy._last_rebalance_date is None
        assert strategy._days_since_rebalance == 0

    def test_s5_04_candidate_filter_does_not_use_position_profit_stops(self):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], stop_loss_pct=0.10)
        strategy._positions["000001"] = 100
        strategy._entry_prices["000001"] = 10.0

        reason = strategy._candidate_rejection("000001", _value_rsrs_bar("000001", 8.9))

        assert reason == ""

    def test_s5_05_zero_price_stock_dividend_fill_keeps_internal_cost_in_sync(self):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"])
        strategy.on_fill(
            None,
            SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0),
        )

        strategy.on_fill(
            None,
            SimpleNamespace(symbol="000001", quantity=10, side="BUY", fill_price=0.0, price=0.0),
        )

        assert strategy._positions["000001"] == 110
        assert strategy._entry_prices["000001"] == pytest.approx(1000.0 / 110.0)
