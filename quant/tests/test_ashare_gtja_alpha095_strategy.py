from datetime import date
import statistics

from quant.features.strategies.reject.ashare_gtja_alpha095_amount_std.strategy import (
    AShareGtjaAlpha095AmountStdStrategy,
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


def _bar(symbol, close=10.0, amount=1000000.0, **extra):
    return {
        "symbol": symbol,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": 100000,
        "amount": amount,
        "turnover": amount,
        "turnover_rate_f": 2.0,
        "total_mv": 1000000.0,
        "circ_mv": 800000.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_gtja_alpha095_strategy_keeps_metadata_outside_active_registry():
    assert AShareGtjaAlpha095AmountStdStrategy._registry_name == "ashare_gtja_alpha095_amount_std"
    assert AShareGtjaAlpha095AmountStdStrategy._registry_active is False
    assert not StrategyRegistry.is_registered("ashare_gtja_alpha095_amount_std")


def test_gtja_alpha095_matches_twenty_day_amount_std():
    strategy = AShareGtjaAlpha095AmountStdStrategy(symbols=["600001"], amount_lookback=20)
    strategy.on_start(_Context())

    amounts = [float(index * 100000) for index in range(1, 21)]
    for amount in amounts:
        strategy.on_data(None, _bar("600001", amount=amount))

    assert strategy._amount_std("600001") == statistics.stdev(amounts)


def test_gtja_alpha095_prefers_high_raw_amount_std():
    strategy = AShareGtjaAlpha095AmountStdStrategy(
        symbols=["600001", "600002", "000300"],
        max_positions=1,
        target_weight_slots=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        amount_lookback=20,
        alpha_high_is_better=True,
    )
    context = _Context()
    strategy.on_start(context)

    for index in range(20):
        strategy.on_data(None, _bar("600001", amount=500000.0 + index * 250000.0))
        strategy.on_data(None, _bar("600002", amount=1000000.0 + index * 1000.0))
        strategy.on_data(None, _bar("000300", amount=9000000.0 + index * 900000.0))

    strategy.on_after_trading(context, date(2026, 5, 27))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]
    assert "000300" not in strategy._symbols
