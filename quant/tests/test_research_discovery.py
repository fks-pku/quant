import logging
from unittest.mock import MagicMock

import pytest

from quant.features.research.discovery.source_hub import SourceHub
from quant.features.research.discovery.dedup import deduplicate
from quant.features.research.models import RawStrategy


def _make_raw(**overrides):
    defaults = dict(
        title="Test Strategy",
        description="A test strategy description",
        source="test",
        source_url="http://example.com",
        authors=None,
        published_date=None,
    )
    defaults.update(overrides)
    return RawStrategy(**defaults)


class TestSourceHubNormalize:
    def test_dict_normalized_to_raw_strategy(self):
        source = MagicMock()
        source.search.return_value = [
            {
                "title": "Momentum Factor",
                "description": "Cross-sectional momentum",
                "source_url": "http://arxiv.org/abs/1234",
                "authors": "Smith",
                "published_date": "2025-01-01",
            }
        ]
        hub = SourceHub({"test": source})
        results = hub.search(source_names=["test"])
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, RawStrategy)
        assert r.title == "Momentum Factor"
        assert r.source == "test"
        assert r.source_url == "http://arxiv.org/abs/1234"
        assert r.authors == "Smith"
        assert r.published_date == "2025-01-01"

    def test_missing_optional_fields_become_none(self):
        source = MagicMock()
        source.search.return_value = [
            {"title": "Minimal", "description": "desc"}
        ]
        hub = SourceHub({"s": source})
        results = hub.search(source_names=["s"])
        r = results[0]
        assert r.authors is None
        assert r.published_date is None

    def test_source_name_used_as_fallback(self):
        source = MagicMock()
        source.search.return_value = [
            {"title": "T", "description": "D", "source_url": ""}
        ]
        hub = SourceHub({"my_source": source})
        results = hub.search(source_names=["my_source"])
        assert results[0].source == "my_source"


class TestSourceHubUnknownSource:
    def test_unknown_source_name_skipped(self, caplog):
        hub = SourceHub({"arxiv": MagicMock()})
        with caplog.at_level(logging.WARNING):
            results = hub.search(source_names=["nonexistent"])
        assert results == []
        assert "Unknown source" in caplog.text


class TestSourceHubExceptionHandling:
    def test_adapter_exception_logged_no_crash(self, caplog):
        source = MagicMock()
        source.search.side_effect = ConnectionError("timeout")
        hub = SourceHub({"broken": source})
        with caplog.at_level(logging.WARNING):
            results = hub.search(source_names=["broken"])
        assert results == []
        assert "failed" in caplog.text

    def test_mixed_sources_partial_results(self):
        good = MagicMock()
        good.search.return_value = [{"title": "Good", "description": "d"}]
        bad = MagicMock()
        bad.search.side_effect = RuntimeError("fail")
        hub = SourceHub({"good": good, "bad": bad})
        results = hub.search(source_names=["good", "bad"])
        assert len(results) == 1
        assert results[0].title == "Good"


class TestSourceHubDefaultSources:
    def test_search_all_sources_when_none_specified(self):
        s1 = MagicMock()
        s1.search.return_value = [{"title": "A", "description": "d"}]
        s2 = MagicMock()
        s2.search.return_value = [{"title": "B", "description": "d"}]
        hub = SourceHub({"s1": s1, "s2": s2})
        results = hub.search()
        assert len(results) == 2


class TestDeduplicate:
    def test_exact_duplicates_removed(self):
        strategies = [
            _make_raw(title="Same", description="same desc"),
            _make_raw(title="Same", description="same desc"),
        ]
        result = deduplicate(strategies)
        assert len(result) == 1

    def test_case_insensitive_dedup(self):
        strategies = [
            _make_raw(title="Momentum", description="desc"),
            _make_raw(title="momentum", description="desc"),
        ]
        result = deduplicate(strategies)
        assert len(result) == 1

    def test_different_strategies_kept(self):
        strategies = [
            _make_raw(title="Alpha", description="desc a"),
            _make_raw(title="Beta", description="desc b"),
        ]
        result = deduplicate(strategies)
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        assert deduplicate([]) == []

    def test_whitespace_stripped_before_dedup(self):
        strategies = [
            _make_raw(title="  Momentum  ", description="  desc  "),
            _make_raw(title="Momentum", description="desc"),
        ]
        result = deduplicate(strategies)
        assert len(result) == 1
