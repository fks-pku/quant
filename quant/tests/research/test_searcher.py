import pytest
from unittest.mock import MagicMock
from quant.research.agents.searcher import SearcherAgent
from quant.research.sources.base import SourceAdapter
from typing import List, Dict, Any


class DummySource(SourceAdapter):
    @property
    def name(self):
        return "dummy"

    async def search(self, keywords: List[str], max_results: int) -> List[Dict[str, Any]]:
        return [
            {"title": "Test Strategy", "url": "http://example.com/1", "description": "A test", "published_date": "2024-01-01", "metadata": {}}
        ]

    async def fetch_full_content(self, url: str) -> str:
        return "full content"


class ErrorSource(SourceAdapter):
    @property
    def name(self):
        return "error"

    async def search(self, keywords, max_results):
        raise ConnectionError("Source down")

    async def fetch_full_content(self, url):
        return ""


@pytest.mark.asyncio
async def test_searcher_returns_raw_ideas():
    mock_llm = MagicMock()
    sources = [DummySource()]
    agent = SearcherAgent(mock_llm, sources)
    ideas = await agent.search(topic="momentum", max_ideas=5)
    assert len(ideas) >= 1
    assert ideas[0].title == "Test Strategy"


@pytest.mark.asyncio
async def test_searcher_handles_source_errors():
    mock_llm = MagicMock()
    sources = [ErrorSource()]
    agent = SearcherAgent(mock_llm, sources)
    ideas = await agent.search(topic="test")
    assert len(ideas) == 0


@pytest.mark.asyncio
async def test_searcher_deduplicates():
    mock_llm = MagicMock()
    sources = [DummySource(), DummySource()]
    agent = SearcherAgent(mock_llm, sources)
    ideas = await agent.search(topic="test")
    assert len(ideas) == 1


def test_keyword_generation():
    mock_llm = MagicMock()
    agent = SearcherAgent(mock_llm, [])
    assert agent._generate_keywords("mean reversion") == ["mean", "reversion"]
    default = agent._generate_keywords(None)
    assert len(default) > 0
