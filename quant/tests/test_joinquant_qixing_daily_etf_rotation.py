from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.joinquant_qixing_daily_etf_rotation.strategy import (
    JoinquantQixingDailyEtfRotationStrategy,
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
    turnover=50000.0,
    volume=100000,
    last_date=date(2026, 5, 20),
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


def test_selects_highest_24_day_regression_score():
    strategy = JoinquantQixingDailyEtfRotationStrategy(
        symbols=["510001", "510002", "511880"],
        cash_symbol="511880",
        score_window=24,
        min_active_candidates=2,
        min_avg_turnover=1000,
        max_volume_multiple=10.0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.25 for i in range(24)], turnover=50000)
    _feed(strategy, "510002", [10 + i * 0.05 for i in range(24)], turnover=50000)
    _feed(strategy, "511880", [100 for _ in range(24)], turnover=50000)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 6200,
            "side": "BUY",
            "order_type": "MARKET",
            "price": pytest.approx(15.75),
            "strategy_name": "joinquant_qixing_daily_etf_rotation",
        }
    ]


def test_volume_spike_filter_uses_cash_symbol():
    strategy = JoinquantQixingDailyEtfRotationStrategy(
        symbols=["510001", "511880"],
        cash_symbol="511880",
        score_window=24,
        liquidity_window=5,
        volume_window=5,
        min_active_candidates=1,
        min_avg_turnover=1000,
        max_volume_multiple=1.5,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.2 for i in range(23)], turnover=50000, volume=100000, last_date=date(2026, 5, 19))
    _feed(strategy, "510001", [15.0], turnover=50000, volume=300000, last_date=date(2026, 5, 20))
    _feed(strategy, "511880", [100 for _ in range(24)], turnover=50000, volume=100000)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["symbol"] == "511880"
    assert context.orders[0]["side"] == "BUY"


def test_stop_loss_switches_existing_position_to_cash():
    strategy = JoinquantQixingDailyEtfRotationStrategy(
        symbols=["510001", "510002", "511880"],
        cash_symbol="511880",
        score_window=24,
        min_active_candidates=1,
        min_avg_turnover=1000,
        recent_drawdown_stop=0.05,
        fixed_stop_loss=0.08,
        max_volume_multiple=10.0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.1 for i in range(20)] + [12.0, 11.8, 11.0, 10.9], turnover=50000)
    _feed(strategy, "510002", [10 + i * 0.2 for i in range(24)], turnover=50000)
    _feed(strategy, "511880", [100 for _ in range(24)], turnover=50000)
    strategy.on_fill(
        context,
        SimpleNamespace(symbol="510001", quantity=1000, side="BUY", fill_price=12.0),
    )

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["symbol"] == "510001"
    assert context.orders[0]["side"] == "SELL"
    assert context.orders[1]["symbol"] == "511880"
    assert context.orders[1]["side"] == "BUY"
