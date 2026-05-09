import json
import shutil
import uuid
from pathlib import Path

import pytest
import pandas as pd

from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.models import EvaluationReport, RawStrategy, ResearchConfig, StrategySpec, ValidationReport
from quant.features.research.pool import CandidatePool
from quant.features.research.research_engine import ResearchEngine
from quant.infrastructure.research.repository import FileResearchStore


def _raw_strategy() -> RawStrategy:
    return RawStrategy(
        title="Daily Momentum Breakout",
        description="Ranks liquid stocks by 20 day momentum and buys breakouts using daily OHLCV.",
        source="arxiv",
        source_url="https://example.test/paper",
        authors="Researcher",
        published_date="2026-04-01",
    )


def _evaluation_report() -> EvaluationReport:
    return EvaluationReport(
        suitability_score=7.5,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=["SPY", "QQQ"],
        strategy_type="momentum",
        summary="Daily OHLCV momentum breakout with clear behavioral rationale.",
        economic_rationale_score=2.0,
        factor_uniqueness_score=1.0,
        data_availability_score=2.0,
        implementation_score=2.0,
        overfit_risk_score=1.0,
        cost_capacity_score=1.0,
        regime_robustness_score=1.0,
        risk_flags=["survivorship_bias"],
        rejection_reason="",
    )


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_pipeline" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_evaluator_parses_extended_json_report():
    class JsonLLM:
        def analyze(self, prompt, context):
            return json.dumps(
                {
                    "suitability_score": 7.5,
                    "complexity_score": 3.0,
                    "data_requirement": "low",
                    "daily_adaptable": True,
                    "estimated_edge": 0.08,
                    "recommended_symbols": ["SPY", "QQQ"],
                    "strategy_type": "momentum",
                    "summary": "Daily OHLCV momentum breakout with clear behavioral rationale.",
                    "economic_rationale_score": 2.0,
                    "factor_uniqueness_score": 1.0,
                    "data_availability_score": 2.0,
                    "implementation_score": 2.0,
                    "overfit_risk_score": 1.0,
                    "cost_capacity_score": 1.0,
                    "regime_robustness_score": 1.0,
                    "risk_flags": ["survivorship_bias"],
                    "rejection_reason": "",
                }
            )

    report = StrategyEvaluator(JsonLLM()).evaluate(_raw_strategy())

    assert report.suitability_score == pytest.approx(7.5)
    assert report.economic_rationale_score == pytest.approx(2.0)
    assert report.factor_uniqueness_score == pytest.approx(1.0)
    assert report.data_availability_score == pytest.approx(2.0)
    assert report.risk_flags == ["survivorship_bias"]


