from datetime import date, timedelta

import pytest

from quant.features.rejected_strategy.joinquant_wufu_daily_etf_lof.strategy import (
    JoinquantWufuDailyEtfLofStrategy,
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


def _feed(strategy, symbol, closes, *, premium_rate=0.0, turnover=50000.0, name="risk ETF", last_date=None):
    first_date = last_date - timedelta(days=len(closes) - 1) if last_date is not None else None
    for index, close in enumerate(closes):
        bar = {
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_open": close,
            "adj_high": close,
            "adj_low": close,
            "adj_close": close,
            "adj_factor": 1.0,
            "volume": 100000,
            "turnover": turnover,
            "premium_rate": premium_rate,
            "fund_name": name,
            "instrument_type": "ETF",
            "fund_status": "L",
        }
        if first_date is not None:
            bar["timestamp"] = first_date + timedelta(days=index)
        strategy.on_data(
            None,
            bar,
        )


def test_selects_highest_25_day_regression_score_after_filters():
    strategy = JoinquantWufuDailyEtfLofStrategy(
        symbols=["510001", "510002", "511880"],
        cash_symbol="511880",
        score_window=25,
        min_active_candidates=2,
        min_avg_turnover=1000,
        max_premium_rate=0.03,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.2 for i in range(25)], turnover=50000)
    _feed(strategy, "510002", [10 + i * 0.05 for i in range(25)], turnover=50000)
    _feed(strategy, "511880", [100 for _ in range(25)], turnover=50000, name="cash ETF")

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 6600,
            "side": "BUY",
            "order_type": "MARKET",
            "price": pytest.approx(14.8),
            "strategy_name": "joinquant_wufu_daily_etf_lof",
        }
    ]


def test_excludes_high_premium_and_low_liquidity_then_uses_cash_symbol():
    strategy = JoinquantWufuDailyEtfLofStrategy(
        symbols=["510001", "510002", "511880"],
        cash_symbol="511880",
        score_window=25,
        min_active_candidates=1,
        min_avg_turnover=1000,
        max_premium_rate=0.03,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.2 for i in range(25)], premium_rate=0.08, turnover=50000)
    _feed(strategy, "510002", [10 + i * 0.1 for i in range(25)], premium_rate=0.0, turnover=50)
    _feed(strategy, "511880", [100 for _ in range(25)], turnover=50000, name="cash ETF")

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["symbol"] == "511880"
    assert context.orders[0]["side"] == "BUY"


def test_ignores_stale_candidate_bar_and_uses_cash_symbol():
    strategy = JoinquantWufuDailyEtfLofStrategy(
        symbols=["510001", "511880"],
        cash_symbol="511880",
        score_window=25,
        min_active_candidates=1,
        min_avg_turnover=1000,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i * 0.2 for i in range(25)], turnover=50000, last_date=date(2026, 5, 19))
    _feed(strategy, "511880", [100 for _ in range(25)], turnover=50000, name="cash ETF", last_date=date(2026, 5, 20))

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["symbol"] == "511880"
