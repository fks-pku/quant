from datetime import date

from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.reject.ashare_csi300_strict_index_enhanced.strategy import (
    AShareCsi300StrictIndexEnhancedStrategy,
    STRATEGY_NAME,
)


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


def test_csi300_strict_index_enhanced_keeps_metadata_outside_active_registry():
    assert AShareCsi300StrictIndexEnhancedStrategy._registry_name == STRATEGY_NAME
    assert AShareCsi300StrictIndexEnhancedStrategy._registry_active is False
    assert not StrategyRegistry.is_registered(STRATEGY_NAME)


def test_csi300_strict_index_weights_use_latest_prior_date_only():
    strategy = AShareCsi300StrictIndexEnhancedStrategy(
        symbols=["600001", "600002", "000300"],
        index_weights=[
            {"trade_date": "2026-01-31", "symbol": "600001", "weight": 60.0},
            {"trade_date": "2026-01-31", "symbol": "600002", "weight": 40.0},
            {"trade_date": "2026-02-28", "symbol": "600001", "weight": 20.0},
            {"trade_date": "2026-02-28", "symbol": "600002", "weight": 80.0},
        ],
    )

    weight_date, weights = strategy._weights_for_date(date(2026, 2, 15))
    early_date, early_weights = strategy._weights_for_date(date(2026, 1, 1))

    assert weight_date == date(2026, 1, 31)
    assert weights == {"600001": 0.6, "600002": 0.4}
    assert early_date is None
    assert early_weights == {}


def test_csi300_strict_index_enhanced_never_buys_non_constituents():
    strategy = AShareCsi300StrictIndexEnhancedStrategy(
        symbols=["600001", "600002", "000300"],
        index_weights=[{"trade_date": "2026-05-31", "symbol": "600001", "weight": 100.0}],
        max_positions=2,
        min_turnover=0.0,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_data(None, _bar("600001", roe=15.0))
    strategy.on_data(None, _bar("600002", roe=50.0))
    strategy.on_data(None, _bar("000300"))

    strategy.on_after_trading(context, date(2026, 6, 1))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]


def test_csi300_strict_index_enhanced_uses_neutral_warmup_factors():
    strategy = AShareCsi300StrictIndexEnhancedStrategy(symbols=["600001", "000300"], min_turnover=0.0)

    snapshot = strategy._snapshot("600001", _bar("600001"), 0.01)
    diagnostics = strategy.get_guard_diagnostics()

    assert snapshot["missing_field"] == ""
    assert snapshot["momentum"] == 0.0
    assert snapshot["recent_momentum"] == 0.0
    assert snapshot["volatility"] == 0.35
    assert diagnostics["field_missing"]["momentum"] == 1
    assert diagnostics["field_missing"]["recent_momentum"] == 1
