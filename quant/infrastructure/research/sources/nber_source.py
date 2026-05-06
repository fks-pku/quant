import logging
import urllib.request
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from quant.domain.ports import ResearchSource
from quant.infrastructure.research.sources.rate_limiter import RateLimiter


logger = logging.getLogger(__name__)


class NBERSource(ResearchSource):
    def __init__(
        self,
        feed_url: str = "https://www.nber.org/papers/rss",
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.feed_url = feed_url
        self.rate_limiter = rate_limiter or RateLimiter()

    @property
    def source_name(self) -> str:
        return "nber"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        keyword = str(query.get("keyword", "") or "").lower().strip()
        try:
            self.rate_limiter.wait(self.source_name)
            with urllib.request.urlopen(self.feed_url, timeout=30) as response:
                xml_text = response.read().decode("utf-8")
            return self._parse_rss(xml_text, keyword, max_results)
        except Exception as exc:
            logger.warning("NBER search failed: %s", exc)
            return []

    def _parse_rss(self, xml_text: str, keyword: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_text)
        results: List[Dict[str, Any]] = []
        for item in root.findall(".//item"):
            title = item.findtext("title", default="").strip()
            description = item.findtext("description", default="").strip()
            source_url = item.findtext("link", default="").strip()
            published_date = item.findtext("pubDate", default="").strip()
            searchable = f"{title} {description}".lower()
            if keyword and keyword not in searchable:
                continue
            if title and source_url:
                results.append(
                    {
                        "title": title,
                        "description": description,
                        "source": self.source_name,
                        "source_url": source_url,
                        "authors": None,
                        "published_date": published_date or None,
                    }
                )
            if len(results) >= max_results:
                break
        return results
