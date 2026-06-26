from datetime import date
from types import SimpleNamespace

import pytest

from quant.features.strategies.xueqiu_small_cap_financial_filter.strategy import (
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


class _Position:
    def __init__(self, avg_cost=None):
        self.avg_cost = avg_cost


class _Portfolio:
    nav = 100000.0

    def __init__(self, positions=None):
        self._positions = positions or {}

    def get_position(self, symbol):
        return self._positions.get(symbol)


class _Context:
    def __init__(self, positions=None):
        self.portfolio = _Portfolio(positions)
        self.orders = []

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
        self.orders.append((symbol, quantity, side, order_type, price, strategy_name))
        return f"order-{len(self.orders)}"


def test_xueqiu_filter_rejects_missing_financial_proxies():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"])
    for _ in range(20):
        strategy.on_data(None, _bar())

    assert not strategy._entry_risk("000001", _bar())
    assert strategy._entry_risk("000001", _bar(total_mv=90000.0))
    assert strategy._entry_risk("000001", _bar(pe_ttm=-1.0, pe=-1.0, eps=-0.1, netprofit_margin=-5.0))
    assert strategy._entry_risk("000001", _bar(ps_ttm=20.0, ps=20.0))


def test_xueqiu_default_universe_excludes_permission_boards():
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001", "002475", "300001", "301001", "688001", "689001"],
        risk_index_symbol="399001",
    )

    assert strategy.symbols == ["000001", "002475", "399001"]
    assert strategy.get_state()["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
    for symbol in ["300001", "301001", "688001", "689001"]:
        assert strategy._entry_risk(symbol, _bar(symbol)) is True


def test_xueqiu_small_cap_declares_financial_and_status_fields():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"])

    fields = set(strategy.required_fields)

    assert {"total_mv", "circ_mv", "pe_ttm", "pe", "ps_ttm", "ps"}.issubset(fields)
    assert {"is_st", "tradable", "has_daily_bar", "is_listed", "list_status"}.issubset(fields)


def test_xueqiu_permission_board_exclusion_can_be_overridden():
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["300001", "688001"],
        excluded_board_prefixes=[],
        min_adv_value=0.0,
    )

    assert strategy.symbols == ["300001", "688001"]
    assert strategy._entry_risk("300001", _bar("300001")) is False
    assert strategy._entry_risk("688001", _bar("688001")) is False


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


def test_xueqiu_dynamic_universe_requires_only_static_risk_index_for_snapshot():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001", "002475"])

    assert strategy.required_snapshot_symbols() == []

    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001", "002475"],
        risk_index_symbol="399001",
    )

    assert strategy.required_snapshot_symbols() == ["399001"]


def test_xueqiu_stop_loss_uses_portfolio_average_cost():
    context = _Context({"000001": _Position(avg_cost=10.0)})
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001"],
        stop_loss_pct=0.10,
        min_stop_loss_pct=0.0,
        max_stop_loss_pct=0.0,
        stop_volatility_multiplier=0.0,
    )
    strategy.on_start(context)
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=7.0, price=7.0))
    strategy.on_data(None, _bar("000001", close=8.9))

    exited = strategy._exit_risk_positions()

    assert exited == {"000001"}
    assert context.orders == [("000001", 100, "SELL", "MARKET", 8.9, "xueqiu_small_cap_financial_filter")]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["stop_loss"] == 1


def test_xueqiu_risk_exit_switch_can_disable_pnl_stops():
    context = _Context({"000001": _Position(avg_cost=10.0)})
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001"],
        risk_exit={"enabled": False, "stop_loss_pct": 0.10},
        min_stop_loss_pct=0.0,
        max_stop_loss_pct=0.0,
        stop_volatility_multiplier=0.0,
    )
    strategy.on_start(context)
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0))
    strategy.on_data(None, _bar("000001", close=8.9))

    assert strategy.get_state()["parameters"]["risk_exit"]["enabled"] is False
    assert strategy._exit_risk_positions() == set()
    assert context.orders == []


def test_xueqiu_trailing_take_profit_exits_after_armed_peak_drawdown():
    context = _Context()
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001"],
        stop_loss_pct=0.0,
        take_profit_pct=0.20,
        trailing_stop_pct=0.08,
        trailing_volatility_multiplier=0.0,
        max_trailing_stop_pct=0.0,
    )
    strategy.on_start(context)
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0))

    strategy.on_data(None, _bar("000001", close=12.8))
    assert strategy._exit_risk_positions() == set()
    strategy.on_data(None, _bar("000001", close=11.4))

    exited = strategy._exit_risk_positions()

    assert exited == {"000001"}
    assert context.orders == [("000001", 100, "SELL", "MARKET", 11.4, "xueqiu_small_cap_financial_filter")]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["trailing_take_profit"] == 1


def test_xueqiu_time_stop_exits_stale_nonperformer():
    context = _Context()
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001"],
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
        trailing_stop_pct=0.0,
        max_holding_days=3,
        min_time_stop_return=0.02,
    )
    strategy.on_start(context)
    strategy.on_data(None, _bar("000001", close=10.0))
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0))
    for _ in range(3):
        strategy.on_data(None, _bar("000001", close=10.1))

    exited = strategy._exit_risk_positions()

    assert exited == {"000001"}
    assert context.orders == [("000001", 100, "SELL", "MARKET", 10.1, "xueqiu_small_cap_financial_filter")]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["time_stop"] == 1


def test_xueqiu_candidate_filter_does_not_apply_profit_stops():
    strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=["000001"],
        stop_loss_pct=0.10,
        min_stop_loss_pct=0.0,
        max_stop_loss_pct=0.0,
        stop_volatility_multiplier=0.0,
    )
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0))
    strategy.on_data(None, _bar("000001", close=8.9))

    assert not strategy._entry_risk("000001", _bar("000001", close=8.9))


def test_xueqiu_zero_price_synthetic_fill_adjusts_entry_and_peak():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001"])
    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0))
    strategy._update_peak_price("000001", 12.0)

    strategy.on_fill(None, SimpleNamespace(symbol="000001", quantity=10, side="BUY", fill_price=0.0, price=0.0))

    assert strategy._positions["000001"] == 110
    assert strategy._entry_prices["000001"] == pytest.approx(1000.0 / 110.0)
    assert strategy._peak_prices["000001"] == pytest.approx(1200.0 / 110.0)
