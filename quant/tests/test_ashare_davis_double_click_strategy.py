from datetime import date

from quant.features.strategies.reject.ashare_davis_double_click.strategy import (
    AShareDavisDoubleClickStrategy,
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
        "pe_ttm": 12.0,
        "total_mv": 1000000.0,
        "circ_mv": 800000.0,
        "roe": 12.0,
        "q_roe": 12.0,
        "netprofit_yoy": 30.0,
        "q_netprofit_yoy": 30.0,
        "or_yoy": 10.0,
        "q_sales_yoy": 10.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_davis_double_click_prefers_growth_quality_at_reasonable_pe():
    strategy = AShareDavisDoubleClickStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        momentum_lookback=3,
        momentum_skip=1,
        min_momentum=-1.0,
    )
    context = _Context()
    strategy.on_start(context)

    prices = {
        "600001": [10.0, 10.2, 10.4, 10.6, 10.6],
        "600002": [10.0, 10.2, 10.3, 10.4, 10.4],
        "600003": [10.0, 10.1, 10.2, 10.3, 10.3],
    }
    for index in range(5):
        strategy.on_data(
            None,
            _bar("600001", prices["600001"][index], pe_ttm=16.0, q_netprofit_yoy=80.0, q_roe=18.0),
        )
        strategy.on_data(
            None,
            _bar("600002", prices["600002"][index], pe_ttm=9.0, q_netprofit_yoy=18.0, q_roe=9.0),
        )
        strategy.on_data(
            None,
            _bar("600003", prices["600003"][index], pe_ttm=28.0, q_netprofit_yoy=25.0, q_roe=11.0),
        )

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]


def test_davis_double_click_rejects_weak_profit_growth():
    strategy = AShareDavisDoubleClickStrategy(
        symbols=["600001", "600002"],
        max_positions=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        momentum_lookback=3,
        momentum_skip=1,
        min_momentum=-1.0,
    )
    context = _Context()
    strategy.on_start(context)

    for price in [10.0, 10.1, 10.2, 10.3, 10.3]:
        strategy.on_data(None, _bar("600001", price, q_netprofit_yoy=5.0, netprofit_yoy=5.0))
        strategy.on_data(None, _bar("600002", price, q_netprofit_yoy=30.0, netprofit_yoy=30.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]
    assert strategy.get_guard_diagnostics()["entry_rejections"]["weak_profit_growth"] == 1
