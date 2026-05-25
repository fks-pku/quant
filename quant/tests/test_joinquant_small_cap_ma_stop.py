from datetime import date

from quant.features.strategies.reject.joinquant_small_cap_ma_stop.strategy import JoinquantSmallCapMaStopStrategy


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


def _feed(strategy, symbol, closes, market_cap=None, **extra):
    for close in closes:
        bar = {
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 100000,
            **extra,
        }
        if market_cap is not None:
            bar["total_mv"] = market_cap
        strategy.on_data(None, bar)


def test_selects_lowest_market_cap_names_equal_weighted():
    strategy = JoinquantSmallCapMaStopStrategy(
        symbols=["600001", "600002", "600003"],
        short_window=2,
        long_window=3,
        max_positions=2,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0, 11.0, 12.0, 13.0], market_cap=300.0)
    _feed(strategy, "600002", [10.0, 11.0, 12.0, 13.0], market_cap=100.0)
    _feed(strategy, "600003", [10.0, 11.0, 12.0, 13.0], market_cap=200.0)

    strategy.on_after_trading(context, date(2026, 5, 17))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002", "600003"]
    assert all(order["quantity"] == 3800 for order in buys)


def test_moving_average_crossunder_sells_held_position():
    strategy = JoinquantSmallCapMaStopStrategy(
        symbols=["600001"],
        short_window=2,
        long_window=3,
        max_positions=1,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 1000

    _feed(strategy, "600001", [10.0, 12.0, 12.0, 8.0], market_cap=100.0)

    strategy.on_after_trading(context, date(2026, 5, 17))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 8.0,
            "strategy_name": "joinquant_small_cap_ma_stop",
        }
    ]


def test_missing_market_cap_does_not_trade_on_turnover_proxy():
    strategy = JoinquantSmallCapMaStopStrategy(
        symbols=["600001", "600002"],
        short_window=2,
        long_window=3,
        max_positions=1,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0, 11.0, 12.0, 13.0], turnover=1.0)
    _feed(strategy, "600002", [10.0, 11.0, 12.0, 13.0], turnover=2.0)

    strategy.on_after_trading(context, date(2026, 5, 17))

    assert context.orders == []
