from typing import Dict, List, Optional

from quant.domain.ports import ResearchSource
from quant.features.research.discovery import SourceHub, hash_strategy_text
from quant.features.research.models import RawStrategy


class StrategyScout:
    def __init__(
        self,
        sources: Optional[Dict[str, ResearchSource]] = None,
        source_hub: Optional[SourceHub] = None,
    ):
        self._source_hub = source_hub or SourceHub(sources or {})

    def search(self, sources: Optional[List[str]] = None, max_results: int = 10) -> List[RawStrategy]:
        return self._source_hub.search(sources=sources, max_results=max_results)

    @staticmethod
    def hash_strategy(raw: RawStrategy) -> str:
        return hash_strategy_text(raw.title, raw.description)
