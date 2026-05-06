import logging
from typing import Dict, List, Optional

from quant.domain.ports import ResearchSource
from quant.features.research.discovery.dedup import hash_strategy_text
from quant.features.research.models import RawStrategy


logger = logging.getLogger(__name__)


class SourceHub:
    def __init__(self, sources: Dict[str, ResearchSource]):
        self._sources = dict(sources)

    def search(self, sources: Optional[List[str]] = None, max_results: int = 10) -> List[RawStrategy]:
        selected_sources = sources or list(self._sources.keys())
        results: List[RawStrategy] = []
        seen = set()

        for source_name in selected_sources:
            source = self._sources.get(source_name)
            if source is None:
                continue
            try:
                payloads = source.search({}, max_results=max_results)
            except Exception as exc:
                logger.warning("Source %s search failed: %s", source_name, exc)
                continue

            for payload in payloads:
                raw = self._to_raw_strategy(payload)
                if raw is None:
                    continue
                strategy_hash = hash_strategy_text(raw.title, raw.description)
                if strategy_hash in seen:
                    continue
                seen.add(strategy_hash)
                results.append(raw)

        return results

    def _to_raw_strategy(self, payload: dict) -> Optional[RawStrategy]:
        title = payload.get("title")
        description = payload.get("description")
        source = payload.get("source")
        source_url = payload.get("source_url")
        if not title or description is None or not source or not source_url:
            return None
        return RawStrategy(
            title=str(title),
            description=str(description),
            source=str(source),
            source_url=str(source_url),
            authors=payload.get("authors"),
            published_date=payload.get("published_date"),
        )
