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
    assert report.admission_score > 0
    assert "rank_ic" in report.validation_tests


def test_evaluator_uses_professional_heuristic_when_llm_unavailable():
    report = StrategyEvaluator().evaluate(_raw_strategy())

    assert report.admission_score >= 6.0
    assert report.signal_quality_score >= 6.0
    assert report.data_requirement == "low"
    assert "rank_ic" in report.validation_tests
    assert "fdr_control" in report.validation_tests
    assert report.rejection_reason == ""


def test_evaluator_haircuts_hf_signals_even_when_llm_is_optimistic():
    class OptimisticLLM:
        def analyze(self, prompt, context):
            return {
                "suitability_score": 9.5,
                "complexity_score": 8.0,
                "data_requirement": "high-frequency",
                "daily_adaptable": False,
                "estimated_edge": 0.45,
                "recommended_symbols": ["BTC"],
                "strategy_type": "stat_arb",
                "summary": "High-frequency order book signal.",
                "economic_rationale_score": 1.0,
                "factor_uniqueness_score": 1.0,
                "data_availability_score": 0.2,
                "implementation_score": 0.2,
                "overfit_risk_score": 0.1,
                "cost_capacity_score": 0.1,
                "regime_robustness_score": 0.1,
                "risk_flags": [],
                "rejection_reason": "",
            }

    raw = RawStrategy(
        title="High-Frequency Crypto Order Book Alpha",
        description="Tick-level order book imbalance with deep learning and very high turnover.",
        source="blog",
        source_url="",
    )

    report = StrategyEvaluator(OptimisticLLM()).evaluate(raw)

    assert report.admission_score < 6.0
    assert "high_frequency_not_daily" in report.risk_flags
    assert "unrealistic_edge" in report.risk_flags
    assert report.rejection_reason


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
        report = (tmp_path / "research" / "full_research_report.html").read_text(encoding="utf-8")
        assert "Full Research Report" in report
        assert "Daily Momentum Breakout" in report
        assert "Strict framework backtest report" in report
        assert "000300 CSI 300 index" in report
        index = (tmp_path / "research" / "full_research_report.md").read_text(encoding="utf-8")
        assert "[full_research_report.html](full_research_report.html)" in index
        assert "Complex research reports are rendered as HTML" in index
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
        assert "admission_score" in hypotheses[0]["metrics"]
        assert "signal_quality_score" in hypotheses[0]["metrics"]
        assert hypotheses[0]["evidence"]["source_url"] == "https://example.test/paper"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_writes_promotion_dossier_artifact():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "artifact-1", "name": name, "path": f"/tmp/{name}.json"}

    try:
        artifact_store = RecordingArtifactStore()
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            artifact_store=artifact_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        candidate = research_store.get_candidate("daily_momentum_breakout")

        assert result.integrated == 1
        run_id, name, dossier = next(item for item in artifact_store.saved if item[1] == "promotion_dossier_daily_momentum_breakout")
        assert run_id == "research_pipeline"
        assert name == "promotion_dossier_daily_momentum_breakout"
        assert dossier["strategy_id"] == "daily_momentum_breakout"
        assert dossier["hypothesis"]["title"] == "Daily Momentum Breakout"
        assert dossier["evaluation"]["suitability_score"] == pytest.approx(7.5)
        assert "admission_score" in dossier["evaluation"]
        assert "validation_tests" in dossier
        assert dossier["risk_flags"] == ["survivorship_bias"]
        assert dossier["next_action"] == "walk_forward_or_paper_review"
        assert candidate["research_meta"]["promotion_dossier_artifact"]["artifact_id"] == "artifact-1"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_lineage_manifest_for_tracked_run():
    tmp_path = _test_root()

    class EmptyScout:
        def search(self, sources=None, max_results=10):
            return []

    class RecordingExperimentStore:
        def __init__(self):
            self.started = []
            self.completed = []

        def start_run(self, strategy_id, metadata):
            self.started.append((strategy_id, metadata))
            return "run-lineage"

        def complete_run(self, run_id, status, error=None):
            self.completed.append((run_id, status, error))

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "lineage-1", "name": name}

    experiment_store = RecordingExperimentStore()
    artifact_store = RecordingArtifactStore()
    engine = ResearchEngine(
        config=ResearchConfig(
            auto_backtest=False,
            sources=["arxiv"],
            default_symbols=["SPY", "QQQ"],
            default_backtest_start="2021-01-01",
            default_backtest_end="2024-12-31",
            llm_api_key="secret-key",
        ),
        scout=EmptyScout(),
        research_store=FileResearchStore(tmp_path / "research"),
        experiment_store=experiment_store,
        artifact_store=artifact_store,
        strategies_dir=str(tmp_path / "strategies"),
    )

    result = engine.run_full_pipeline(sources=["arxiv"])

    assert result.run_id == "run-lineage"
    assert experiment_store.started[0][0] == "research_pipeline"
    metadata = experiment_store.started[0][1]
    assert metadata["manifest_version"] == 1
    assert len(metadata["config_hash"]) == 16
    assert len(metadata["data_hash"]) == 16
    assert metadata["data_summary"]["sources"] == ["arxiv"]
    assert metadata["data_summary"]["default_symbols"] == ["SPY", "QQQ"]
    assert metadata["config_summary"]["llm_api_key"] == "***"
    assert artifact_store.saved[0][0] == "run-lineage"
    assert artifact_store.saved[0][1] == "lineage_manifest"
    assert artifact_store.saved[0][2]["run_id"] == "run-lineage"
    assert artifact_store.saved[0][2]["config_hash"] == metadata["config_hash"]


