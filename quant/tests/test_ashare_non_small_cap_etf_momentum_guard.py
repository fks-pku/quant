from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_non_small_cap_etf_momentum_guard.strategy import (
    AShareNonSmallCapEtfMomentumGuardStrategy,
    DEFAULT_NON_SMALL_CAP_ETFS,
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


def _feed(strategy, symbol, closes, *, last_date=date(2026, 5, 20), turnover=50000000.0, volume=100000):
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


def test_default_universe_excludes_small_cap_index_etfs():
    assert "510500" not in DEFAULT_NON_SMALL_CAP_ETFS
    assert "512100" not in DEFAULT_NON_SMALL_CAP_ETFS


def test_selects_top_risk_adjusted_momentum_etfs():
    strategy = AShareNonSmallCapEtfMomentumGuardStrategy(
        symbols=["510001", "510002", "510003"],
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        min_momentum=0.01,
        max_positions=2,
        target_exposure=0.90,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.3])
    _feed(strategy, "510002", [10.0, 10.1, 10.15, 10.25, 10.35, 10.45, 10.55, 10.6])
    _feed(strategy, "510003", [10.0, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65, 9.6])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert {order["symbol"] for order in context.orders} == {"510001", "510002"}
    assert all(order["side"] == "BUY" for order in context.orders)
    quantities = {order["symbol"]: order["quantity"] for order in context.orders}
    assert quantities["510001"] == 3900
    assert quantities["510002"] == 4200


def test_empty_candidate_pool_sells_existing_position_without_refreshing_gate():
    strategy = AShareNonSmallCapEtfMomentumGuardStrategy(
        symbols=["510001"],
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        min_momentum=0.01,
        holding_days=5,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(context, SimpleNamespace(symbol="510001", quantity=1000, side="BUY"))

    _feed(strategy, "510001", [10.0, 9.95, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": pytest.approx(9.65),
            "strategy_name": "ashare_non_small_cap_etf_momentum_guard",
        }
    ]
    assert strategy._last_rebalance_date is None
    assert strategy._days_since_rebalance == 0
