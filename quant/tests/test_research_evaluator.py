import pytest
from unittest.mock import MagicMock
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.models import RawStrategy, EvaluationReport


def test_evaluate_strategy_mock_llm():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {
        "suitability_score": 8.0,
        "complexity_score": 3.0,
        "data_requirement": "medium",
        "daily_adaptable": True,
        "estimated_edge": 0.12,
        "recommended_symbols": ["AAPL", "SPY"],
        "strategy_type": "momentum",
        "summary": "Good strategy",
    }
    evaluator = StrategyEvaluator(llm_adapter=mock_llm)
    raw = RawStrategy(title="Momentum", description="Buy high sell higher", source="arxiv", source_url="url")
    report = evaluator.evaluate(raw)
    assert isinstance(report, EvaluationReport)
    assert report.suitability_score == 8.0
    assert report.daily_adaptable is True


def test_evaluate_strategy_llm_failure():
    mock_llm = MagicMock()
    mock_llm.analyze.side_effect = Exception("LLM error")
    evaluator = StrategyEvaluator(llm_adapter=mock_llm)
    raw = RawStrategy(title="Fail", description="...", source="arxiv", source_url="url")
    report = evaluator.evaluate(raw)
    assert report.suitability_score == 0.0
    assert report.data_requirement == "unknown"
