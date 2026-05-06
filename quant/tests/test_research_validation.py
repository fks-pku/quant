import math
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest

from quant.features.research.models import EvaluationReport, RawStrategy, ResearchConfig, ValidationReport
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.validation import FactorValidator, StrategySpecBuilder, benjamini_hochberg
from quant.features.research.validation.signal_library import build_validation_frame
from quant.infrastructure.research.repository import FileResearchStore


def _raw(title: str = "Daily Momentum") -> RawStrategy:
    return RawStrategy(
        title=title,
        description="Daily close based signal on liquid equities.",
        source="test",
        source_url="https://example.test/research",
    )


def _report(strategy_type: str, symbols=None) -> EvaluationReport:
    return EvaluationReport(
        suitability_score=8.0,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=symbols or ["AAA", "BBB", "CCC"],
        strategy_type=strategy_type,
        summary=f"{strategy_type} signal",
    )


def _validation_report(strategy_id: str = "daily_momentum", status: str = "pass") -> ValidationReport:
    return ValidationReport(
        strategy_id=strategy_id,
        status=status,
        rank_ic=0.08,
        rank_ic_ir=1.2,
        ic_decay=[0.08, 0.04],
        fdr_adjusted_p=0.01,
        fdr_significant=True,
        ff_alpha_monthly=0.0,
        ff_alpha_tstat=0.0,
        ff_r2=0.0,
        long_short_spread=0.02,
        hit_rate=0.60,
        data_start="2020-01-01",
        data_end="2020-03-31",
        n_observations=180,
    )


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_validation" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bars(periods: int = 80) -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2020-01-01", periods=periods, freq="D")
    for index, date in enumerate(dates):
        values = {
            "AAA": 100 + index * 1.00,
            "BBB": 100 + index * 0.25,
            "CCC": 140 - index * 0.30,
        }
        for symbol, close in values.items():
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": close - 0.2,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def test_strategy_spec_builder_maps_supported_strategy_types():
    builder = StrategySpecBuilder()

    momentum = builder.build(_raw("Momentum"), _report("momentum"))
    mean_reversion = builder.build(_raw("Mean Reversion"), _report("mean_reversion"))
    breakout = builder.build(_raw("Breakout"), _report("breakout"))

    assert momentum.status == "ready"
    assert momentum.signal_formula_key == "momentum_close_return"
    assert momentum.required_fields == ("close",)
    assert mean_reversion.signal_formula_key == "mean_reversion_close_to_ma"
    assert breakout.signal_formula_key == "volatility_breakout_atr"
    assert breakout.required_fields == ("high", "low", "close")


def test_strategy_spec_builder_marks_unknown_and_missing_formula():
    unknown = StrategySpecBuilder().build(_raw("Unknown"), _report("stat_arb"))
    missing_formula = StrategySpecBuilder({"formulas": {}}).build(_raw("Momentum"), _report("momentum"))

    assert unknown.status == "unsupported_type"
    assert unknown.signal_formula_key == ""
    assert missing_formula.status == "missing_formula"
    assert missing_formula.signal_formula_key == ""


def test_strategy_spec_builder_marks_configured_manual_spec_type():
    spec = StrategySpecBuilder({"manual_spec_strategy_types": ["value"]}).build(_raw("Value"), _report("value"))

    assert spec.status == "needs_manual_spec"
    assert spec.signal_formula_key == ""
    assert "manual specification" in spec.reason


def test_benjamini_hochberg_adjusts_p_values():
    results = benjamini_hochberg([0.001, 0.02, 0.04, 0.20], alpha=0.05)

    assert [item["significant"] for item in results] == [True, True, False, False]
    assert [item["adjusted_p"] for item in results] == pytest.approx([0.004, 0.04, 0.0533333333, 0.20])


def test_validator_applies_execution_lag_for_close_based_signal():
    spec = StrategySpecBuilder().build(_raw("Momentum"), _report("momentum"))
    frame = build_validation_frame(_bars(40), spec)

    first = frame.sort_values(["signal_date", "symbol"]).iloc[0]

    assert first["return_start_date"] > first["signal_date"]
    assert (first["return_start_date"] - first["signal_date"]).days == spec.execution_lag_days


def test_factor_validator_returns_report_for_supported_formula():
    class FakeMarketData:
        def __init__(self):
            self.calls = 0

        def get_daily_bars(self, symbols, start, end):
            self.calls += 1
            return _bars(90)

    market_data = FakeMarketData()
    spec = StrategySpecBuilder().build(_raw("Momentum"), _report("momentum"))
    validator = FactorValidator(
        market_data,
        config={
            "min_observations": 30,
            "thresholds": {"min_abs_rank_ic": 0.0, "max_fdr_p": 1.0, "min_hit_rate": 0.0},
        },
    )

    report = validator.validate(spec, "2020-01-01", "2020-03-31")

    assert market_data.calls == 1
    assert report.status == "pass"
    assert report.strategy_id == "momentum"
    assert report.n_observations >= 30
    assert math.isfinite(report.rank_ic)


