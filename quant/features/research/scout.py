import hashlib
from typing import Dict, List, Optional

from quant.domain.ports import ResearchSource
from quant.features.research.discovery import SourceHub
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
        text = f"{raw.title.lower().strip()}::{raw.description.lower().strip()[:200]}"
        return hashlib.md5(text.encode()).hexdigest()
