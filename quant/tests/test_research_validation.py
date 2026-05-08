import numpy as np
import pandas as pd
import pytest

from quant.features.research.models import (
    EvaluationReport,
    RawStrategy,
    StrategySpec,
    ValidationReport,
)


def _raw(title="Test Strategy") -> RawStrategy:
    return RawStrategy(
        title=title,
        description="desc",
        source="test",
        source_url="https://example.test",
    )


def _report(strategy_type="momentum", symbols=None) -> EvaluationReport:
    return EvaluationReport(
        suitability_score=7.5,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=symbols or ["SPY"],
        strategy_type=strategy_type,
        summary="test",
    )


class TestStrategySpecBuilder:
    def test_momentum_maps_to_close_return(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Momentum Test"), _report("momentum"))
        assert spec.signal_formula_key == "momentum_close_return"
        assert spec.status == "ready"

    def test_mean_reversion_maps_to_close_to_ma(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("MR Test"), _report("mean_reversion"))
        assert spec.signal_formula_key == "mean_reversion_close_to_ma"
        assert spec.status == "ready"

    def test_breakout_maps_to_atr(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Breakout Test"), _report("breakout"))
        assert spec.signal_formula_key == "volatility_breakout_atr"
        assert spec.status == "ready"

    def test_unknown_type_returns_unsupported(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Unknown"), _report("stat_arb"))
        assert spec.status == "unsupported_type"

    def test_missing_formula_returns_missing_formula(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder(config={"formulas": {}})
        spec = builder.build(_raw("Momentum"), _report("momentum"))
        assert spec.status == "missing_formula"


class TestFDR:
    def test_benjamini_hochberg_deterministic(self):
        from quant.features.research.validation.fdr import benjamini_hochberg

        p_values = [0.001, 0.02, 0.03, 0.20]
        result = benjamini_hochberg(p_values, alpha=0.05)
        assert result[0] == True
        assert result[1] == True
        assert result[2] == True
        assert result[3] == False

    def test_empty_p_values(self):
        from quant.features.research.validation.fdr import benjamini_hochberg

        assert benjamini_hochberg([], alpha=0.05) == []


class TestFactorValidator:
    @staticmethod
    def _make_market_data(n_rows=300):
        dates = pd.date_range("2020-01-01", periods=n_rows, freq="B")
        np.random.seed(42)
        close = 100.0 + np.cumsum(np.random.randn(n_rows) * 0.5)
        high = close + np.abs(np.random.randn(n_rows))
        low = close - np.abs(np.random.randn(n_rows))
        df = pd.DataFrame(
            {"close": close, "high": high, "low": low, "volume": 1000000},
            index=dates,
        )

        class FakeMarketData:
            def get_daily_bars(self, symbols, start, end):
                return df

        return FakeMarketData()

    def test_close_based_signal_applies_execution_lag(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(300)
        validator = FactorValidator(md, config={"min_observations": 50, "execution_lag_days": 1})
        spec = StrategySpec(
            strategy_id="test_mom",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert report.status == "validated"
        assert report.n_observations > 0

    def test_insufficient_observations_returns_error(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(10)
        validator = FactorValidator(md, config={"min_observations": 252})
        spec = StrategySpec(
            strategy_id="test_short",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert report.status == "error"

    def test_supported_formula_returns_validation_report(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(300)
        validator = FactorValidator(md, config={"min_observations": 50})
        spec = StrategySpec(
            strategy_id="test_mr",
            strategy_type="mean_reversion",
            signal_formula_key="mean_reversion_close_to_ma",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert isinstance(report, ValidationReport)
        assert report.status == "validated"

    def test_unsupported_spec_does_not_call_market_data(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        validator = FactorValidator(None, config={"min_observations": 50})
        spec = StrategySpec(
            strategy_id="test_bad",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="unsupported_type",
        )
        report = validator.validate(spec)
        assert report.status == "skipped"
