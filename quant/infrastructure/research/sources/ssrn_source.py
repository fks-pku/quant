from typing import Any, Dict, List

from quant.domain.ports import ResearchSource


class SSRNSource(ResearchSource):
    @property
    def source_name(self) -> str:
        return "ssrn"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        return []