def test_research_engine_persists_candidates_and_markdown_artifacts():
    tmp_path = _test_root()
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        candidates = research_store.list_by_status("candidate")
        assert result.integrated == 1
        assert len(candidates) == 1
        assert candidates[0]["id"] == "daily_momentum_breakout"
        assert candidates[0]["research_meta"]["economic_rationale_score"] == pytest.approx(2.0)
        assert (tmp_path / "research" / "last_result.json").exists()
        assert "Daily Momentum Breakout" in (tmp_path / "research" / "discovered_strategies.md").read_text(encoding="utf-8")
        assert "economic_rationale" in (tmp_path / "research" / "strategy_evaluation.md").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_candidate_hypothesis_ledger():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        hypotheses = research_store.list_hypotheses("candidate")

        assert result.integrated == 1
        assert len(hypotheses) == 1
        assert hypotheses[0]["strategy_id"] == "daily_momentum_breakout"
        assert hypotheses[0]["title"] == "Daily Momentum Breakout"
        assert hypotheses[0]["stage"] == "integrate"
        assert hypotheses[0]["metrics"]["suitability_score"] == pytest.approx(7.5)
        assert hypotheses[0]["metrics"]["estimated_edge"] == pytest.approx(0.08)
        assert hypotheses[0]["evidence"]["source_url"] == "https://example.test/paper"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_rejected_hypothesis_ledger():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class LowScoreEvaluator:
        def evaluate(self, raw):
            report = _evaluation_report()
            report.suitability_score = 3.0
            return report

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, evaluation_threshold=6.0),
            scout=FixedScout(),
            evaluator=LowScoreEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        hypotheses = research_store.list_hypotheses("rejected")

        assert result.rejected == 1
        assert len(hypotheses) == 1
        assert hypotheses[0]["strategy_id"] == ""
        assert hypotheses[0]["stage"] == "evaluate"
        assert "suitability=3.0" in hypotheses[0]["decision_reason"]
        assert hypotheses[0]["metrics"]["suitability_score"] == pytest.approx(3.0)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_candidate_pool_updates_persistent_status():
    tmp_path = _test_root()
    research_store = FileResearchStore(tmp_path / "research")
    try:
        research_store.upsert_candidate(
            {
                "id": "daily_momentum_breakout",
                "name": "Daily Momentum Breakout",
                "status": "candidate",
                "research_meta": {"suitability_score": 7.5},
            }
        )

        pool = CandidatePool(research_store=research_store)

        assert pool.promote("daily_momentum_breakout") is True
        assert research_store.get_candidate("daily_momentum_breakout")["status"] == "paused"
        assert pool.promote("daily_momentum_breakout") is False
        assert CandidatePool(research_store=research_store).list_candidates() == []
        assert CandidatePool(research_store=research_store).get_research_meta("daily_momentum_breakout") == {"suitability_score": 7.5}
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_pauses_low_dsr_candidate_without_backtest():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class LowDsrRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end):
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": True,
                    "worst_oos_sharpe": 0.7,
                    "deflated_sharpe_ratio": 0.5,
                },
            )()

    def fail_backtest(*args, **kwargs):
        raise AssertionError("backtest should not run for low DSR candidate")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=fail_backtest,
            rigor_hub=LowDsrRigorHub(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        assert result.rejected == 0
        assert result.walkforward_passed == 0
        assert result.errors == []
        assert candidate["status"] == "needs_more_validation"
        assert candidate["research_meta"]["dsr_warning"] == pytest.approx(0.5)
        assert any(entry.phase == "rigor" and entry.verdict == "warning" for entry in result.log)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_ic_decay_warning_is_logged_without_rejecting():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["SPY"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.03), (10, 0.02), (21, 0.01)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
        )

        result = engine.run_full_pipeline()

        warnings = [entry for entry in result.log if entry.phase == "validation" and entry.verdict == "warn"]
        assert result.integrated == 1
        assert result.rejected == 0
        assert any("high_ic_decay" in entry.reason or "high_ic_decay" in entry.scores for entry in warnings)
        assert any("high_ic_decay" in entry.scores.get("errors", []) for entry in warnings)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_walkforward_trade_enrichment_adds_capacity_fields():
    from quant.api.research_bp import _serialize_walkforward_trade

    class Trade:
        symbol = "SPY"
        side = "BUY"
        quantity = 50
        fill_price = 20.0
        pnl = 0.0
        fill_date = "2020-01-03"

    data = pd.DataFrame(
        [
            {"symbol": "SPY", "timestamp": "2020-01-02", "volume": 10_000},
            {"symbol": "SPY", "timestamp": "2020-01-03", "volume": 20_000},
        ]
    )

    trade = _serialize_walkforward_trade(Trade(), data)

    assert trade["trade_value"] == pytest.approx(1_000.0)
    assert trade["avg_daily_volume"] == pytest.approx(20_000.0)


def test_api_make_strategy_scout_uses_infrastructure_sources():
    from quant.api import research_bp as research_module

    scout = research_module._make_strategy_scout(ResearchConfig(sources=["ssrn"]))

    assert scout._source_hub is not None
    assert scout._hub_sources == ["ssrn"]
    assert scout._source_hub._sources["ssrn"].__class__.__name__ == "SSRNSource"


