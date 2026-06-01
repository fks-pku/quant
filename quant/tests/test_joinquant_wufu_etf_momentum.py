from datetime import date

from quant.features.rejected_strategy.joinquant_wufu_etf_momentum.strategy import (
    JoinquantWufuEtfMomentumStrategy,
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


def _feed(strategy, symbol, closes):
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
            },
        )


def test_selects_highest_positive_regression_momentum_etf():
    strategy = JoinquantWufuEtfMomentumStrategy(
        symbols=["510001", "510002", "511880"],
        core_symbols=["510001", "510002"],
        score_window=13,
        min_active_candidates=2,
        target_exposure=0.98,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10 + i for i in range(13)])
    _feed(strategy, "510002", [10 for _ in range(13)])
    _feed(strategy, "511880", [100 for _ in range(13)])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 4400,
            "side": "BUY",
            "order_type": "MARKET",
            "price": 22.0,
            "strategy_name": "joinquant_wufu_etf_momentum",
        }
    ]


def test_moves_to_fill_etf_when_core_scores_are_not_positive():
    strategy = JoinquantWufuEtfMomentumStrategy(
        symbols=["510001", "510002", "511880"],
        core_symbols=["510001", "510002"],
        score_window=13,
        min_active_candidates=2,
        target_exposure=0.98,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [22 - i for i in range(13)])
    _feed(strategy, "510002", [20 - i * 0.5 for i in range(13)])
    _feed(strategy, "511880", [100 for _ in range(13)])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "511880",
            "quantity": 900,
            "side": "BUY",
            "order_type": "MARKET",
            "price": 100.0,
            "strategy_name": "joinquant_wufu_etf_momentum",
        }
    ]


def test_sells_previous_target_before_buying_new_target():
    strategy = JoinquantWufuEtfMomentumStrategy(
        symbols=["510001", "510002", "511880"],
        core_symbols=["510001", "510002"],
        score_window=13,
        min_active_candidates=2,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["510002"] = 1000

    _feed(strategy, "510001", [10 + i for i in range(13)])
    _feed(strategy, "510002", [10 for _ in range(13)])
    _feed(strategy, "511880", [100 for _ in range(13)])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["side"] == "SELL"
    assert context.orders[0]["symbol"] == "510002"
    assert context.orders[1]["side"] == "BUY"
    assert context.orders[1]["symbol"] == "510001"
