from datetime import date

from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.reject.ashare_csi1000_strict_index_enhanced.strategy import (
    AShareCsi1000StrictIndexEnhancedStrategy,
    STRATEGY_NAME,
)
from quant.runtime.daily_strategy_runner import run_daily_snapshot


class _Portfolio:
    nav = 1_000_000.0


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


def _bar(symbol: str, close: float = 10.0, **extra):
    return {
        "symbol": symbol,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": 100_000,
        "turnover": 1_000_000.0,
        "total_mv": 1_000_000.0,
        "circ_mv": 800_000.0,
        "pe_ttm": 12.0,
        "pb": 1.2,
        "ps_ttm": 1.5,
        "dv_ttm": 2.0,
        "roe": 10.0,
        "grossprofit_margin": 30.0,
        "debt_to_assets": 45.0,
        "tradable": True,
        "has_daily_bar": True,
        "is_st": False,
        "is_listed": True,
        "list_status": "L",
        **extra,
    }


def test_csi1000_strict_index_enhanced_keeps_metadata_outside_active_registry():
    assert AShareCsi1000StrictIndexEnhancedStrategy._registry_name == STRATEGY_NAME
    assert AShareCsi1000StrictIndexEnhancedStrategy._registry_active is False
    assert not StrategyRegistry.is_registered(STRATEGY_NAME)


def test_csi1000_strict_index_enhanced_defaults_to_000852_benchmark():
    strategy = AShareCsi1000StrictIndexEnhancedStrategy(symbols=["600001", "000852"])
    diagnostics = strategy.get_guard_diagnostics()

    assert strategy.name == STRATEGY_NAME
    assert strategy.benchmark_symbol == "000852"
    assert strategy.trade_symbols == ["600001"]
    assert strategy.max_positions == 120
    assert strategy.max_single_weight == 0.055
    assert diagnostics["parameters"]["benchmark_symbol"] == "000852"


def test_csi1000_strict_index_enhanced_never_buys_non_constituents():
    strategy = AShareCsi1000StrictIndexEnhancedStrategy(
        symbols=["600001", "600002", "000852"],
        index_weights=[{"trade_date": "2026-05-31", "symbol": "600001", "weight": 100.0}],
        max_positions=2,
        min_turnover=0.0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_data(None, _bar("600001", roe=15.0))
    strategy.on_data(None, _bar("600002", roe=50.0))
    strategy.on_data(None, _bar("000852"))

    strategy.on_after_trading(context, date(2026, 6, 1))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]
    assert buys[0]["strategy_name"] == STRATEGY_NAME


def test_csi1000_strict_index_enhanced_dynamic_universe_does_not_block_strict_snapshot():
    strategy = AShareCsi1000StrictIndexEnhancedStrategy(
        symbols=["600001", "600002", "000852"],
        index_weights=[{"trade_date": "2026-05-31", "symbol": "600001", "weight": 100.0}],
        max_positions=2,
        min_turnover=0.0,
    )
    strategy.on_start(_Context())

    result = run_daily_snapshot(
        strategy,
        date(2026, 6, 1),
        [_bar("600001"), _bar("000852")],
        strict=True,
    )

    assert strategy.required_snapshot_symbols() == []
    assert result.ran is True
    assert result.missing_symbols == ()


def test_csi1000_strict_index_enhanced_uses_csi1000_factor_profile():
    strategy = AShareCsi1000StrictIndexEnhancedStrategy(symbols=["600001", "000852"])

    assert strategy.score_specs == [
        ("momentum", 0.28, True),
        ("recent_momentum", 0.18, True),
        ("roe", 0.14, True),
        ("grossprofit_margin", 0.10, True),
        ("volatility", 0.12, False),
        ("pb", 0.08, False),
        ("debt_to_assets", 0.06, False),
        ("index_weight", 0.04, True),
    ]
    assert abs(sum(weight for _, weight, _ in strategy.score_specs) - 1.0) < 0.000001