def test_api_make_rigor_hub_uses_two_arg_walkforward_runner_and_experiment_store(monkeypatch):
    from quant.api import research_bp as research_module

    calls = []
    experiment_store = object()

    def walkforward_runner(strategy_id, request):
        calls.append((strategy_id, request))
        return {"metrics": {"sharpe": 0.0}}

    def legacy_runner_factory():
        raise AssertionError("legacy backtest runner should not wire RigorHub")

    monkeypatch.setattr(research_module, "_make_walkforward_runner", lambda: walkforward_runner)
    monkeypatch.setattr(research_module, "_make_backtest_fn", legacy_runner_factory)

    hub = research_module._make_rigor_hub(ResearchConfig(), experiment_store=experiment_store)
    response = hub._runner("test_strat", {"start": "2020-01-01", "end": "2020-02-01"})

    assert response["metrics"]["sharpe"] == 0.0
    assert calls == [("test_strat", {"start": "2020-01-01", "end": "2020-02-01"})]
    assert hub._experiment_store is experiment_store


def test_api_make_validation_components_wires_market_and_factor_ports(monkeypatch):
    from quant.api import research_bp as research_module

    market_data = object()
    factor_data = object()

    monkeypatch.setattr(research_module, "_make_research_market_data", lambda cfg: market_data)
    monkeypatch.setattr(research_module, "_make_factor_data", lambda cfg: factor_data)

    spec_builder, validator = research_module._make_validation_components(
        ResearchConfig(
            validation_enabled=True,
            validation_min_obs=123,
            validation_config={"min_stocks": 17, "factor_validation_enabled": True},
        )
    )

    assert spec_builder.__class__.__name__ == "StrategySpecBuilder"
    assert validator.__class__.__name__ == "FactorValidator"
    assert validator._market_data is market_data
    assert validator._factor_data is factor_data
    assert validator._config["min_observations"] == 123
    assert validator._config["min_stocks"] == 17
    assert validator._config["factor_validation_enabled"] is True


def test_api_make_validation_components_respects_disabled_flag():
    from quant.api import research_bp as research_module

    assert research_module._make_validation_components(ResearchConfig(validation_enabled=False)) == (None, None)


def test_api_scheduler_injects_validation_components(monkeypatch):
    from quant.api import research_bp as research_module

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(research_module, "_research_scheduler", None)
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(auto_run=False))
    monkeypatch.setattr(research_module, "_make_research_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_strategy_scout", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: object())
    monkeypatch.setattr(research_module, "_make_rigor_hub", lambda cfg, experiment_store=None: None)
    monkeypatch.setattr(research_module, "_make_benchmark_data_loader", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_experiment_stores", lambda cfg: (None, None))
    monkeypatch.setattr(research_module, "_make_validation_components", lambda cfg: ("spec", "validator"))
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)

    scheduler = research_module._get_scheduler()

    assert scheduler.engine is not None
    assert captured["spec_builder"] == "spec"
    assert captured["validator"] == "validator"


def test_cli_make_validation_components_wires_market_and_factor_ports(monkeypatch):
    from quant.scripts import run_research as cli

    market_data = object()
    factor_data = object()

    monkeypatch.setattr(cli, "_create_research_market_data", lambda cfg: market_data)
    monkeypatch.setattr(cli, "_create_factor_data", lambda cfg: factor_data)

    spec_builder, validator = cli._create_validation_components(
        ResearchConfig(
            validation_enabled=True,
            validation_min_obs=88,
            validation_config={"sensitivity_enabled": True},
        )
    )

    assert spec_builder.__class__.__name__ == "StrategySpecBuilder"
    assert validator.__class__.__name__ == "FactorValidator"
    assert validator._market_data is market_data
    assert validator._factor_data is factor_data
    assert validator._config["min_observations"] == 88
    assert validator._config["sensitivity_enabled"] is True
