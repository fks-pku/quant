import pytest

from quant.api.research_bp import _add_execution_liquidity_features
from quant.features.backtest.order_executor import (
    _execution_adv_value,
    _execution_bar_volume,
    compute_execution_impact,
)


def test_cn_tushare_amount_turnover_is_normalized_to_cash_value():
    bar = {
        "symbol": "000001",
        "close": 15.16,
        "volume": 147911,
        "turnover": 227563.7145,
        "volatility20": 0.02,
    }

    assert _execution_adv_value(bar, 15.16) == pytest.approx(227563714.5)
    assert _execution_bar_volume(bar, 15.16, "CN") == pytest.approx(14791100.0)


def test_cn_small_cap_impact_uses_normalized_turnover_units():
    model = {
        "enabled": True,
        "markets": ["CN"],
        "impact_coefficient": 0.5,
        "volatility_fallback": 0.02,
    }
    bar = {
        "symbol": "000001",
        "volume": 147911,
        "turnover": 227563.7145,
        "volatility20": 0.02,
    }

    impact_bps = compute_execution_impact(
        3200,
        15.16,
        bar,
        "CN",
        model,
        fallback_daily_volume=147911,
        fallback_impact_factor=0.0,
    )

    assert impact_bps == pytest.approx(1.459, rel=1e-3)


def test_research_liquidity_features_normalize_cn_turnover_units():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        [
            {"timestamp": "2026-01-01", "symbol": "000001", "close": 10.0, "volume": 1000, "turnover": 10.0},
            {"timestamp": "2026-01-02", "symbol": "000001", "close": 10.0, "volume": 1000, "turnover": 10.0},
        ]
    )

    enriched = _add_execution_liquidity_features(frame, {"enabled": True, "markets": ["CN"]})

    assert enriched.loc[1, "adv20_value"] == pytest.approx(10000.0)


def test_research_liquidity_features_skip_when_execution_model_disabled():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        [
            {"timestamp": "2026-01-01", "symbol": "000001", "close": 10.0, "volume": 1000, "turnover": 10.0},
            {"timestamp": "2026-01-02", "symbol": "000001", "close": 10.0, "volume": 1000, "turnover": 10.0},
        ]
    )

    enriched = _add_execution_liquidity_features(frame, None)

    assert "adv20_value" not in enriched.columns
    assert "volatility20" not in enriched.columns
