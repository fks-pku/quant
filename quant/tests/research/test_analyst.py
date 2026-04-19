import pytest
from unittest.mock import MagicMock
from datetime import datetime

from quant.research.agents.analyst import AnalystAgent
from quant.research.models import RawIdea


def _make_idea(title="Test Idea"):
    return RawIdea(
        id="test-id",
        source="arxiv",
        source_url="http://arxiv.org/abs/2401.00001",
        title=title,
        description="A test strategy description",
        published_date="2024-01-15",
        metadata={},
        discovered_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_analyst_passes_high_score():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {
        "feasibility_score": 85,
        "academic_rigor": 70,
        "backtestability": 80,
        "compatibility": 90,
        "novelty": 60,
        "implementation_plan": "Use RSI factor for entry signals",
        "suggested_factors": ["rsi"],
        "suggested_params": {"lookback": [10, 30]},
        "risk_assessment": "May underperform in trending markets",
    }

    agent = AnalystAgent(mock_llm)
    ideas = [_make_idea("High Score Strategy")]
    scored = await agent.analyze(ideas, min_score=60)

    assert len(scored) == 1
    assert scored[0].feasibility_score == 85
    assert scored[0].suggested_factors == ["rsi"]


@pytest.mark.asyncio
async def test_analyst_filters_low_score():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {
        "feasibility_score": 30,
        "academic_rigor": 20,
        "backtestability": 25,
        "compatibility": 40,
        "novelty": 10,
        "implementation_plan": "Not feasible",
        "suggested_factors": [],
        "suggested_params": {},
        "risk_assessment": "Too theoretical",
    }

    agent = AnalystAgent(mock_llm)
    ideas = [_make_idea("Low Score Strategy")]
    scored = await agent.analyze(ideas, min_score=60)

    assert len(scored) == 0


@pytest.mark.asyncio
async def test_analyst_handles_llm_failure():
    mock_llm = MagicMock()
    mock_llm.analyze.side_effect = Exception("LLM down")

    agent = AnalystAgent(mock_llm)
    ideas = [_make_idea("Failed Analysis")]
    scored = await agent.analyze(ideas, min_score=60)

    assert len(scored) == 0


@pytest.mark.asyncio
async def test_analyst_processes_multiple_ideas():
    mock_llm = MagicMock()
    mock_llm.analyze.side_effect = [
        {"feasibility_score": 80, "academic_rigor": 70, "backtestability": 80, "compatibility": 80, "novelty": 70, "implementation_plan": "Plan A", "suggested_factors": [], "suggested_params": {}, "risk_assessment": ""},
        {"feasibility_score": 40, "academic_rigor": 30, "backtestability": 40, "compatibility": 50, "novelty": 20, "implementation_plan": "Plan B", "suggested_factors": [], "suggested_params": {}, "risk_assessment": ""},
    ]

    agent = AnalystAgent(mock_llm)
    ideas = [_make_idea("Idea A"), _make_idea("Idea B")]
    scored = await agent.analyze(ideas, min_score=60)

    assert len(scored) == 1
    assert scored[0].raw_idea.title == "Idea A"
