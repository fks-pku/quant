import logging
from typing import Any, Dict, List, Optional

from quant.features.research.models import RawStrategy

logger = logging.getLogger(__name__)


class SourceHub:
    def __init__(self, sources: Dict[str, Any]):
        self._sources = sources

    def search(self, source_names: Optional[List[str]] = None, max_results: int = 10) -> List[RawStrategy]:
        names = source_names or list(self._sources.keys())
        results = []
        for name in names:
            source = self._sources.get(name)
            if source is None:
                logger.warning(f"Unknown source: {name}")
                continue
            try:
                raw_dicts = source.search(query={}, max_results=max_results)
                for d in raw_dicts:
                    results.append(self._normalize(name, d))
            except Exception as e:
                logger.warning(f"Source {name} search failed: {e}")
        return results

    def _normalize(self, source_name: str, raw: Dict[str, Any]) -> RawStrategy:
        return RawStrategy(
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            source=raw.get("source", source_name),
            source_url=raw.get("source_url", ""),
            authors=raw.get("authors"),
            published_date=raw.get("published_date"),
        )