def test_factor_validator_validate_many_applies_run_level_fdr():
    class PreparedValidator(FactorValidator):
        def __init__(self):
            pass

        def _validate_one(self, spec, start, end):
            p_values = {
                "s1": 0.001,
                "s2": 0.02,
                "s3": 0.04,
                "s4": 0.20,
            }
            return _validation_report(spec.strategy_id, "pass"), p_values[spec.strategy_id]

        def _threshold_errors(self, rank_ic, fdr_significant, hit_rate):
            return [] if fdr_significant else ["fdr significance threshold not met"]

        def _max_fdr_p(self):
            return 0.05

    base = StrategySpecBuilder().build(_raw("Momentum"), _report("momentum"))
    specs = [
        base.__class__(
            strategy_id=strategy_id,
            strategy_type=base.strategy_type,
            signal_formula_key=base.signal_formula_key,
            universe=base.universe,
            horizon_days=base.horizon_days,
            lookback_days=base.lookback_days,
            execution_lag_days=base.execution_lag_days,
            required_fields=base.required_fields,
            status=base.status,
        )
        for strategy_id in ("s1", "s2", "s3", "s4")
    ]

    reports = PreparedValidator().validate_many(specs, "2020-01-01", "2020-12-31")

    assert [report.fdr_adjusted_p for report in reports] == pytest.approx([0.004, 0.04, 0.0533333333, 0.20])
    assert [report.fdr_significant for report in reports] == [True, True, False, False]
    assert [report.status for report in reports] == ["pass", "pass", "fail", "fail"]


def test_factor_validator_returns_insufficient_data():
    class FakeMarketData:
        def get_daily_bars(self, symbols, start, end):
            return _bars(8)

    spec = StrategySpecBuilder().build(_raw("Momentum"), _report("momentum"))
    validator = FactorValidator(FakeMarketData(), config={"min_observations": 30})

    report = validator.validate(spec, "2020-01-01", "2020-01-31")

    assert report.status == "insufficient_data"
    assert report.n_observations < 30
    assert "insufficient observations" in report.errors[0]


def test_factor_validator_skips_unsupported_spec_without_market_data_call():
    class FakeMarketData:
        def __init__(self):
            self.calls = 0

        def get_daily_bars(self, symbols, start, end):
            self.calls += 1
            raise AssertionError("market data must not be called")

    market_data = FakeMarketData()
    spec = StrategySpecBuilder().build(_raw("Unknown"), _report("stat_arb"))
    report = FactorValidator(market_data).validate(spec, "2020-01-01", "2020-03-31")

    assert market_data.calls == 0
    assert report.status == "unsupported_type"
    assert report.n_observations == 0


def test_research_engine_validation_gate_counts_and_skips_manual_specs():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw("Daily Momentum"), _raw("Stat Arb")]

    class FixedEvaluator:
        def evaluate(self, raw):
            if raw.title == "Stat Arb":
                return _report("stat_arb")
            return _report("momentum")

    class PassingValidator:
        def __init__(self):
            self.specs = []

        def validate(self, spec, start, end):
            self.specs.append(spec)
            return _validation_report(spec.strategy_id, "pass")

    try:
        validator = PassingValidator()
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=StrategySpecBuilder(),
            validator=validator,
        )

        result = engine.run_full_pipeline()

        assert result.specified == 2
        assert result.validated == 1
        assert result.validated_passed == 1
        assert result.needs_manual_spec == 1
        assert result.integrated == 1
        assert result.rejected == 0
        assert [spec.strategy_id for spec in validator.specs] == ["daily_momentum"]
        assert research_store.get_candidate("daily_momentum") is not None
        assert research_store.get_candidate("stat_arb") is None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_validation_disabled_preserves_integration_behavior():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw("Stat Arb")]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _report("stat_arb")

    class FailingValidator:
        def validate(self, spec, start, end):
            raise AssertionError("validation must be disabled")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=StrategySpecBuilder(),
            validator=FailingValidator(),
        )

        result = engine.run_full_pipeline()

        assert result.specified == 0
        assert result.validated == 0
        assert result.needs_manual_spec == 0
        assert result.integrated == 1
        assert research_store.get_candidate("stat_arb") is not None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_validation_enabled_without_validator_allows_ready_specs():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw("Daily Momentum")]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _report("momentum")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        assert result.specified == 1
        assert result.integrated == 1
        assert result.validated == 0
        assert result.needs_manual_spec == 0
        assert research_store.get_candidate("daily_momentum") is not None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_validation_enabled_without_validator_skips_unsupported_specs():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw("Stat Arb")]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _report("stat_arb")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        assert result.specified == 1
        assert result.integrated == 0
        assert result.validated == 0
        assert result.needs_manual_spec == 1
        assert result.rejected == 0
        assert research_store.get_candidate("stat_arb") is None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_validation_exceptions_without_aborting_run():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw("Daily Momentum")]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _report("momentum")

    class FailingValidator:
        def validate_many(self, specs, start, end):
            raise RuntimeError("market data offline")

        def validate(self, spec, start, end):
            raise RuntimeError("market data offline")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            validator=FailingValidator(),
        )

        result = engine.run_full_pipeline()

        assert result.integrated == 0
        assert result.validated == 1
        assert result.rejected == 1
        assert any("Validation error for Daily Momentum" in error for error in result.errors)
        assert (tmp_path / "research" / "last_result.json").exists()
        assert research_store.get_candidate("daily_momentum") is None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
