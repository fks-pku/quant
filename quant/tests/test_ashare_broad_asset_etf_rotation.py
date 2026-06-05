from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_broad_asset_etf_rotation.strategy import (
    AShareBroadAssetEtfRotationStrategy,
    DEFAULT_CATEGORY_SYMBOLS,
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


def test_default_universe_includes_csi1000_and_excludes_cross_border_etfs():
    assert DEFAULT_CATEGORY_SYMBOLS["csi1000"] == ["512100"]
    symbols = {symbol for values in DEFAULT_CATEGORY_SYMBOLS.values() for symbol in values}
    assert "512100" in symbols
    assert not {"513100", "513050", "159920", "510900"}.intersection(symbols)


def test_dynamic_universe_does_not_require_every_candidate_in_daily_snapshot():
    strategy = AShareBroadAssetEtfRotationStrategy()

    assert strategy.required_snapshot_symbols() == []


def test_gold_cash_and_bond_are_ranked_candidates_not_forced_fallbacks():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"gold": ["518880"], "cash": ["511990"], "bond_rate": ["511010"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=1,
        target_exposure=0.90,
        holding_days=1,
        require_pit_size=True,
        volatility_floor=0.01,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "518880", [10.0, 10.01, 10.02, 10.03, 10.04, 10.05, 10.06, 10.07])
    _feed(strategy, "511990", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    _feed(strategy, "511010", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert [order["symbol"] for order in context.orders] == ["511010"]
    assert strategy.get_guard_diagnostics()["last_selected"] == ["511010"]


def test_no_candidate_sells_existing_position_and_keeps_actual_cash():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300"], "gold": ["518880"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        holding_days=1,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(context, SimpleNamespace(symbol="510300", quantity=1000, side="BUY"))

    _feed(strategy, "510300", [10.0, 9.95, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65])
    _feed(strategy, "518880", [5.0, 4.99, 4.98, 4.97, 4.96, 4.95, 4.94, 4.93])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510300",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": pytest.approx(9.65),
            "strategy_name": "ashare_broad_asset_etf_rotation",
        }
    ]
    assert strategy.get_guard_diagnostics()["last_selected"] == []


def test_stale_symbol_is_not_visible_even_when_score_is_best():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300", "515300"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=1,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2])
    _feed(strategy, "515300", [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5], last_date=date(2026, 5, 19))

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert [order["symbol"] for order in context.orders] == ["510300"]
    assert "515300" not in strategy.get_guard_diagnostics()["last_visible_by_category"]["csi300"]


def test_category_cap_keeps_one_representative_per_bucket():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300", "159919"], "gold": ["518880"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        max_positions_per_category=1,
        target_exposure=0.90,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.3, 10.6, 10.9, 11.2, 11.5, 11.8, 12.1])
    _feed(strategy, "159919", [10.0, 10.25, 10.5, 10.75, 11.0, 11.25, 11.5, 11.75])
    _feed(strategy, "518880", [5.0, 5.04, 5.08, 5.12, 5.16, 5.2, 5.24, 5.28])

    strategy.on_after_trading(context, date(2026, 5, 20))

    symbols = [order["symbol"] for order in context.orders]
    assert len([symbol for symbol in symbols if symbol in {"510300", "159919"}]) == 1
    assert "518880" in symbols
