from datetime import date

from quant.features.strategies.reject.ashare_alpha158_factor_composite.strategy import (
    AShareAlpha158FactorCompositeStrategy,
)
from quant.features.strategies.reject.ashare_csi300_low_turnover_multifactor.strategy import (
    AShareCsi300LowTurnoverMultifactorStrategy,
)
from quant.features.strategies.reject.ashare_dividend_low_vol_smart_beta.strategy import (
    AShareDividendLowVolSmartBetaStrategy,
)
from quant.features.strategies.reject.ashare_etf_rsrs_momentum_rotation.strategy import (
    AShareEtfRsrsMomentumRotationStrategy,
)
from quant.features.strategies.reject.ashare_low_vol_value_momentum.strategy import (
    AShareLowVolValueMomentumStrategy,
)
from quant.features.strategies.reject.ashare_white_horse_market_temperature.strategy import (
    AShareWhiteHorseMarketTemperatureStrategy,
)
from quant.features.strategies.registry import StrategyRegistry


class _Portfolio:
    nav = 10000.0


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


def test_forum_large_cap_strategies_are_registered():
    for name in [
        "ashare_csi300_low_turnover_multifactor",
        "ashare_alpha158_factor_composite",
        "ashare_white_horse_market_temperature",
        "ashare_low_vol_value_momentum",
        "ashare_dividend_low_vol_smart_beta",
        "ashare_etf_rsrs_momentum_rotation",
    ]:
        assert StrategyRegistry.is_registered(name)


def test_low_turnover_strategy_limits_daily_replacements():
    strategy = AShareCsi300LowTurnoverMultifactorStrategy(
        symbols=["600001", "600002", "600003", "600004", "000300"],
        max_positions=2,
        target_weight_slots=2,
        max_replacements_per_rebalance=1,
    )
    context = _Context()
    strategy.on_start(context)
    strategy._positions = {"600001": 100, "600002": 100}
    strategy._eligible_snapshots = lambda: [
        {"symbol": "600003"},
        {"symbol": "600004"},
    ]
    strategy._score_snapshots = lambda snapshots: {"600003": 1.0, "600004": 0.9}
    strategy._get_last_price = lambda symbol: 10.0

    strategy._execute_rebalance(context, date(2026, 5, 22))

    sells = [order for order in context.orders if order["side"] == "SELL"]
    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in sells] == ["600001"]
    assert "600003" in {order["symbol"] for order in buys}
    assert all(order["symbol"] != "600002" or order["side"] != "SELL" for order in context.orders)


def test_profile_defaults_map_to_expected_score_specs():
    assert AShareAlpha158FactorCompositeStrategy(symbols=["600001", "000300"]).score_specs[0] == (
        "momentum",
        0.30,
        True,
    )
    assert AShareWhiteHorseMarketTemperatureStrategy(symbols=["600001", "000300"]).score_specs[0] == (
        "roe",
        0.25,
        True,
    )
    assert AShareLowVolValueMomentumStrategy(symbols=["600001", "000300"]).score_specs[0] == (
        "volatility",
        0.30,
        False,
    )
    assert AShareDividendLowVolSmartBetaStrategy(symbols=["600001", "000300"]).score_specs[0] == (
        "dv_ttm",
        0.35,
        True,
    )


def test_etf_rsrs_risk_off_liquidates_without_waiting_for_rebalance_gate():
    strategy = AShareEtfRsrsMomentumRotationStrategy(symbols=["510300"], timing_symbol="000300", holding_days=60)
    context = _Context()
    strategy.on_start(context)
    strategy._positions = {"510300": 1000}
    strategy._last_rebalance_date = date(2026, 5, 21)
    strategy._days_since_rebalance = 1
    strategy._update_rsrs_state = lambda: False
    strategy._get_last_price = lambda symbol: 4.0

    strategy.on_after_trading(context, date(2026, 5, 22))

    assert context.orders == [
        {
            "symbol": "510300",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": 4.0,
            "strategy_name": "ashare_etf_rsrs_momentum_rotation",
        }
    ]
    assert strategy._last_rebalance_date is None
