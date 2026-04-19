import pytest
from unittest.mock import MagicMock
from datetime import datetime
from pathlib import Path

from quant.research.agents.coder import CoderAgent
from quant.research.models import ScoredIdea, RawIdea

VALID_CODE = '''from quant.strategies.base import Strategy
from quant.strategies.registry import strategy

@strategy("TestAutoStrategy")
class TestAutoStrategy(Strategy):
    def __init__(self, config=None):
        super().__init__("TestAutoStrategy")
    def on_data(self, context, data):
        pass
'''

VALID_CONFIG = "name: TestAutoStrategy\nparameters: {}\n"


def _make_idea(title="TestIdea"):
    return ScoredIdea(
        id="scored-id",
        raw_idea=RawIdea(
            id="raw-id", source="arxiv", source_url="http://x",
            title=title, description="desc",
            discovered_at=datetime.now()
        ),
        feasibility_score=80,
        academic_rigor=70,
        backtestability=75,
        compatibility=80,
        novelty=60,
        implementation_plan="Use RSI factor",
        suggested_factors=["rsi"],
        suggested_params={"lookback": [10, 30]},
        risk_assessment="",
        scored_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_coder_generates_valid_code(tmp_path):
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {"code": VALID_CODE, "config_yaml": VALID_CONFIG}

    agent = CoderAgent(mock_llm, strategies_dir=tmp_path / "strategies")
    idea = _make_idea()
    candidate = await agent.implement(idea)

    assert candidate.strategy_name.startswith("Auto")
    assert Path(candidate.code_path).exists()
    assert Path(candidate.config_path).exists()
    content = Path(candidate.code_path).read_text()
    assert "@strategy" in content


@pytest.mark.asyncio
async def test_coder_handles_invalid_code(tmp_path):
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {"code": "def broken(", "config_yaml": "invalid"}

    agent = CoderAgent(mock_llm, strategies_dir=tmp_path / "strategies")
    idea = _make_idea()
    candidate = await agent.implement(idea)

    assert candidate.registered is False
    assert Path(candidate.code_path).exists()


@pytest.mark.asyncio
async def test_coder_handles_llm_failure(tmp_path):
    mock_llm = MagicMock()
    mock_llm.analyze.side_effect = Exception("LLM down")

    agent = CoderAgent(mock_llm, strategies_dir=tmp_path / "strategies")
    idea = _make_idea()
    candidate = await agent.implement(idea)

    assert candidate.registered is False


def test_validate_code_accepts_valid():
    agent = CoderAgent(MagicMock())
    assert agent._validate_code("x = 1") is True


def test_validate_code_rejects_invalid():
    agent = CoderAgent(MagicMock())
    assert agent._validate_code("def broken(") is False