def test_research_engine_records_lineage_manifest_without_tracking():
    tmp_path = _test_root()

    class EmptyScout:
        def search(self, sources=None, max_results=10):
            return []

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "lineage-2", "name": name}

    artifact_store = RecordingArtifactStore()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=False, sources=["ssrn"]),
        scout=EmptyScout(),
        research_store=FileResearchStore(tmp_path / "research"),
        artifact_store=artifact_store,
        strategies_dir=str(tmp_path / "strategies"),
    )

    result = engine.run_full_pipeline()

    assert result.run_id is None
    assert artifact_store.saved[0][0] == "research_pipeline"
    assert artifact_store.saved[0][1] == "lineage_manifest"
    assert artifact_store.saved[0][2]["run_id"] is None
    assert artifact_store.saved[0][2]["data_summary"]["sources"] == ["ssrn"]


def test_research_engine_writes_candidate_scorecard_artifact():
    tmp_path = _test_root()

    high = _raw_strategy()
    low = RawStrategy(
        title="Fragile Intraday Microstructure",
        description="Requires intraday order book effects and does not adapt cleanly to daily bars.",
        source="ssrn",
        source_url="https://example.test/fragile",
    )

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [high, low]

    class MixedEvaluator:
        def evaluate(self, raw):
            report = _evaluation_report()
            if raw.title == low.title:
                report.suitability_score = 2.0
                report.estimated_edge = 0.01
                report.risk_flags = ["hf_not_daily"]
            return report

    class RecordingArtifactStore:
        def __init__(self):
            self.tables = []

        def save_json(self, run_id, name, data):
            return {"artifact_id": f"json-{name}", "name": name}

        def save_table(self, run_id, name, table):
            self.tables.append((run_id, name, table))
            return {"artifact_id": "scorecard-1", "name": name}

    try:
        artifact_store = RecordingArtifactStore()
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, evaluation_threshold=6.0),
            scout=FixedScout(),
            evaluator=MixedEvaluator(),
            research_store=FileResearchStore(tmp_path / "research"),
            artifact_store=artifact_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        run_id, name, rows = next(item for item in artifact_store.tables if item[1] == "candidate_scorecard")
        assert result.integrated == 1
        assert result.rejected == 1
        assert run_id == "research_pipeline"
        assert name == "candidate_scorecard"
        assert [row["title"] for row in rows] == ["Daily Momentum Breakout", "Fragile Intraday Microstructure"]
        assert rows[0]["status"] == "candidate"
        assert rows[0]["strategy_id"] == "daily_momentum_breakout"
        assert rows[0]["suitability_score"] == pytest.approx(7.5)
        assert "admission_score" in rows[0]
        assert "signal_quality_score" in rows[0]
        assert rows[1]["status"] == "rejected"
        assert rows[1]["suitability_score"] == pytest.approx(2.0)
        assert "suitability=2.0" in rows[1]["decision_reason"]
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


