import json
import shutil
import uuid
from pathlib import Path

import pytest
import pandas as pd

from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, EvaluationReport, RawStrategy, ResearchConfig, StrategySpec, ValidationReport
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


def test_research_defaults_and_spec_universe_are_a_share_only():
    from quant.features.research.validation.strategy_spec_builder import StrategySpecBuilder
    from quant.scripts.run_research import HeuristicEvaluator

    assert ResearchConfig().default_symbols == DEFAULT_A_SHARE_SYMBOLS

    report = _evaluation_report()
    spec = StrategySpecBuilder().build(_raw_strategy(), report)

    assert spec.universe == DEFAULT_A_SHARE_SYMBOLS

    full_universe = ["000001", "000002", "600519", "AAPL"]
    full_spec = StrategySpecBuilder({"default_universe": full_universe}).build(_raw_strategy(), report)

    assert full_spec.universe == ["000001", "000002", "600519"]

    cli_report = HeuristicEvaluator().evaluate(_raw_strategy())

    assert cli_report.recommended_symbols == DEFAULT_A_SHARE_SYMBOLS


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


def test_heuristic_evaluator_uses_cn_symbols_when_target_market_is_cn():
    report = StrategyEvaluator(rubric_config={
        "target_market": "cn",
        "default_symbols": ["000300", "600519"],
    }).evaluate(_raw_strategy())

    assert report.recommended_symbols == ["000300", "600519"]


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
        assert (tmp_path / "research" / "reports" / "latest" / "last_result.json").exists()
        assert "Daily Momentum Breakout" in (tmp_path / "research" / "idea_bank" / "discovered_strategies.md").read_text(encoding="utf-8")
        assert "economic_rationale" in (tmp_path / "research" / "reports" / "latest" / "strategy_evaluation.md").read_text(encoding="utf-8")
        report = (tmp_path / "research" / "reports" / "latest" / "full_research_report.html").read_text(encoding="utf-8")
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "latest" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "latest" / "metadata.json").exists()
        assert "完整策略研究报告" in report
        assert "1. 结论汇总" in report
        assert "2. idea 来源与初筛" in report
        assert "信号公式" in report
        assert 'class="grid"' in report
        assert 'class="formula"' in report
        assert "6. 策略回测报告" in report
        assert "7. purged walk-forward" in report
        assert "Split 明细" in report
        assert "Daily Momentum Breakout" in report
        assert "000300" in report
        index = (tmp_path / "research" / "reports" / "latest" / "full_research_report.md").read_text(encoding="utf-8")
        assert "[full_research_report.html](full_research_report.html)" in index
        assert "复杂研究报告统一使用 HTML" in index
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_discovery_only_stores_idea_bank_without_evaluation():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FailingEvaluator:
        def evaluate(self, raw):
            raise AssertionError("discovery-only mode must not evaluate ideas")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True),
            scout=FixedScout(),
            evaluator=FailingEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_discovery_only()
        ideas = research_store.list_ideas("discovered")

        assert result.discovered == 1
        assert result.evaluated == 0
        assert result.integrated == 0
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Daily Momentum Breakout"
        assert (tmp_path / "research" / "idea_bank" / "discovered_strategies.md").exists()
        assert (tmp_path / "research" / "idea_bank" / "idea_bank.json").exists()
        assert (tmp_path / "research" / "idea_bank" / "idea_bank.md").exists()
        assert not (tmp_path / "research" / "full_research_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_formal_research_loads_local_idea_bank_without_scouting():
    tmp_path = _test_root()

    class FailingScout:
        def search(self, sources=None, max_results=10):
            raise AssertionError("formal mode must load from local idea bank")

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_idea(_raw_strategy(), status="discovered", reason="seed")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FailingScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_formal_research_from_idea_bank()
        ideas = research_store.list_ideas("candidate")

        assert result.discovered == 1
        assert result.evaluated == 1
        assert result.integrated == 1
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Daily Momentum Breakout"
        assert (tmp_path / "research" / "reports" / "latest" / "strategy_evaluation.md").exists()
        assert not (tmp_path / "research" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "latest" / "full_research_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_formal_research_filters_by_idea_id():
    tmp_path = _test_root()
    second = RawStrategy(
        title="Daily Breakout Candidate",
        description="Daily OHLCV breakout strategy for liquid A-share equities.",
        source="arxiv",
        source_url="https://example.test/breakout",
        authors="Researcher",
        published_date="2026-02-01",
    )

    class FailingScout:
        def search(self, sources=None, max_results=10):
            raise AssertionError("formal mode must load from local idea bank")

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_idea(_raw_strategy(), status="discovered", reason="seed")
        research_store.upsert_idea(second, status="discovered", reason="seed")
        selected_id = next(row["idea_id"] for row in research_store.list_ideas() if row["title"] == second.title)
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FailingScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_formal_research_from_idea_bank(idea_ids=[selected_id])

        assert result.discovered == 1
        assert result.integrated == 1
        assert research_store.list_ideas("candidate")[0]["title"] == second.title
        assert research_store.list_ideas("discovered")[0]["title"] == "Daily Momentum Breakout"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_full_research_report_replaces_corrupt_decision_reason():
    from quant.infrastructure.research.reporting import build_full_research_report_html

    report = build_full_research_report_html(
        {"run_id": "encoding_guard", "backtested": 1, "walkforward_passed": 0},
        [
            {
                "title": "Encoding Guard",
                "status": "rejected",
                "stage": "go_no_go",
                "decision_reason": "?? FDR ?????????",
                "metrics": {
                    "fdr_adjusted_p": 0.41,
                    "rank_ic": 0.008,
                    "strict_backtest": {"metrics": {"sharpe": 0.5}},
                },
            }
        ],
    )

    assert "?? FDR" not in report
    assert "信号 FDR 不显著" in report


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
        assert hypotheses[0]["stage"] == "stage2_integrate"
        assert hypotheses[0]["metrics"]["suitability_score"] == pytest.approx(7.5)
        assert hypotheses[0]["metrics"]["estimated_edge"] == pytest.approx(0.08)
        assert "admission_score" in hypotheses[0]["metrics"]
        assert "signal_quality_score" in hypotheses[0]["metrics"]
        assert hypotheses[0]["evidence"]["source_url"] == "https://example.test/paper"
        assert any(entry.phase == "stage1_queue" for entry in result.log)
        assert any(entry.phase == "stage2_integrate" for entry in result.log)
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
        assert hypotheses[0]["stage"] == "stage1_admission"
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


def test_research_engine_runs_strict_backtest_before_pausing_low_dsr_candidate():
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

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            rigor_hub=LowDsrRigorHub(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        assert result.rejected == 0
        assert result.backtested == 1
        assert result.walkforward_passed == 0
        assert result.errors == []
        assert candidate["status"] == "needs_more_validation"
        assert candidate["research_meta"]["dsr_warning"] == pytest.approx(0.5)
        assert any(entry.phase == "rigor" and entry.verdict == "warning" for entry in result.log)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_nonviable_walkforward_rejects_candidate_and_updates_ledger():
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
                universe=["000300"],
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

    class NonviableRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end):
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": False,
                    "aggregate_oos_sharpe": -1.2,
                    "worst_oos_sharpe": -4.6,
                    "pct_profitable_splits": 0.2,
                    "deflated_sharpe_ratio": 0.0,
                },
            )()

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            rigor_hub=NonviableRigorHub(),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        idea = research_store.list_ideas()[0]
        assert result.rejected == 1
        assert result.backtested == 1
        assert candidate["status"] == "rejected"
        assert not (tmp_path / "strategies" / "daily_momentum_breakout" / "strategy.py").exists()
        assert (tmp_path / "rejected_strategy" / "daily_momentum_breakout" / "strategy.py").exists()
        assert hypothesis["status"] == "rejected"
        assert hypothesis["stage"] == "go_no_go"
        assert "strict Backtester executed for audit" in hypothesis["decision_reason"]
        assert hypothesis["metrics"]["walkforward"]["aggregate_oos_sharpe"] == pytest.approx(-1.2)
        assert hypothesis["metrics"]["walkforward"]["worst_oos_sharpe"] == pytest.approx(-4.6)
        assert hypothesis["metrics"]["walkforward"]["verdict"] == "fail"
        assert idea["status"] == "rejected"
        assert any(entry.phase == "stage2_validation" and entry.verdict == "info" for entry in result.log)
        assert any(entry.phase == "rigor" and entry.verdict == "info" for entry in result.log)
        assert any(entry.phase == "rigor" and entry.verdict == "fail" for entry in result.log)
        rigor_fail = next(entry for entry in result.log if entry.phase == "rigor" and entry.verdict == "fail")
        assert rigor_fail.scores["deflated_sharpe_ratio"] == pytest.approx(0.0)
        assert any(entry.phase == "backtest" and entry.verdict == "info" for entry in result.log)
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

        warnings = [entry for entry in result.log if entry.phase == "stage2_validation" and entry.verdict == "warn"]
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


