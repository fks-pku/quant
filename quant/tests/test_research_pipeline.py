import json
import shutil
import uuid
from pathlib import Path

import pytest

from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.models import EvaluationReport, RawStrategy, ResearchConfig
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
