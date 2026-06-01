from quant.features.strategies.registry import StrategyRegistry
from quant.features.strategies.reject.ashare_dividend_low_vol_monthly_enhanced.strategy import (
    AShareDividendLowVolMonthlyEnhancedStrategy,
    STRATEGY_NAME,
)


def test_dividend_low_vol_monthly_enhanced_keeps_metadata_outside_active_registry():
    assert AShareDividendLowVolMonthlyEnhancedStrategy._registry_name == STRATEGY_NAME
    assert AShareDividendLowVolMonthlyEnhancedStrategy._registry_active is False
    assert not StrategyRegistry.is_registered(STRATEGY_NAME)


def test_dividend_low_vol_monthly_enhanced_matches_public_research_shape():
    strategy = AShareDividendLowVolMonthlyEnhancedStrategy(symbols=["600001", "000300"])

    assert strategy.holding_days == 20
    assert strategy.max_positions == 30
    assert strategy.volatility_lookback == 20
    assert strategy.drawdown_lookback == 60
    assert strategy.score_specs[:3] == [
        ("dv_ttm", 0.32, True),
        ("volatility", 0.28, False),
        ("pb", 0.14, False),
    ]


def test_dividend_low_vol_monthly_enhanced_risk_exit_can_be_disabled():
    enabled = AShareDividendLowVolMonthlyEnhancedStrategy(symbols=["600001", "000300"])
    disabled = AShareDividendLowVolMonthlyEnhancedStrategy(
        symbols=["600001", "000300"],
        risk_exit={"enabled": False},
    )

    assert enabled.get_state()["parameters"]["risk_exit"]["enabled"] is True
    assert enabled.stop_loss_pct == 0.18
    assert enabled.take_profit_pct == 0.45
    assert enabled.trailing_stop_pct == 0.16
    assert disabled.get_state()["parameters"]["risk_exit"]["enabled"] is False
    assert disabled.stop_loss_pct == 0.0
    assert disabled.take_profit_pct == 0.0
    assert disabled.trailing_stop_pct == 0.0