def test_research_engine_continues_after_negative_rank_ic_direction():
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

    assert result.integrated == 1
    assert result.rejected == 0
    assert integrator.called is True
    assert any(entry.phase == "stage2_validation" and entry.verdict == "fail" for entry in result.log)


def test_validation_failed_strategy_runs_backtest_then_archives_to_rejected_strategy():
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
                universe=["000300"],
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

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            spec_builder=FixedSpecBuilder(),
            validator=NegativeValidator(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        idea = research_store.list_ideas()[0]
        assert result.integrated == 1
        assert result.backtested == 1
        assert result.rejected == 1
        assert candidate["status"] == "rejected"
        assert candidate["research_meta"]["validation_gate"]["status"] == "failed"
        assert candidate["research_meta"]["rejected_strategy_dir"].endswith(
            "rejected_strategy/daily_momentum_breakout"
        )
        assert not (tmp_path / "strategies" / "daily_momentum_breakout" / "strategy.py").exists()
        assert (tmp_path / "rejected_strategy" / "daily_momentum_breakout" / "strategy.py").exists()
        assert hypothesis["status"] == "rejected"
        assert hypothesis["stage"] == "go_no_go"
        assert "Validation failed" in hypothesis["decision_reason"]
        assert "strict Backtester executed for audit" in hypothesis["decision_reason"]
        assert idea["status"] == "rejected"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


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
                universe=["600519"],
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
                default_symbols=["000300", "000905"],
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
        assert rigor_hub.symbols == ["600519"]
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


def test_research_config_default_backtest_window_spans_2012_to_2025():
    cfg = ResearchConfig()

    assert cfg.default_backtest_start == "2012-01-01"
    assert cfg.default_backtest_end == "2025-12-31"


def test_api_yearly_returns_from_equity_uses_calendar_years():
    from quant.api.research_bp import _yearly_returns_from_equity

    equity = pd.Series(
        [100.0, 110.0, 99.0],
        index=pd.to_datetime(["2012-01-03", "2012-12-31", "2013-12-31"]),
    )

    yearly = _yearly_returns_from_equity(equity, initial_cash=100.0)

    assert yearly["2012"] == pytest.approx(0.10)
    assert yearly["2013"] == pytest.approx(-0.10)


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
    assert validator._config["start_date"] == "2012-01-01"
    assert validator._config["end_date"] == "2025-12-31"


def test_api_make_validation_components_respects_disabled_flag():
    from quant.api import research_bp as research_module

    assert research_module._make_validation_components(ResearchConfig(validation_enabled=False)) == (None, None)


def test_api_candidate_symbols_prefer_strategy_spec_universe():
    from quant.api import research_bp as research_module

    info = {"research_meta": {"strategy_spec": {"universe": ["600519", "000001"]}}}

    assert research_module._candidate_symbols(info, ["000300"]) == ["600519", "000001"]
    assert research_module._candidate_symbols({"research_meta": {"strategy_spec": {"universe": ["AAPL"]}}}, ["SPY"]) == DEFAULT_A_SHARE_SYMBOLS
    assert research_module._candidate_symbols({}, ["000300"]) == ["000300"]


def test_api_latest_report_payload_points_to_full_html_report(tmp_path):
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    (report_dir / "reports" / "latest").mkdir(parents=True)
    report_path = report_dir / "reports" / "latest" / "full_research_report.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")

    payload = research_module._latest_report_payload(ResearchConfig(research_dir=str(report_dir)))

    assert payload["available"] is True
    assert payload["url"] == "/api/research/report/latest"
    assert payload["path"] == str(report_path)
    assert payload["reports_root"] == str(report_dir / "reports")
    assert "updated_at" in payload


def test_api_latest_report_payload_falls_back_to_legacy_report(tmp_path):
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    report_dir.mkdir()
    report_path = report_dir / "full_research_report.html"
    report_path.write_text("<html><body>legacy</body></html>", encoding="utf-8")

    payload = research_module._latest_report_payload(ResearchConfig(research_dir=str(report_dir)))

    assert payload["available"] is True
    assert payload["path"] == str(report_path)


def test_api_parse_research_idea_statuses():
    from quant.api import research_bp as research_module

    assert research_module._parse_statuses("discovered,research_queue") == ["discovered", "research_queue"]
    assert research_module._parse_statuses(["candidate", " rejected "]) == ["candidate", "rejected"]
    assert research_module._parse_statuses(None) is None
    assert research_module._parse_idea_ids("abc,def") == ["abc", "def"]


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
    assert validator._config["start_date"] == "2012-01-01"
    assert validator._config["end_date"] == "2025-12-31"
