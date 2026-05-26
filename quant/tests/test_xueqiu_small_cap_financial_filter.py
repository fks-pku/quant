from datetime import date
from types import SimpleNamespace

from quant.features.strategies.reject.xueqiu_small_cap_financial_filter.strategy import (
    XueqiuSmallCapFinancialFilterStrategy,
)


def _bar(symbol="000001", **overrides):
    data = {
        "symbol": symbol,
        "close": 10.0,
        "volume": 1000000,
        "turnover": 20000000.0,
        "total_mv": 120000.0,
        "circ_mv": 110000.0,
        "pe_ttm": 20.0,
        "ps_ttm": 8.0,
        "is_st": False,
        "_suspended": False,
        "status_is_suspended": False,
        "tradable": True,
        "has_daily_bar": True,
        "is_listed": True,
        "list_status": "L",
    }
    data.update(overrides)
    return data


def test_xueqiu_filter_rejects_missing_financial_proxies():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"])
    for _ in range(20):
        strategy.on_data(None, _bar())

    assert not strategy._entry_risk("000001", _bar())
    assert strategy._entry_risk("000001", _bar(total_mv=90000.0))
    assert strategy._entry_risk("000001", _bar(pe_ttm=-1.0, pe=-1.0, eps=-0.1, netprofit_margin=-5.0))
    assert strategy._entry_risk("000001", _bar(ps_ttm=20.0, ps=20.0))


def test_xueqiu_score_prefers_smaller_market_cap():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001", "000002"])

    assert strategy._candidate_score("000001", _bar("000001", total_mv=110000.0)) > strategy._candidate_score(
        "000002",
        _bar("000002", total_mv=300000.0),
    )


def test_xueqiu_empty_month_sells_existing_positions():
    orders = []

    class Context:
        portfolio = SimpleNamespace(nav=100000.0)

        def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
            orders.append((symbol, quantity, side, order_type, price, strategy_name))
            return "order-1"

    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"])
    strategy.on_start(Context())
    strategy._positions["000001"] = 100
    strategy.on_data(Context(), _bar("000001"))

    strategy.on_after_trading(Context(), date(2025, 1, 6))

    assert orders == [("000001", 100, "SELL", "MARKET", 10.0, "xueqiu_small_cap_financial_filter")]


def test_xueqiu_empty_months_accepts_empty_list():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"], empty_months=[])

    assert strategy.empty_months == set()
