from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_sector_etf_momentum_rotation.strategy import (
    AShareSectorEtfMomentumRotationStrategy,
    DEFAULT_SECTOR_CATEGORY_SYMBOLS,
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


def _feed(
    strategy,
    symbol,
    closes,
    *,
    last_date=date(2026, 5, 20),
    turnover=50000000.0,
    volume=100000,
):
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


def test_default_sector_pool_excludes_cross_border_etfs():
    symbols = {symbol for values in DEFAULT_SECTOR_CATEGORY_SYMBOLS.values() for symbol in values}

    assert {"512880", "512480", "512690", "512400"}.issubset(symbols)
    assert not {"513100", "513050", "159920", "510900"}.intersection(symbols)


def test_selects_best_representative_per_sector_category():
    strategy = AShareSectorEtfMomentumRotationStrategy(
        category_symbols={
            "brokerage": ["512880", "159016"],
            "semiconductor": ["512480"],
            "military": ["512660"],
        },
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        min_momentum=0.01,
        max_positions=2,
        target_exposure=0.90,
        holding_days=20,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "512880", [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5])
    _feed(strategy, "159016", [10.0, 10.05, 10.1, 10.15, 10.2, 10.25, 10.3, 10.35])
    _feed(strategy, "512480", [5.0, 5.1, 5.2, 5.35, 5.5, 5.65, 5.8, 5.95])
    _feed(strategy, "512660", [10.0, 9.95, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert {order["symbol"] for order in context.orders} == {"512880", "512480"}
    assert "159016" not in {order["symbol"] for order in context.orders}
    assert all(order["side"] == "BUY" for order in context.orders)
    assert set(strategy.get_guard_diagnostics()["last_selected_categories"]) == {"brokerage", "semiconductor"}


def test_low_liquidity_and_below_trend_candidates_are_rejected():
    strategy = AShareSectorEtfMomentumRotationStrategy(
        category_symbols={"consumer": ["512690"], "bank": ["512800"], "coal": ["515220"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        min_momentum=0.01,
        max_positions=3,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "512690", [10.0, 10.2, 10.4, 10.7, 11.0, 11.3, 11.6, 11.9])
    _feed(strategy, "512800", [10.0, 10.2, 10.4, 10.7, 11.0, 11.3, 11.6, 11.9], turnover=0.0, volume=0)
    _feed(strategy, "515220", [10.0, 10.4, 10.8, 11.2, 10.5, 10.1, 9.8, 9.6])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert [order["symbol"] for order in context.orders] == ["512690"]
    rejections = strategy.get_guard_diagnostics()["entry_rejections"]
    assert rejections["low_turnover"] >= 1
    assert rejections["below_trend"] >= 1


def test_position_risk_exit_is_not_blocked_by_rebalance_gate():
    strategy = AShareSectorEtfMomentumRotationStrategy(
        category_symbols={"semiconductor": ["512480"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        holding_days=20,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._last_rebalance_date = date(2026, 5, 19)
    strategy._days_since_rebalance = 1
    strategy.on_fill(context, SimpleNamespace(symbol="512480", quantity=1000, side="BUY"))

    _feed(strategy, "512480", [10.0, 10.2, 10.4, 10.3, 10.1, 9.7, 9.4, 9.1])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "512480",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": pytest.approx(9.1),
            "strategy_name": "ashare_sector_etf_momentum_rotation",
        }
    ]
