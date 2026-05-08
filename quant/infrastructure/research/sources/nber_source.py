import logging
from typing import Any, Dict, List

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)


class NBERSource(ResearchSource):
    @property
    def source_name(self) -> str:
        return "nber"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        try:
            import requests
            resp = requests.get(
                "https://www.nber.org/api/v1/working_page_listing/contentType/working_paper/_perPage/50/page/1?range=2020-2025",
                timeout=30,
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            data = resp.json() if "json" in ct else []
            return self._parse_response(data, max_results)
        except Exception as e:
            logger.warning(f"NBER search failed: {e}")
            return []

    def _parse_response(self, data, max_results) -> List[Dict[str, Any]]:
        results = []
        if isinstance(data, list):
            for item in data[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "description": item.get("abstract", ""),
                    "source": "nber",
                    "source_url": item.get("url", ""),
                    "authors": item.get("authors", ""),
                    "published_date": item.get("release_date", ""),
                })
        return results
