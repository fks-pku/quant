from typing import Any, Dict, List

from quant.domain.ports import ResearchSource


class BlogSource(ResearchSource):
    @property
    def source_name(self) -> str:
        return "blog"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        return []
