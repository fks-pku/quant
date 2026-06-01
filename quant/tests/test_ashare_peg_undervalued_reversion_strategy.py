from datetime import date

from quant.features.strategies.reject.ashare_peg_undervalued_reversion.strategy import (
    ASharePegUndervaluedReversionStrategy,
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


class _Fill:
    def __init__(self, symbol, side, quantity, price):
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price


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
        "total_mv": 1000000.0,
        "circ_mv": 800000.0,
        "roe": 10.0,
        "q_roe": 10.0,
        "netprofit_yoy": 25.0,
        "q_netprofit_yoy": 25.0,
        "or_yoy": 5.0,
        "q_sales_yoy": 5.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_peg_reversion_prefers_low_peg_with_quality_guard():
    strategy = ASharePegUndervaluedReversionStrategy(
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
        "600002": [10.0, 10.1, 10.2, 10.4, 10.4],
        "600003": [10.0, 10.1, 10.2, 10.3, 10.3],
    }
    for index in range(5):
        strategy.on_data(
            None,
            _bar("600001", prices["600001"][index], pe_ttm=12.0, q_netprofit_yoy=25.0, q_roe=12.0),
        )
        strategy.on_data(
            None,
            _bar("600002", prices["600002"][index], pe_ttm=8.0, q_netprofit_yoy=20.0, q_roe=11.0),
        )
        strategy.on_data(
            None,
            _bar("600003", prices["600003"][index], pe_ttm=25.0, q_netprofit_yoy=30.0, q_roe=14.0),
        )

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]


def test_peg_reversion_rejects_names_above_entry_peg_threshold():
    strategy = ASharePegUndervaluedReversionStrategy(
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
        strategy.on_data(None, _bar("600001", price, pe_ttm=20.0, q_netprofit_yoy=25.0))
        strategy.on_data(None, _bar("600002", price, pe_ttm=10.0, q_netprofit_yoy=25.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]
    assert strategy.get_guard_diagnostics()["entry_rejections"]["peg_too_high"] == 1


def test_peg_reversion_excludes_permission_boards_by_default():
    strategy = ASharePegUndervaluedReversionStrategy(symbols=["300001", "600001"])

    assert strategy.symbols == ["600001"]
    assert strategy.get_state()["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]


def test_peg_reversion_exits_when_peg_normalizes():
    strategy = ASharePegUndervaluedReversionStrategy(
        symbols=["600001"],
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
    strategy.on_fill(context, _Fill("600001", "BUY", 100, 10.0))
    strategy.on_data(None, _bar("600001", 11.0, pe_ttm=32.0, q_netprofit_yoy=30.0))

    reason = strategy._position_exit_reason("600001", strategy._get_last_bar("600001"))

    assert reason == "peg_reversion"


def test_peg_reversion_uses_wide_stop_loss_package():
    strategy = ASharePegUndervaluedReversionStrategy(
        symbols=["600001"],
        stop_loss_pct=0.22,
        min_stop_loss_pct=0.22,
        max_stop_loss_pct=0.22,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(context, _Fill("600001", "BUY", 100, 10.0))
    strategy.on_data(None, _bar("600001", 7.7, pe_ttm=10.0, q_netprofit_yoy=30.0))

    reason = strategy._position_exit_reason("600001", strategy._get_last_bar("600001"))

    assert reason == "stop_loss"
