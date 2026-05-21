from datetime import date

from quant.features.strategies.ashare_mid_cap_dividend_low_vol_capacity.strategy import (
    AShareMidCapDividendLowVolCapacityStrategy,
)
from quant.features.strategies.ashare_mid_cap_low_vol_value.strategy import (
    AShareMidCapLowVolValueStrategy,
)
from quant.features.strategies.ashare_mid_cap_momentum_value_guard.strategy import (
    AShareMidCapMomentumValueGuardStrategy,
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
        "turnover_rate_f": 3.0,
        "pe_ttm": 12.0,
        "pb": 1.0,
        "ps_ttm": 1.0,
        "dv_ttm": 3.0,
        "total_mv": 1000000.0,
        "circ_mv": 800000.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_mid_cap_low_vol_value_uses_dynamic_cap_band():
    strategy = AShareMidCapLowVolValueStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=3,
        cap_percentile_low=0.30,
        cap_percentile_high=0.80,
        min_turnover=0.0,
        volatility_lookback=2,
        drawdown_lookback=2,
    )
    context = _Context()
    strategy.on_start(context)

    for price in [10.0, 10.1, 10.2]:
        strategy.on_data(None, _bar("600001", price, total_mv=100.0))
        strategy.on_data(None, _bar("600002", price, total_mv=200.0))
        strategy.on_data(None, _bar("600003", price, total_mv=300.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]
    diagnostics = strategy.get_guard_diagnostics()
    assert diagnostics["entry_rejections"]["below_mid_cap_band"] == 1
    assert diagnostics["entry_rejections"]["above_mid_cap_band"] == 1


def test_mid_cap_dividend_low_vol_capacity_prefers_dividend_after_filters():
    strategy = AShareMidCapDividendLowVolCapacityStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        volatility_lookback=2,
    )
    context = _Context()
    strategy.on_start(context)

    for price in [10.0, 10.1, 10.2]:
        strategy.on_data(None, _bar("600001", price, dv_ttm=4.0, pb=1.0, circ_mv=800000.0))
        strategy.on_data(None, _bar("600002", price, dv_ttm=1.0, pb=1.0, circ_mv=800000.0))
        strategy.on_data(None, _bar("600003", price, dv_ttm=0.5, pb=1.0, circ_mv=800000.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]


def test_mid_cap_momentum_value_guard_uses_12_1_style_momentum():
    strategy = AShareMidCapMomentumValueGuardStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        momentum_lookback=3,
        momentum_skip=1,
        volatility_lookback=2,
    )
    context = _Context()
    strategy.on_start(context)

    prices = {
        "600001": [10.0, 10.5, 11.0, 11.5, 11.5],
        "600002": [10.0, 10.0, 10.0, 10.0, 10.0],
        "600003": [10.0, 9.9, 9.8, 9.7, 9.7],
    }
    for index in range(5):
        for symbol, series in prices.items():
            strategy.on_data(None, _bar(symbol, series[index], pb=1.0, ps_ttm=1.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]
