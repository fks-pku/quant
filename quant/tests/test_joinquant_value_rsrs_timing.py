from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.joinquant_value_rsrs_timing.strategy import (
    JoinquantValueRsrsTimingStrategy,
)
from quant.features.strategies.registry import StrategyRegistry


def test_strategy_keeps_metadata_outside_active_registry():
    assert JoinquantValueRsrsTimingStrategy._registry_name == "joinquant_value_rsrs_timing"
    assert JoinquantValueRsrsTimingStrategy._registry_active is False
    assert not StrategyRegistry.is_registered("joinquant_value_rsrs_timing")


def test_value_score_prefers_cheaper_higher_dividend_candidate():
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001", "000002"])
    snapshots = [
        {"symbol": "000001", "pb": 0.7, "pe_ttm": 8.0, "ps_ttm": 0.9, "dv_ttm": 5.0, "circ_mv": 500000.0},
        {"symbol": "000002", "pb": 3.0, "pe_ttm": 45.0, "ps_ttm": 8.0, "dv_ttm": 0.5, "circ_mv": 100000.0},
    ]

    scores = strategy._score_snapshots(snapshots)

    assert scores["000001"] > scores["000002"]


def test_rsrs_score_turns_risk_on_when_beta_strengthens():
    strategy = JoinquantValueRsrsTimingStrategy(
        symbols=["000001"],
        rsrs_window=3,
        rsrs_zscore_window=5,
        rsrs_entry=0.5,
        rsrs_exit=-0.5,
    )
    day = date(2024, 1, 1)
    multipliers = [1.02, 1.03, 1.04, 1.10, 1.18, 1.30, 1.45]
    for idx, multiplier in enumerate(multipliers):
        low = 10.0 + idx
        strategy.on_data(
            None,
            {
                "timestamp": day + timedelta(days=idx),
                "symbol": "000300",
                "low": low,
                "high": low * multiplier,
                "close": low * 1.01,
            },
        )

    assert strategy._update_rsrs_state() is True
    assert strategy.get_guard_diagnostics()["rsrs"]["score"] > 0


def test_rsrs_score_turns_risk_off_when_beta_weakens():
    strategy = JoinquantValueRsrsTimingStrategy(
        symbols=["000001"],
        rsrs_window=3,
        rsrs_zscore_window=5,
        rsrs_entry=0.5,
        rsrs_exit=-0.5,
    )
    strategy._risk_on = True
    day = date(2024, 1, 1)
    highs = [10.0, 12.0, 14.0, 20.0, 23.0, 25.0, 26.0]
    for idx, high in enumerate(highs):
        low = 10.0 + idx
        strategy.on_data(
            None,
            {
                "timestamp": day + timedelta(days=idx),
                "symbol": "000300",
                "low": low,
                "high": high,
                "close": low * 1.01,
            },
        )

    assert strategy._update_rsrs_state() is False
    assert strategy.get_guard_diagnostics()["rsrs"]["score"] < 0


def test_risk_off_liquidates_existing_trade_positions(monkeypatch):
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], rsrs_window=3, rsrs_zscore_window=5)
    strategy._positions["000001"] = 100
    strategy.on_data(None, {"timestamp": date(2024, 1, 1), "symbol": "000001", "close": 12.0, "high": 12.5, "low": 11.5})
    sells = []

    def capture_sell(symbol, quantity, order_type="MARKET", price=None):
        sells.append((symbol, quantity, order_type, price))
        return "order-1"

    monkeypatch.setattr(strategy, "sell", capture_sell)

    strategy.on_after_trading(_FakeContext(), date(2024, 1, 1))

    assert sells == [("000001", 100, "MARKET", 12.0)]


def test_hard_stop_loss_exits_position(monkeypatch):
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], stop_loss_pct=0.10)
    strategy.on_fill(
        None,
        SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0),
    )
    strategy.on_data(None, {"timestamp": date(2024, 1, 2), "symbol": "000001", "close": 8.9, "high": 9.1, "low": 8.8})
    sells = []

    def capture_sell(symbol, quantity, order_type="MARKET", price=None):
        sells.append((symbol, quantity, order_type, price))
        return "order-1"

    monkeypatch.setattr(strategy, "sell", capture_sell)

    exited = strategy._exit_risk_positions()

    assert exited == {"000001"}
    assert sells == [("000001", 100, "MARKET", 8.9)]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["stop_loss"] == 1