def test_research_engine_passes_ready_strategy_spec_to_integrator():
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
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
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

    class RecordingIntegrator:
        registry = {}

        def __init__(self):
            self.received_spec = None

        def integrate(self, raw, report, spec=None):
            self.received_spec = spec
            return "daily_momentum_breakout"

    integrator = RecordingIntegrator()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, validation_enabled=True),
        scout=FixedScout(),
        evaluator=FixedEvaluator(),
        integrator=integrator,
        spec_builder=FixedSpecBuilder(),
        validator=FixedValidator(),
    )

    result = engine.run_full_pipeline()

    assert result.integrated == 1
    assert integrator.received_spec is not None
    assert integrator.received_spec.signal_formula_key == "momentum_close_return"


def test_research_engine_rejects_negative_rank_ic_direction():
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

    class NegativeValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=-0.05,
                rank_ic_ir=-1.0,
                ic_decay=[(1, -0.04), (5, -0.05), (10, -0.03), (21, -0.02)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=-0.001,
                hit_rate=0.45,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    class RecordingIntegrator:
        registry = {}

        def __init__(self):
            self.called = False

        def integrate(self, raw, report, spec=None):
            self.called = True
            return "daily_momentum_breakout"

    integrator = RecordingIntegrator()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, validation_enabled=True),
        scout=FixedScout(),
        evaluator=FixedEvaluator(),
        integrator=integrator,
        spec_builder=FixedSpecBuilder(),
        validator=NegativeValidator(),
    )

    result = engine.run_full_pipeline()

    assert result.integrated == 0
    assert result.rejected == 1
    assert integrator.called is False
    assert any(entry.phase == "validation" and entry.verdict == "fail" for entry in result.log)


def test_research_engine_uses_strategy_spec_universe_for_walkforward():
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
                universe=["AAPL"],
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
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
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

    class RecordingRigorHub:
        def __init__(self):
            self.symbols = None

        def run_walkforward(self, strategy_id, symbols, start, end):
            self.symbols = symbols
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": True,
                    "worst_oos_sharpe": 1.0,
                    "deflated_sharpe_ratio": 0.99,
                },
            )()

    try:
        rigor_hub = RecordingRigorHub()
        engine = ResearchEngine(
            config=ResearchConfig(
                auto_backtest=True,
                rigor_enabled=True,
                default_symbols=["SPY", "QQQ"],
            ),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=FileResearchStore(tmp_path / "research"),
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
            rigor_hub=rigor_hub,
            backtest_fn=lambda *args, **kwargs: None,
        )

        result = engine.run_full_pipeline()

        assert result.integrated == 1
        assert rigor_hub.symbols == ["AAPL"]
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

    scout = research_module._make_strategy_scout(ResearchConfig(sources=["ssrn"], scout_config={"rank_results": True}))

    assert scout._source_hub is not None
    assert scout._hub_sources == ["ssrn"]
    assert scout._source_hub._sources["ssrn"].__class__.__name__ == "SSRNSource"


def test_api_load_research_config_reads_feature_yaml():
    from quant.api import research_bp as research_module

    cfg = research_module._load_research_config()

    assert cfg.sources == ["arxiv", "ssrn", "nber", "blog"]
    assert cfg.scout_config["query_plan"]["ssrn"][0]["query"] == "daily trading strategy equity factor"
    assert cfg.scout_config["required_match_terms"] == ["daily_ohlcv"]


def test_api_remote_llm_without_key_uses_heuristic(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    adapter = research_module._create_llm_adapter(ResearchConfig(llm_provider="deepseek", llm_api_key=None))

    assert adapter is None


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


def test_api_candidate_symbols_prefer_strategy_spec_universe():
    from quant.api import research_bp as research_module

    info = {"research_meta": {"strategy_spec": {"universe": ["AAPL", "MSFT"]}}}

    assert research_module._candidate_symbols(info, ["SPY"]) == ["AAPL", "MSFT"]
    assert research_module._candidate_symbols({}, ["SPY"]) == ["SPY"]


def test_api_latest_report_payload_points_to_full_html_report(tmp_path):
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    report_dir.mkdir()
    report_path = report_dir / "full_research_report.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")

    payload = research_module._latest_report_payload(ResearchConfig(research_dir=str(report_dir)))

    assert payload["available"] is True
    assert payload["url"] == "/api/research/report/latest"
    assert payload["path"] == str(report_path)
    assert "updated_at" in payload


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
