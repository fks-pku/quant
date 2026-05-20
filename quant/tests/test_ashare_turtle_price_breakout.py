from datetime import date

from quant.features.rejected_strategy.ashare_turtle_price_breakout.strategy import (
    AShareTurtlePriceBreakoutStrategy,
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


def _feed(strategy, symbol, closes, **extra):
    for close in closes:
        strategy.on_data(
            None,
            {
                "symbol": symbol,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adj_open": close,
                "adj_high": close,
                "adj_low": close,
                "adj_close": close,
                "volume": 100000,
                "turnover": close * 100000,
                "tradable": True,
                "is_st": False,
                "is_listed": True,
                "list_status": "L",
                **extra,
            },
        )


def test_turtle_breakout_ignores_prices_at_or_below_floor():
    strategy = AShareTurtlePriceBreakoutStrategy(
        symbols=["600001"],
        entry_lookback=3,
        exit_lookback=2,
        atr_window=3,
        max_positions=1,
        min_price=10.0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [8.0, 9.0, 9.5, 9.8])

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == []


def test_turtle_breakout_buys_qualified_donchian_breakout():
    strategy = AShareTurtlePriceBreakoutStrategy(
        symbols=["600001"],
        entry_lookback=3,
        exit_lookback=2,
        atr_window=3,
        max_positions=1,
        min_price=10.0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.5, 10.7, 10.9, 11.5])

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 8600,
            "side": "BUY",
            "order_type": "MARKET",
            "price": 11.5,
            "strategy_name": "ashare_turtle_price_breakout",
        }
    ]


def test_turtle_breakout_sells_when_exit_channel_breaks():
    strategy = AShareTurtlePriceBreakoutStrategy(
        symbols=["600001"],
        entry_lookback=3,
        exit_lookback=2,
        atr_window=3,
        max_positions=1,
        min_price=10.0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 1000

    _feed(strategy, "600001", [11.0, 12.0, 13.0, 10.8])

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 10.8,
            "strategy_name": "ashare_turtle_price_breakout",
        }
    ]
