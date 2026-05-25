from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_broad_etf_momentum_rotation.strategy import (
    AShareBroadEtfMomentumRotationStrategy,
)


class _Portfolio:
    nav = 100000.0


class _Context:
    def __init__(self):
        self.portfolio = _Portfolio()
        self.orders = []

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
        self.orders.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
                "price": price,
                "strategy_name": strategy_name,
            }
        )
        return f"order-{len(self.orders)}"


def _feed(strategy, symbol, closes, *, last_date=date(2026, 5, 20), turnover=200000.0, volume=100000):
    first_date = last_date - timedelta(days=len(closes) - 1)
    for index, close in enumerate(closes):
        bar = {
            "symbol": symbol,
            "timestamp": first_date + timedelta(days=index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_open": close,
            "adj_high": close,
            "adj_low": close,
            "adj_close": close,
            "adj_factor": 1.0,
            "volume": volume,
            "turnover": turnover,
        }
        strategy.on_data(None, bar)


def test_selects_top_two_risk_adjusted_momentum_etfs():
    strategy = AShareBroadEtfMomentumRotationStrategy(
        symbols=["510001", "510002", "510003"],
        momentum_lookback=6,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        target_exposure=0.90,
        holding_days=60,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10.0, 10.2, 10.4, 10.7, 11.0, 11.3, 11.7])
    _feed(strategy, "510002", [10.0, 10.1, 10.25, 10.45, 10.7, 10.95, 11.2])
    _feed(strategy, "510003", [10.0, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert {order["symbol"] for order in context.orders} == {"510001", "510002"}
    assert all(order["side"] == "BUY" for order in context.orders)
    quantities = {order["symbol"]: order["quantity"] for order in context.orders}
    assert quantities["510001"] == 3800
    assert quantities["510002"] == 4000


def test_no_candidate_keeps_portfolio_in_cash_without_buying_cash_etf():
    strategy = AShareBroadEtfMomentumRotationStrategy(
        symbols=["510001", "510002"],
        momentum_lookback=6,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        target_exposure=0.90,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4])
    _feed(strategy, "510002", [10.0, 9.95, 9.9, 9.85, 9.8, 9.75, 9.7])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == []


def test_daily_risk_exit_is_not_blocked_by_rebalance_gate():
    strategy = AShareBroadEtfMomentumRotationStrategy(
        symbols=["510001"],
        momentum_lookback=6,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        holding_days=60,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._last_rebalance_date = date(2026, 5, 19)
    strategy._days_since_rebalance = 1
    strategy.on_fill(context, SimpleNamespace(symbol="510001", quantity=1000, side="BUY"))

    _feed(strategy, "510001", [10.0, 10.2, 10.4, 10.3, 10.1, 9.7, 9.4])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": pytest.approx(9.4),
            "strategy_name": "ashare_broad_etf_momentum_rotation",
        }
    ]
