import time
import random
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any, List, Dict
from xml.etree import ElementTree as ET

from quant.features.research.models import RawStrategy
from quant.features.research.discovery.quality import attach_discovery_quality, discovery_score

logger = logging.getLogger(__name__)


class SourceAdapter(ABC):
    @abstractmethod
    def search(self, max_results: int = 10) -> List[RawStrategy]:
        ...


class ArxivAdapter(SourceAdapter):
    def __init__(self, category: str = "q-fin.TR"):
        self.category = category
        self.base_url = "http://export.arxiv.org/api/query"

    def search(self, max_results: int = 10) -> List[RawStrategy]:
        url = f"{self.base_url}?search_query=cat:{self.category}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        try:
            import requests

            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return self._parse_xml(resp.text)
        except Exception as e:
            logger.warning(f"arXiv search failed: {e}")
            return []

    def _parse_xml(self, xml_text: str) -> List[RawStrategy]:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)
        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", default="", namespaces=ns).strip()
            summary = entry.findtext("atom:summary", default="", namespaces=ns).strip()
            url = entry.findtext("atom:id", default="", namespaces=ns).strip()
            author_el = entry.find("atom:author", ns)
            authors = author_el.findtext("atom:name", default="", namespaces=ns).strip() if author_el is not None else ""
            published = entry.findtext("atom:published", default="", namespaces=ns).strip()
            if title:
                results.append(RawStrategy(title=title, description=summary, source="arxiv", source_url=url, authors=authors, published_date=published))
        return results


class SSRNAdapter(SourceAdapter):
    def search(self, max_results: int = 10) -> List[RawStrategy]:
        logger.warning("SSRN adapter not yet implemented")
        return []


class StrategyScout:
    def __init__(self, config: Dict[str, Any] = None):
        self._config = config or {}
        self._adapters: Dict[str, SourceAdapter] = {
            "arxiv": ArxivAdapter(),
            "ssrn": SSRNAdapter(),
        }
        self._source_hub = None
        self._hub_sources = None

    @classmethod
    def from_source_hub(cls, source_hub, sources=None, config: Dict[str, Any] = None):
        instance = cls(config=config)
        instance._source_hub = source_hub
        instance._hub_sources = sources
        return instance

    def search(self, sources: List[str] = None, max_results: int = 10) -> List[RawStrategy]:
        if self._source_hub is not None:
            from quant.features.research.discovery.dedup import deduplicate
            raw = self._source_hub.search(source_names=self._hub_sources or sources, max_results=max_results)
            return self._rank_and_filter(deduplicate(raw))
        sources = sources or list(self._adapters.keys())
        all_results: List[RawStrategy] = []
        for source in sources:
            adapter = self._adapters.get(source)
            if not adapter:
                continue
            try:
                results = adapter.search(max_results=max_results)
                all_results.extend(attach_discovery_quality(raw, config=self._config) for raw in results)
                time.sleep(random.uniform(3, 5))
            except Exception as e:
                logger.warning(f"Source {source} search failed: {e}")
        from quant.features.research.discovery.dedup import deduplicate
        return self._rank_and_filter(deduplicate(all_results))

    @staticmethod
    def hash_strategy(raw: RawStrategy) -> str:
        text = f"{raw.title.lower().strip()}::{raw.description.lower().strip()[:200]}"
        return hashlib.md5(text.encode()).hexdigest()

    def _rank_and_filter(self, strategies: List[RawStrategy]) -> List[RawStrategy]:
        scored = [attach_discovery_quality(raw, config=self._config) for raw in strategies]
        scored = [raw for raw in scored if self._passes_configured_filters(raw)]
        min_score = float(self._config.get("min_discovery_score", 0.0) or 0.0)
        if min_score > 0:
            scored = [raw for raw in scored if discovery_score(raw) >= min_score]
        if self._config.get("rank_results", True):
            scored.sort(key=lambda raw: (-discovery_score(raw), raw.source, raw.title))
        return scored

    def _passes_configured_filters(self, raw: RawStrategy) -> bool:
        quality = (raw.metadata or {}).get("discovery_quality") or {}
        matched = set(quality.get("matched_terms") or [])
        risks = set(quality.get("risk_flags") or [])
        required = set(self._config.get("required_match_terms") or [])
        required_any = set(self._config.get("required_any_match_terms") or [])
        blocked_risks = set(self._config.get("blocked_risk_flags") or [])
        if required and not required.issubset(matched):
            return False
        if required_any and not matched.intersection(required_any):
            return False
        if blocked_risks and risks.intersection(blocked_risks):
            return False
        return True
