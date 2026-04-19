import pytest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime
from pathlib import Path

from quant.research.coordinator import ResearchCoordinator
from quant.research.models import RawIdea
from quant.core.events import EventBus

VALID_STRATEGY_CODE = '''from quant.strategies.base import Strategy
from quant.strategies.registry import strategy

@strategy("PipelineTestAuto")
class PipelineTestAuto(Strategy):
    def __init__(self, symbols=None, config=None):
        super().__init__("PipelineTestAuto")
        self._symbols = symbols or ["AAPL"]
    @property
    def symbols(self):
        return self._symbols
    def on_start(self, context):
        super().on_start(context)
    def on_data(self, context, data):
        pass
    def on_after_trading(self, context, trading_date):
        pass
    def on_stop(self, context):
        pass
'''

VALID_CONFIG_YAML = "name: PipelineTestAuto\nparameters:\n  symbols: [AAPL]\n"


class MockSource:
    @property
    def name(self):
        return "mock"

    async def search(self, keywords, max_results):
        return [
            {
                "title": "Mean Reversion Alpha",
                "url": "http://example.com/paper1",
                "description": "A mean reversion strategy using intraday Z-score signals",
                "published_date": "2024-01-15",
                "metadata": {"authors": ["Smith"]},
            }
        ]

    async def fetch_full_content(self, url):
        return "Full paper content"


class EmptySource:
    @property
    def name(self):
        return "empty"

    async def search(self, keywords, max_results):
        return []

    async def fetch_full_content(self, url):
        return ""


@pytest.mark.asyncio
async def test_full_pipeline_with_mocked_llm(tmp_path):
    event_bus = EventBus()

    mock_llm = MagicMock()

    call_count = [0]

    def mock_analyze(prompt, context):
        call_count[0] += 1
        if "feasibility_score" in prompt or "Evaluate this strategy" in prompt:
            return {
                "feasibility_score": 85,
                "academic_rigor": 75,
                "backtestability": 80,
                "compatibility": 85,
                "novelty": 70,
                "implementation_plan": "Buy when Z-score < -2, sell when Z-score > 2",
                "suggested_factors": ["mean_reversion", "bollinger"],
                "suggested_params": {"lookback": [10, 30], "z_threshold": [1.5, 3.0]},
                "risk_assessment": "May underperform in trending markets",
            }
        elif "Generate a trading strategy" in prompt or "code" in prompt.lower():
            return {
                "code": VALID_STRATEGY_CODE,
                "config_yaml": VALID_CONFIG_YAML,
            }
        elif "Sharpe Ratio" in prompt or "backtest results" in prompt.lower():
            return {
                "llm_analysis": "Strategy shows reasonable performance",
                "recommendation": "watchlist",
                "recommendation_reasoning": "Needs more data for adoption",
                "comparison": {},
            }
        return {}

    mock_llm.analyze.side_effect = mock_analyze

    coordinator = ResearchCoordinator(
        event_bus=event_bus,
        llm_adapter=mock_llm,
        data_provider=None,
        backtest_config={"backtest": {"slippage_bps": 5}},
        config={
            "search": {"max_concurrent_sources": 1},
            "analysis": {"min_feasibility_score": 60},
            "evaluation": {"symbols": ["AAPL"]},
        },
    )

    coordinator._searcher.sources = [MockSource()]
    coordinator._coder.strategies_dir = tmp_path / "strategies"

    with patch.object(coordinator, "_save_reports"):
        task = coordinator.start_research(topic="mean reversion", max_ideas=1)

        await asyncio.sleep(3)

    assert task.status in ("COMPLETED", "EVALUATING", "ERROR"), f"Unexpected status: {task.status}"
    assert len(task.ideas) >= 1, f"Expected at least 1 idea, got {len(task.ideas)}"
    assert call_count[0] >= 2, f"LLM should have been called at least twice, got {call_count[0]}"

    if task.status == "COMPLETED":
        assert len(task.candidates) >= 1, f"Expected at least 1 candidate, got {len(task.candidates)}"
        assert len(task.reports) >= 1, f"Expected at least 1 report, got {len(task.reports)}"


@pytest.mark.asyncio
async def test_pipeline_handles_empty_search():
    event_bus = EventBus()
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {}

    coordinator = ResearchCoordinator(
        event_bus=event_bus,
        llm_adapter=mock_llm,
        config={
            "search": {"max_concurrent_sources": 1},
            "analysis": {"min_feasibility_score": 60},
            "evaluation": {"symbols": ["AAPL"]},
        },
    )

    coordinator._searcher.sources = [EmptySource()]

    with patch.object(coordinator, "_save_reports"):
        task = coordinator.start_research(topic="test", max_ideas=1)

        await asyncio.sleep(3)

    assert task.status in ("COMPLETED", "ANALYZING", "ERROR"), f"Status: {task.status}"
    assert len(task.ideas) == 0
