import logging
from typing import Any, Dict, List

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)


class BlogSource(ResearchSource):
    @property
    def source_name(self) -> str:
        return "blog"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        logger.warning("Blog source not yet implemented")
        return []
