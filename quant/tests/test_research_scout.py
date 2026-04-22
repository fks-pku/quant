import pytest
from unittest.mock import patch, MagicMock
from quant.features.research.scout import ArxivAdapter, StrategyScout
from quant.features.research.models import RawStrategy


def test_arxiv_adapter_search_mock():
    mock_xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Test Paper</title>
        <summary>Test summary</summary>
        <id>https://arxiv.org/abs/1234.5678</id>
        <author><name>John Doe</name></author>
        <published>2024-01-01T00:00:00Z</published>
      </entry>
    </feed>"""
    adapter = ArxivAdapter()
    with patch("quant.features.research.scout.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text=mock_xml)
        results = adapter.search(max_results=1)
        assert len(results) == 1
        assert results[0].title == "Test Paper"
        assert results[0].source == "arxiv"


def test_strategy_scout_search_all():
    scout = StrategyScout()
    with patch.object(scout._adapters["arxiv"], "search") as mock_arxiv:
        mock_arxiv.return_value = [RawStrategy("T", "D", "arxiv", "url")]
        results = scout.search(sources=["arxiv"], max_results=1)
        assert len(results) == 1
