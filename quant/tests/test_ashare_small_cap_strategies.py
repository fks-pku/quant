from datetime import date

from quant.features.strategies.ashare_small_cap_pure_baseline.strategy import (
    AShareSmallCapPureBaselineStrategy,
)
from quant.features.strategies.reject.ashare_small_cap_quality_reversal.strategy import (
    AShareSmallCapQualityReversalStrategy,
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
        bar = {
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1000000,
            "turnover": close * 1000000,
            "total_mv": 1000.0,
            "circ_mv": 800.0,
            "pb": 2.0,
            "ps": 2.0,
            "pe": 20.0,
            "turnover_rate_f": 5.0,
            "volume_ratio": 1.0,
            "tradable": True,
            "has_daily_bar": True,
            "is_st": False,
            "is_listed": True,
            "list_status": "L",
            **extra,
        }
        strategy.on_data(None, bar)


def test_pure_baseline_selects_smallest_valid_market_caps_and_filters_risk_names():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["600001", "600002", "600003", "600004", "600005"],
        max_positions=2,
        min_price=5.0,
        min_adv_value=10000.0,
        target_exposure=1.0,
        market_timing_symbol="",
        stock_trend_window=0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0], total_mv=300.0)
    _feed(strategy, "600002", [10.0], total_mv=100.0)
    _feed(strategy, "600003", [10.0], total_mv=200.0)
    _feed(strategy, "600004", [10.0], total_mv=50.0, is_st=True)
    _feed(strategy, "600005", [4.5], total_mv=40.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002", "600003"]
    assert all(order["quantity"] == 5000 for order in buys)


def test_pure_baseline_market_timing_risk_off_liquidates_and_blocks_new_entries():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["000300", "600001", "600002"],
        max_positions=2,
        min_price=5.0,
        min_adv_value=10000.0,
        target_exposure=1.0,
        market_timing_symbol="000300",
        market_trend_window=3,
        market_momentum_lookback=1,
        market_risk_off_exposure=0.0,
        stock_trend_window=0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 1000

    _feed(strategy, "000300", [10.0, 9.0, 8.0, 7.0], total_mv=0.0)
    _feed(strategy, "600001", [10.0], total_mv=100.0)
    _feed(strategy, "600002", [10.0], total_mv=50.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 10.0,
            "strategy_name": "ashare_small_cap_pure_baseline",
        }
    ]


def test_pure_baseline_target_exposure_uses_fixed_position_slots():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["600001", "600002"],
        max_positions=2,
        min_price=5.0,
        min_adv_value=10000.0,
        target_exposure=0.5,
        market_timing_symbol="",
        stock_trend_window=0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0], total_mv=100.0)
    _feed(strategy, "600002", [10.0], total_mv=50.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002", "600001"]
    assert all(order["quantity"] == 2500 for order in buys)


def test_pure_baseline_quality_controls_filter_expensive_or_crowded_names():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["600001", "600002", "600003", "600004", "600005"],
        max_positions=2,
        min_price=5.0,
        min_adv_value=10000.0,
        target_exposure=1.0,
        market_timing_symbol="",
        stock_trend_window=0,
        max_pb=6.0,
        max_ps_ttm=8.0,
        max_pe_ttm=80.0,
        max_turnover_rate_f=25.0,
        max_volume_ratio=3.0,
        require_positive_pe=True,
        require_quality_fields=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0], total_mv=50.0, pb=12.0)
    _feed(strategy, "600002", [10.0], total_mv=60.0, pe=-5.0)
    _feed(strategy, "600003", [10.0], total_mv=70.0, turnover_rate_f=35.0)
    _feed(strategy, "600004", [10.0], total_mv=80.0, pb=2.0, ps=2.0, pe=20.0)
    _feed(strategy, "600005", [10.0], total_mv=90.0, pb=3.0, ps=3.0, pe=25.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600004", "600005"]
    diagnostics = strategy.get_guard_diagnostics()["entry_rejections"]
    assert diagnostics["high_pb"] == 1
    assert diagnostics["missing_or_negative_pe"] == 1
    assert diagnostics["high_turnover_rate_f"] == 1


def test_pure_baseline_blends_into_broad_index_when_small_cap_relative_strength_is_weak():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["600001", "510300", "510050"],
        max_positions=1,
        min_price=5.0,
        min_adv_value=10000.0,
        target_exposure=0.5,
        market_timing_symbol="",
        stock_trend_window=0,
        broad_index_symbols=["510300", "510050"],
        broad_index_exposure=0.4,
        broad_momentum_lookback=1,
        broad_volatility_window=2,
        broad_volatility_penalty=0.0,
        broad_min_momentum=0.0,
        relative_strength_lookback=1,
        weak_small_cap_exposure=0.2,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "600001", [10.0, 10.0], total_mv=100.0)
    _feed(strategy, "510300", [10.0, 10.5, 11.0], total_mv=0.0)
    _feed(strategy, "510050", [10.0, 10.1, 10.2], total_mv=0.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [(order["symbol"], order["quantity"]) for order in buys] == [
        ("600001", 2000),
        ("510300", 6300),
    ]


def test_pure_baseline_dust_position_does_not_submit_zero_quantity_sell():
    strategy = AShareSmallCapPureBaselineStrategy(
        symbols=["600001"],
        max_positions=1,
        min_price=5.0,
        min_adv_value=10000.0,
        market_timing_symbol="",
        stock_trend_window=0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 0.5

    _feed(strategy, "600001", [4.8], total_mv=100.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == []
    assert strategy.get_guard_diagnostics()["exit_triggers"]["dust_position"] == 1


def test_quality_reversal_penalizes_overheated_or_expensive_micro_caps():
    strategy = AShareSmallCapQualityReversalStrategy(
        symbols=["600001", "600002", "600003"],
        max_positions=1,
        min_price=5.0,
        min_adv_value=10000.0,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(
        strategy,
        "600001",
        [10.0, 10.0, 10.0, 10.0, 10.0, 18.0],
        total_mv=80.0,
        pb=18.0,
        ps=20.0,
        pe=120.0,
        turnover_rate_f=35.0,
        volume_ratio=4.0,
    )
    _feed(strategy, "600002", [10.0, 10.0, 10.0, 10.0, 10.0, 9.5], total_mv=150.0, pb=1.0, ps=1.0, pe=18.0)
    _feed(strategy, "600003", [10.0, 10.0, 10.0, 10.0, 10.0, 9.6], total_mv=120.0, pb=4.0, ps=5.0, pe=70.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600002"]


def test_risk_exit_runs_daily_even_inside_rebalance_interval():
    strategy = AShareSmallCapQualityReversalStrategy(
        symbols=["600001", "600002"],
        max_positions=2,
        rebalance_interval=10,
        min_price=5.0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions["600001"] = 1000
    strategy._rebalance_counter = 5

    _feed(strategy, "600001", [4.8], total_mv=100.0)
    _feed(strategy, "600002", [10.0], total_mv=90.0)

    strategy.on_after_trading(context, date(2026, 5, 19))

    assert context.orders == [
        {
            "symbol": "600001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 4.8,
            "strategy_name": "ashare_small_cap_quality_reversal",
        }
    ]