def test_stop_exit_is_not_submitted_again_during_same_day_rebalance(monkeypatch):
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
    day = date(2024, 1, 2)
    base = {
        "timestamp": day,
        "open": 10.0,
        "high": 10.2,
        "low": 9.8,
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
    strategy.on_data(None, {**base, "symbol": "000001", "close": 8.9})
    strategy.on_data(None, {**base, "symbol": "000002", "close": 10.0, "pb": 0.8})
    sells = []
    monkeypatch.setattr(strategy, "sell", lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)))
    monkeypatch.setattr(strategy, "buy", lambda *args, **kwargs: "order-buy")

    strategy.on_after_trading(_FakeContext(), day)

    assert sells == [("000001", 100, 8.9)]


def test_trailing_take_profit_exits_after_peak_drawdown(monkeypatch):
    strategy = JoinquantValueRsrsTimingStrategy(
        symbols=["000001"],
        take_profit_pct=0.20,
        trailing_stop_pct=0.08,
    )
    strategy.on_fill(
        None,
        SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0),
    )
    sells = []

    def capture_sell(symbol, quantity, order_type="MARKET", price=None):
        sells.append((symbol, quantity, order_type, price))
        return "order-1"

    monkeypatch.setattr(strategy, "sell", capture_sell)
    strategy.on_data(None, {"timestamp": date(2024, 1, 2), "symbol": "000001", "close": 12.5, "high": 12.7, "low": 12.2, "volume": 100000})
    assert strategy._exit_risk_positions() == set()
    strategy.on_data(None, {"timestamp": date(2024, 1, 3), "symbol": "000001", "close": 11.4, "high": 11.6, "low": 11.3, "volume": 100000})

    exited = strategy._exit_risk_positions()

    assert exited == {"000001"}
    assert sells == [("000001", 100, "MARKET", 11.4)]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["trailing_take_profit"] == 1


def test_risk_on_reentry_after_risk_off_rebalances_immediately(monkeypatch):
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
    strategy._last_rebalance_date = date(2024, 1, 1)
    strategy._days_since_rebalance = 0
    strategy._risk_on = False
    monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)
    called = []
    monkeypatch.setattr(strategy, "_execute_rebalance", lambda context, trading_date, pending_exit_symbols=None: called.append(trading_date) or True)

    strategy.on_after_trading(_FakeContext(), date(2024, 2, 1))

    assert called == [date(2024, 2, 1)]


def test_empty_candidate_rebalance_does_not_refresh_gate(monkeypatch):
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
    monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)

    strategy.on_after_trading(_FakeContext(), date(2024, 1, 2))

    assert strategy._last_rebalance_date is None
    assert strategy._days_since_rebalance == 0


def test_candidate_rejection_does_not_use_position_profit_stops():
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], stop_loss_pct=0.10)
    strategy._positions["000001"] = 100
    strategy._entry_prices["000001"] = 10.0

    reason = strategy._candidate_rejection(
        "000001",
        {
            "symbol": "000001",
            "close": 8.9,
            "turnover": 100000.0,
            "is_st": False,
            "tradable": True,
            "has_daily_bar": True,
            "is_listed": True,
            "list_status": "L",
        },
    )

    assert reason == ""


def test_stock_dividend_fill_adjusts_entry_price():
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


def test_low_price_candidate_is_rejected():
    strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], min_price=5.0)
    reason = strategy._candidate_rejection(
        "000001",
        {
            "symbol": "000001",
            "close": 4.9,
            "turnover": 100000.0,
            "is_st": False,
            "tradable": True,
            "has_daily_bar": True,
            "is_listed": True,
            "list_status": "L",
        },
    )

    assert reason == "low_price"


class _FakePortfolio:
    nav = 100000.0


class _FakeContext:
    portfolio = _FakePortfolio()
