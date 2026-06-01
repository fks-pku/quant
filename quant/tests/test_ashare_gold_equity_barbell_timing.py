from datetime import date, timedelta

from quant.features.strategies.ashare_gold_equity_barbell_timing.strategy import (
    AShareGoldEquityBarbellTimingStrategy,
    DEFAULT_RISK_ETFS,
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


def _feed(
    strategy,
    symbol,
    closes,
    *,
    last_date=date(2026, 5, 20),
    turnover=50000000.0,
    volume=100000,
    total_netasset=None,
    net_asset=None,
):
    first_date = last_date - timedelta(days=len(closes) - 1)
    for index, close in enumerate(closes):
        bar = {
            "symbol": symbol,
            "timestamp": first_date + timedelta(days=index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_open": close,
            "adj_high": close,
            "adj_low": close,
            "adj_close": close,
            "adj_factor": 1.0,
            "volume": volume,
            "turnover": turnover,
            "total_netasset": total_netasset,
            "net_asset": net_asset,
        }
        strategy.on_data(None, bar)


def test_default_risk_universe_excludes_small_cap_index_etfs():
    assert "510500" not in DEFAULT_RISK_ETFS
    assert "512100" not in DEFAULT_RISK_ETFS


def test_risk_on_buys_best_equity_leg_and_gold_leg():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_symbols=["510001", "510002"],
        defensive_symbols=["518880"],
        timing_symbol="510001",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        target_exposure=0.98,
        risk_leg_weight=0.50,
        holding_days=20,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.3])
    _feed(strategy, "510002", [10.0, 10.1, 10.15, 10.25, 10.35, 10.45, 10.55, 10.6])
    _feed(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert {order["symbol"] for order in context.orders} == {"510001", "518880"}
    quantities = {order["symbol"]: order["quantity"] for order in context.orders}
    assert quantities["510001"] == 4300
    assert quantities["518880"] == 9100


def test_risk_off_buys_gold_only():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_symbols=["510001", "510002"],
        defensive_symbols=["518880"],
        timing_symbol="510001",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        target_exposure=0.98,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510001", [10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.3])
    _feed(strategy, "510002", [10.0, 10.1, 10.2, 10.2, 10.3, 10.4, 10.5, 10.6])
    _feed(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "518880",
            "quantity": 18300,
            "side": "BUY",
            "order_type": "MARKET",
            "price": 5.35,
            "strategy_name": "ashare_gold_equity_barbell_timing",
        }
    ]


def test_risk_exit_stop_loss_runs_even_inside_holding_gate():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_symbols=["510001"],
        defensive_symbols=["518880"],
        timing_symbol="510001",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        holding_days=20,
        risk_exit={"enabled": True, "stop_loss_pct": 0.08},
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(None, type("Fill", (), {"symbol": "510001", "quantity": 1000, "side": "BUY", "fill_price": 10.0})())
    strategy._last_rebalance_date = date(2026, 5, 1)
    strategy._days_since_rebalance = 0
    _feed(strategy, "510001", [10.0, 9.1], last_date=date(2026, 5, 20))

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510001",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 9.1,
            "strategy_name": "ashare_gold_equity_barbell_timing",
        }
    ]
    assert strategy.get_guard_diagnostics()["exit_triggers"]["stop_loss"] == 1


def test_risk_exit_switch_can_disable_gold_barbell_pnl_stops():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_symbols=["510001"],
        defensive_symbols=["518880"],
        timing_symbol="510001",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        risk_exit={"enabled": False, "stop_loss_pct": 0.08},
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(None, type("Fill", (), {"symbol": "510001", "quantity": 1000, "side": "BUY", "fill_price": 10.0})())
    _feed(strategy, "510001", [10.0, 9.1], last_date=date(2026, 5, 20))

    assert strategy.get_state()["parameters"]["risk_exit"]["enabled"] is False
    assert strategy._exit_risk_positions() == set()
    assert context.orders == []


def test_pit_category_universe_ranks_visible_wide_candidates_by_signal():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_category_symbols={
            "csi300": ["510300", "159919"],
            "sse50": ["510050"],
        },
        defensive_category_symbols={"gold": ["518880"]},
        timing_symbol="000300",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        target_exposure=0.98,
        risk_leg_weight=0.50,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "000300", [10.0, 10.2, 10.4, 10.5, 10.7, 10.9, 11.1, 11.3])
    _feed(strategy, "510300", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.1, 10.2], total_netasset=100_000_000_000)
    _feed(strategy, "159919", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4], total_netasset=50_000_000_000)
    _feed(strategy, "510050", [10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.3], total_netasset=80_000_000_000)
    _feed(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35], total_netasset=70_000_000_000)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert "159919" in {order["symbol"] for order in context.orders}
    assert "510300" not in {order["symbol"] for order in context.orders}


def test_pit_category_universe_does_not_select_future_unlisted_symbol():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_category_symbols={"csi300": ["510300", "515300"]},
        defensive_category_symbols={"gold": ["518880"]},
        timing_symbol="000300",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        target_exposure=0.98,
        risk_leg_weight=0.50,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "000300", [10.0, 10.2, 10.4, 10.5, 10.7, 10.9, 11.1, 11.3])
    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2], total_netasset=100_000_000_000)
    _feed(
        strategy,
        "515300",
        [10.0, 10.4, 10.8, 11.2, 11.6, 12.0, 12.4, 12.8],
        last_date=date(2026, 5, 19),
        total_netasset=200_000_000_000,
    )
    _feed(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35], total_netasset=70_000_000_000)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert "510300" in {order["symbol"] for order in context.orders}
    assert "515300" not in {order["symbol"] for order in context.orders}


def test_pit_defensive_category_ranks_visible_gold_candidates_by_signal():
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_category_symbols={"csi300": ["510300"]},
        defensive_category_symbols={"gold": ["518800", "518880"]},
        timing_symbol="000300",
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        target_exposure=0.98,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "000300", [10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.3])
    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.2, 10.3, 10.4, 10.5, 10.6], total_netasset=100_000_000_000)
    _feed(strategy, "518800", [4.0, 4.0, 4.0, 4.0, 4.01, 4.01, 4.01, 4.02], total_netasset=120_000_000_000)
    _feed(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35], total_netasset=70_000_000_000)

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders[0]["symbol"] == "518880"
