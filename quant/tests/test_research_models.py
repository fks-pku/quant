import pytest
from dataclasses import asdict
from quant.features.research.models import RawStrategy, EvaluationReport, ResearchConfig, ResearchResult


def test_raw_strategy_creation():
    rs = RawStrategy(
        title="Test Strategy",
        description="A test description",
        source="arxiv",
        source_url="https://arxiv.org/abs/1234",
    )
    assert rs.title == "Test Strategy"
    assert rs.source == "arxiv"


def test_evaluation_report_creation():
    er = EvaluationReport(
        suitability_score=7.5,
        complexity_score=4.0,
        data_requirement="medium",
        daily_adaptable=True,
        estimated_edge=0.10,
        recommended_symbols=["AAPL", "SPY"],
        strategy_type="momentum",
        summary="Good momentum strategy",
    )
    assert er.suitability_score == 7.5
    assert er.daily_adaptable is True


def test_research_config_defaults():
    cfg = ResearchConfig()
    assert cfg.auto_run is False
    assert cfg.interval_days == 7
    assert cfg.evaluation_threshold == 6.0


def test_research_result_creation():
    rr = ResearchResult(discovered=5, evaluated=3, integrated=1, backtested=1, promoted_auto=0, rejected=1, errors=[])
    assert rr.discovered == 5
    assert rr.errors == []
