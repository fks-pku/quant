import logging
from typing import Any, Dict, List, Optional

from quant.features.research.models import RawStrategy
from quant.features.research.discovery.quality import attach_discovery_quality

logger = logging.getLogger(__name__)


class SourceHub:
    def __init__(
        self,
        sources: Dict[str, Any],
        query_plan: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        quality_config: Optional[Dict[str, Any]] = None,
    ):
        self._sources = sources
        self._query_plan = query_plan or {}
        self._quality_config = quality_config or {}

    def search(self, source_names: Optional[List[str]] = None, max_results: int = 10) -> List[RawStrategy]:
        names = source_names or list(self._sources.keys())
        results = []
        for name in names:
            source = self._sources.get(name)
            if source is None:
                logger.warning(f"Unknown source: {name}")
                continue
            try:
                for query in self._queries_for(name):
                    raw_dicts = source.search(query=query, max_results=max_results)
                    for d in raw_dicts:
                        results.append(self._normalize(name, d, query))
            except Exception as e:
                logger.warning(f"Source {name} search failed: {e}")
        return results

    def _queries_for(self, source_name: str) -> List[Dict[str, Any]]:
        queries = self._query_plan.get(source_name)
        if not queries:
            return [{}]
        normalized = []
        for query in queries:
            if isinstance(query, dict):
                normalized.append(dict(query))
            elif query:
                normalized.append({"query": str(query)})
        return normalized or [{}]

    def _normalize(self, source_name: str, raw: Dict[str, Any], query: Optional[Dict[str, Any]] = None) -> RawStrategy:
        metadata = dict(raw.get("metadata") or {})
        metadata["query"] = dict(query or {})
        metadata["source_name"] = source_name
        strategy = RawStrategy(
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            source=raw.get("source", source_name),
            source_url=raw.get("source_url", ""),
            authors=raw.get("authors"),
            published_date=raw.get("published_date"),
            metadata=metadata,
        )
        return attach_discovery_quality(strategy, config=self._quality_config)
