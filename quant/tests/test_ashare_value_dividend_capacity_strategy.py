from datetime import date

from quant.features.strategies.ashare_value_dividend_capacity.strategy import (
    AShareValueDividendCapacityStrategy,
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


def _bar(symbol, **extra):
    return {
        "symbol": symbol,
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "adj_close": 10.0,
        "volume": 1000000,
        "turnover": 1000000,
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


def test_value_dividend_capacity_selects_high_value_dividend_names():
    strategy = AShareValueDividendCapacityStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        min_total_mv=300000.0,
        min_circ_mv=200000.0,
        min_turnover=50000.0,
    )
    context = _Context()
    strategy.on_start(context)

    strategy.on_data(None, _bar("600001", pe_ttm=8.0, pb=0.8, ps_ttm=0.7, dv_ttm=4.0, circ_mv=700000.0))
    strategy.on_data(None, _bar("600002", pe_ttm=30.0, pb=4.0, ps_ttm=6.0, dv_ttm=0.2, circ_mv=2000000.0))
    strategy.on_data(None, _bar("600003", pe_ttm=10.0, pb=1.2, ps_ttm=1.0, dv_ttm=2.5, circ_mv=400000.0))

    strategy.on_after_trading(context, date(2026, 5, 21))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]
    assert buys[0]["quantity"] == 10000


def test_value_dividend_capacity_filters_missing_required_fields():
    strategy = AShareValueDividendCapacityStrategy(symbols=["600001"], max_positions=1)
    context = _Context()
    strategy.on_start(context)

    strategy.on_data(None, _bar("600001", dv_ttm=None))
    strategy.on_after_trading(context, date(2026, 5, 21))

    assert context.orders == []
    diagnostics = strategy.get_guard_diagnostics()
    assert diagnostics["field_missing"]["dv_ttm"] == 1
    assert diagnostics["entry_rejections"]["missing_dv_ttm"] == 1


def test_value_dividend_capacity_exits_daily_on_listing_risk():
    strategy = AShareValueDividendCapacityStrategy(symbols=["600001"], holding_days=20)
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 1000
    strategy._last_rebalance_date = date(2026, 5, 20)

    strategy.on_data(None, _bar("600001", list_status="D"))
    strategy.on_after_trading(context, date(2026, 5, 21))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 10.0,
            "strategy_name": "ashare_value_dividend_capacity",
        }
    ]
