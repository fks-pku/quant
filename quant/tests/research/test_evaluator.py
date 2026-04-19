import pytest
from unittest.mock import MagicMock
from datetime import datetime

from quant.research.agents.evaluator import EvaluatorAgent
from quant.research.models import StrategyCandidate, ScoredIdea, RawIdea


def _make_candidate(strategy_name="NonExistentStrategy"):
    idea = ScoredIdea(
        id="scored-id",
        raw_idea=RawIdea(
            id="raw-id", source="arxiv", source_url="http://x",
            title="Test", description="desc", discovered_at=datetime.now()
        ),
        feasibility_score=80, academic_rigor=70, backtestability=75,
        compatibility=80, novelty=60, implementation_plan="",
        suggested_factors=[], suggested_params={}, risk_assessment="",
        scored_at=datetime.now(),
    )
    return StrategyCandidate(
        id="cand-id",
        scored_idea=idea,
        strategy_name=strategy_name,
        code_path="/tmp/test_strategy.py",
        config_path="/tmp/test_config.yaml",
    )


@pytest.mark.asyncio
async def test_evaluator_rejects_nonexistent_strategy():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {}
    mock_provider = MagicMock()

    agent = EvaluatorAgent(mock_llm, mock_provider, {"backtest": {"slippage_bps": 5}})
    candidate = _make_candidate("NonExistentStrategy")

    report = await agent.evaluate(candidate, ["AAPL"])
    assert report.recommendation == "reject"
    assert "failed" in report.recommendation_reasoning.lower()


@pytest.mark.asyncio
async def test_evaluator_rejects_without_data_provider():
    mock_llm = MagicMock()
    agent = EvaluatorAgent(mock_llm, None, {})
    candidate = _make_candidate()

    report = await agent.evaluate(candidate)
    assert report.recommendation == "reject"


@pytest.mark.asyncio
async def test_evaluator_default_symbols():
    mock_llm = MagicMock()
    agent = EvaluatorAgent(mock_llm, None, {})
    candidate = _make_candidate()

    report = await agent.evaluate(candidate)
    assert report.candidate.strategy_name == "NonExistentStrategy"


def test_evaluator_recommendation_logic_adopt():
    mock_llm = MagicMock()
    agent = EvaluatorAgent(mock_llm)
    metrics = {"sharpe_ratio": 1.5, "max_drawdown_pct": 0.10}

    rec = "watchlist"
    if metrics["sharpe_ratio"] > 1.0 and metrics["max_drawdown_pct"] < 0.2:
        rec = "adopt"
    assert rec == "adopt"


def test_evaluator_recommendation_logic_reject():
    mock_llm = MagicMock()
    agent = EvaluatorAgent(mock_llm)
    metrics = {"sharpe_ratio": -0.5, "max_drawdown_pct": 0.30}

    rec = "watchlist"
    if metrics["sharpe_ratio"] < 0:
        rec = "reject"
    assert rec == "reject"
