from datetime import date

from quant.features.strategies.ashare_value_momentum_filter.strategy import (
    AShareValueMomentumFilterStrategy,
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


def _bar(symbol, close=10.0, **extra):
    return {
        "symbol": symbol,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": 1000000,
        "turnover": 1000000,
        "turnover_rate_f": 2.0,
        "pe_ttm": 10.0,
        "pb": 1.0,
        "ps_ttm": 1.0,
        "total_mv": 1000000.0,
        "circ_mv": 800000.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_value_momentum_filter_prefers_12_1_momentum_after_value_filters():
    strategy = AShareValueMomentumFilterStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        momentum_lookback=5,
        momentum_skip=2,
        recent_return_lookback=2,
        max_recent_return=0.50,
    )
    context = _Context()
    strategy.on_start(context)

    prices = {
        "600001": [10.0, 10.5, 11.0, 12.0, 12.0, 12.0, 12.0],
        "600002": [10.0, 10.1, 10.2, 10.3, 10.3, 10.3, 10.3],
        "600003": [10.0, 9.9, 9.8, 9.7, 9.7, 9.7, 9.7],
    }
    for index in range(7):
        for symbol, series in prices.items():
            strategy.on_data(None, _bar(symbol, series[index], pe_ttm=10.0, pb=1.0, ps_ttm=1.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]


def test_value_momentum_filter_rejects_recent_overheat():
    strategy = AShareValueMomentumFilterStrategy(
        symbols=["600001", "600002"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        momentum_lookback=5,
        momentum_skip=2,
        recent_return_lookback=2,
        max_recent_return=0.20,
    )
    context = _Context()
    strategy.on_start(context)

    prices = {
        "600001": [10.0, 10.5, 11.0, 11.0, 11.0, 15.0, 15.0],
        "600002": [10.0, 10.1, 10.2, 10.3, 10.3, 10.3, 10.3],
    }
    for index in range(7):
        for symbol, series in prices.items():
            strategy.on_data(None, _bar(symbol, series[index], pe_ttm=10.0, pb=1.0, ps_ttm=1.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]
    assert strategy.get_guard_diagnostics()["entry_rejections"]["recent_overheat"] == 1
