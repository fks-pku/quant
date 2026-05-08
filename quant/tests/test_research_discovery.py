import logging
import sys
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


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestSSRNSource:
    def test_ssrn_html_results_are_parsed(self, monkeypatch):
        from quant.infrastructure.research.sources import ssrn_source
        from quant.infrastructure.research.sources.ssrn_source import SSRNSource

        calls = []
        sleeps = []
        long_description = "A" * 550
        html = f"""
        <html>
          <body>
            <div class="result-item">
              <h3><a class="title" href="/sol3/papers.cfm?abstract_id=123456">Cross-Sectional Momentum in Equity Markets</a></h3>
              <div class="authors">Jane Doe; John Smith</div>
              <div class="published-date">Posted: 12 Jan 2025</div>
              <div class="abstract-text">{long_description}</div>
            </div>
            <div class="result-item">
              <h3><a class="title" href="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=789">Skipped Result</a></h3>
              <div class="authors">Other Author</div>
              <div class="abstract-text">Other description</div>
            </div>
          </body>
        </html>
        """

        class FakeRequests:
            @staticmethod
            def get(url, params=None, headers=None, timeout=None):
                calls.append({
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                })
                return _FakeResponse(html)

        monkeypatch.setitem(sys.modules, "requests", FakeRequests)
        monkeypatch.setattr(ssrn_source.random, "uniform", lambda low, high: 3.5)
        monkeypatch.setattr(ssrn_source.time, "sleep", sleeps.append)
        source = SSRNSource(
            _base_url="https://papers.ssrn.com/sol3/results.cfm",
            _timeout=7,
            _max_retries=0,
        )

        results = source.search({"query": "momentum"}, max_results=1)

        assert len(results) == 1
        assert results[0] == {
            "title": "Cross-Sectional Momentum in Equity Markets",
            "description": long_description[:500],
            "source": "ssrn",
            "source_url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=123456",
            "authors": "Jane Doe; John Smith",
            "published_date": "Posted: 12 Jan 2025",
        }
        assert calls[0]["url"] == "https://papers.ssrn.com/sol3/results.cfm"
        assert calls[0]["params"]["txtKeywords"] == "momentum"
        assert calls[0]["headers"]["User-Agent"] == "QuantResearchBot/1.0 (academic use)"
        assert calls[0]["timeout"] == 7
        assert sleeps == [3.5]

    def test_ssrn_request_error_returns_empty_and_logs_warning(self, monkeypatch, caplog):
        from quant.infrastructure.research.sources import ssrn_source
        from quant.infrastructure.research.sources.ssrn_source import SSRNSource

        calls = []
        sleeps = []

        class FakeRequests:
            @staticmethod
            def get(url, params=None, headers=None, timeout=None):
                calls.append(url)
                raise TimeoutError("timeout")

        monkeypatch.setitem(sys.modules, "requests", FakeRequests)
        monkeypatch.setattr(ssrn_source.random, "uniform", lambda low, high: 3.0)
        monkeypatch.setattr(ssrn_source.time, "sleep", sleeps.append)
        source = SSRNSource(_max_retries=1, _retry_backoff=0.25)

        with caplog.at_level(logging.WARNING):
            results = source.search({"query": "momentum"})

        assert results == []
        assert len(calls) == 2
        assert sleeps == [3.0, 0.25, 3.0]
        assert "SSRN search failed" in caplog.text

    def test_ssrn_blocked_page_returns_empty_and_logs_warning(self, monkeypatch, caplog):
        from quant.infrastructure.research.sources import ssrn_source
        from quant.infrastructure.research.sources.ssrn_source import SSRNSource

        sleeps = []

        class FakeRequests:
            @staticmethod
            def get(url, params=None, headers=None, timeout=None):
                return _FakeResponse("<html><title>Access Denied</title>captcha required</html>")

        monkeypatch.setitem(sys.modules, "requests", FakeRequests)
        monkeypatch.setattr(ssrn_source.random, "uniform", lambda low, high: 4.0)
        monkeypatch.setattr(ssrn_source.time, "sleep", sleeps.append)
        source = SSRNSource(_max_retries=0)

        with caplog.at_level(logging.WARNING):
            results = source.search({"query": "momentum"})

        assert results == []
        assert sleeps == [4.0]
        assert "blocked" in caplog.text.lower()

    def test_ssrn_retries_429_and_5xx_with_exponential_backoff(self, monkeypatch):
        from quant.infrastructure.research.sources import ssrn_source
        from quant.infrastructure.research.sources.ssrn_source import SSRNSource

        calls = []
        sleeps = []
        html = """
        <html><body>
          <a href="/sol3/papers.cfm?abstract_id=123">Recovered SSRN Result</a>
          <div class="abstract-text">Recovered description</div>
        </body></html>
        """
        responses = [
            _FakeResponse("too many requests", status_code=429),
            _FakeResponse("server unavailable", status_code=503),
            _FakeResponse(html, status_code=200),
        ]

        class FakeRequests:
            @staticmethod
            def get(url, params=None, headers=None, timeout=None):
                calls.append(url)
                return responses.pop(0)

        monkeypatch.setitem(sys.modules, "requests", FakeRequests)
        monkeypatch.setattr(ssrn_source.random, "uniform", lambda low, high: 3.0)
        monkeypatch.setattr(ssrn_source.time, "sleep", sleeps.append)
        source = SSRNSource(_max_retries=2, _retry_backoff=0.5)

        results = source.search({"query": "momentum"}, max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == "Recovered SSRN Result"
        assert len(calls) == 3
        assert sleeps == [3.0, 0.5, 3.0, 1.0, 3.0]


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
