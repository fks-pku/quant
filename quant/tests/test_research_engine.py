import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.models import ResearchConfig, RawStrategy, EvaluationReport


def test_run_full_pipeline_mock():
    mock_scout = MagicMock()
    mock_scout.search.return_value = [RawStrategy("T", "D", "arxiv", "url")]
    mock_scout.hash_strategy.return_value = "abc123"

    mock_eval = MagicMock()
    mock_eval.evaluate.return_value = EvaluationReport(
        8.0, 3.0, "medium", True, 0.1, ["AAPL"], "momentum", "Good"
    )

    mock_integrator = MagicMock()
    mock_integrator.integrate.return_value = "test_strategy"

    mock_pool = MagicMock()

    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False),
        scout=mock_scout,
        evaluator=mock_eval,
        integrator=mock_integrator,
        pool=mock_pool,
    )

    result = engine.run_full_pipeline(sources=["arxiv"])
    assert result.discovered == 1
    assert result.evaluated == 1
    assert result.integrated == 1
