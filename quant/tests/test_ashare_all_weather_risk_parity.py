from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_all_weather_risk_parity.strategy import (
    AShareAllWeatherRiskParityStrategy,
    DEFAULT_CATEGORY_SYMBOLS,
    DEFAULT_RISK_BUDGETS,
)
from quant.features.strategies.registry import StrategyRegistry


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
    total_netasset=100000000.0,
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
            "total_netasset": total_netasset,
            "net_asset": total_netasset,
        }
        strategy.on_data(None, bar)


def test_default_universe_uses_domestic_all_weather_buckets():
    symbols = {symbol for values in DEFAULT_CATEGORY_SYMBOLS.values() for symbol in values}

    assert DEFAULT_RISK_BUDGETS == {
        "equity": 0.35,
        "gold": 0.20,
        "bond_rate": 0.35,
        "cash": 0.10,
    }
    assert {"510300", "510050", "510880", "518880", "511010", "511990"}.issubset(symbols)
    assert not {"512100", "513100", "513050", "159920", "510900"}.intersection(symbols)


def test_rejected_strategy_import_does_not_register_active_strategy():
    assert StrategyRegistry.get("ashare_all_weather_risk_parity") is None


def test_inverse_volatility_weights_use_risk_budgets_and_asset_cap():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={
            "equity": ["510300"],
            "gold": ["518880"],
            "bond_rate": ["511010"],
            "cash": ["511990"],
        },
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        target_exposure=0.90,
        max_asset_weight=0.45,
        volatility_floor=0.02,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.8, 9.9, 11.0, 10.2, 11.2])
    _feed(strategy, "518880", [10.0, 10.1, 10.2, 10.3, 10.35, 10.4])
    _feed(strategy, "511010", [10.0, 10.03, 10.06, 10.09, 10.12, 10.15])
    _feed(strategy, "511990", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0])

    strategy.on_after_trading(context, date(2026, 5, 20))

    weights = strategy.get_guard_diagnostics()["last_target_weights"]
    assert set(weights) == {"510300", "518880", "511010", "511990"}
    assert sum(weights.values()) == pytest.approx(0.90)
    assert max(weights.values()) <= 0.45 + 1e-12
    assert weights["511010"] > weights["510300"]
    assert {order["side"] for order in context.orders} == {"BUY"}


def test_each_asset_bucket_selects_best_visible_representative():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={"equity": ["510300", "159919"], "gold": ["518880"]},
        risk_budgets={"equity": 0.70, "gold": 0.30},
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        target_exposure=0.80,
        max_asset_weight=0.60,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
    _feed(strategy, "159919", [10.0, 10.3, 10.6, 10.9, 11.2, 11.5])
    _feed(strategy, "518880", [5.0, 5.02, 5.04, 5.06, 5.08, 5.10])

    strategy.on_after_trading(context, date(2026, 5, 20))

    selected = strategy.get_guard_diagnostics()["last_selected_by_category"]
    assert selected["equity"] == "159919"
    assert "510300" not in {order["symbol"] for order in context.orders}


def test_stale_candidate_is_not_selected_even_when_score_is_best():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={"equity": ["510300", "159919"], "gold": ["518880"]},
        risk_budgets={"equity": 0.70, "gold": 0.30},
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        target_exposure=0.80,
        max_asset_weight=0.60,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
    _feed(strategy, "159919", [10.0, 10.5, 11.0, 11.5, 12.0, 12.5], last_date=date(2026, 5, 19))
    _feed(strategy, "518880", [5.0, 5.02, 5.04, 5.06, 5.08, 5.10])

    strategy.on_after_trading(context, date(2026, 5, 20))

    diagnostics = strategy.get_guard_diagnostics()
    assert diagnostics["last_selected_by_category"]["equity"] == "510300"
    assert "159919" not in diagnostics["last_visible_by_category"]["equity"]


def test_missing_pit_size_excludes_bucket_and_keeps_cash_unforced():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={"equity": ["510300"], "gold": ["518880"]},
        risk_budgets={"equity": 0.70, "gold": 0.30},
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        target_exposure=0.80,
        max_asset_weight=0.60,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.3, 10.4, 10.5], total_netasset=None)
    _feed(strategy, "518880", [5.0, 5.02, 5.04, 5.06, 5.08, 5.10])

    strategy.on_after_trading(context, date(2026, 5, 20))

    diagnostics = strategy.get_guard_diagnostics()
    assert diagnostics["last_selected_by_category"] == {"gold": "518880"}
    assert diagnostics["entry_rejections"]["missing_pit_size"] >= 1
    assert {order["symbol"] for order in context.orders} == {"518880"}


def test_empty_candidate_pool_sells_existing_position_without_refreshing_gate():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={"equity": ["510300"], "gold": ["518880"]},
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        holding_days=20,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(
        None,
        SimpleNamespace(symbol="510300", quantity=1000, side="BUY", fill_price=10.0),
    )

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.3, 10.4, 10.5], total_netasset=None)
    _feed(strategy, "518880", [5.0, 5.02, 5.04, 5.06, 5.08, 5.10], total_netasset=None)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510300",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 10.5,
            "strategy_name": "ashare_all_weather_risk_parity",
        }
    ]
    assert strategy._last_rebalance_date is None
    assert strategy._days_since_rebalance == 0


def test_risk_exit_runs_daily_even_inside_rebalance_gate():
    strategy = AShareAllWeatherRiskParityStrategy(
        category_symbols={"equity": ["510300"], "gold": ["518880"]},
        momentum_lookback=4,
        momentum_skip=1,
        trend_window=4,
        volatility_window=4,
        liquidity_window=2,
        min_avg_turnover=1000.0,
        holding_days=20,
        risk_exit={"enabled": True, "stop_loss_pct": 0.08},
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(
        None,
        SimpleNamespace(symbol="510300", quantity=1000, side="BUY", fill_price=10.0),
    )
    strategy._last_rebalance_date = date(2026, 5, 1)
    strategy._days_since_rebalance = 0
    _feed(strategy, "510300", [10.0, 9.1], last_date=date(2026, 5, 20))

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510300",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 9.1,
            "strategy_name": "ashare_all_weather_risk_parity",
        }
    ]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["stop_loss"] == 1
