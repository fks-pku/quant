import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from quant.domain.ports import ResearchSource
from quant.infrastructure.research.sources.rate_limiter import RateLimiter


logger = logging.getLogger(__name__)


class ArxivSource(ResearchSource):
    def __init__(
        self,
        category: str = "q-fin.TR",
        base_url: str = "http://export.arxiv.org/api/query",
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.category = category
        self.base_url = base_url
        self.rate_limiter = rate_limiter or RateLimiter()

    @property
    def source_name(self) -> str:
        return "arxiv"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        search_query = query.get("query") or f"cat:{self.category}"
        params = urllib.parse.urlencode(
            {
                "search_query": search_query,
                "start": 0,
                "max_results": max_results,
                "sortBy": query.get("sort_by", "submittedDate"),
                "sortOrder": query.get("sort_order", "descending"),
            }
        )
        url = f"{self.base_url}?{params}"
        try:
            self.rate_limiter.wait(self.source_name)
            with urllib.request.urlopen(url, timeout=30) as response:
                xml_text = response.read().decode("utf-8")
            return self._parse_xml(xml_text)
        except Exception as exc:
            logger.warning("arXiv search failed: %s", exc)
            return []

    def _parse_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)
        results: List[Dict[str, Any]] = []
        for entry in root.findall("atom:entry", namespace):
            title = entry.findtext("atom:title", default="", namespaces=namespace).strip()
            summary = entry.findtext("atom:summary", default="", namespaces=namespace).strip()
            source_url = entry.findtext("atom:id", default="", namespaces=namespace).strip()
            authors = [
                author.findtext("atom:name", default="", namespaces=namespace).strip()
                for author in entry.findall("atom:author", namespace)
            ]
            published_date = entry.findtext("atom:published", default="", namespaces=namespace).strip()
            if title and source_url:
                results.append(
                    {
                        "title": title,
                        "description": summary,
                        "source": self.source_name,
                        "source_url": source_url,
                        "authors": ", ".join(author for author in authors if author) or None,
                        "published_date": published_date or None,
                    }
                )
        return results
