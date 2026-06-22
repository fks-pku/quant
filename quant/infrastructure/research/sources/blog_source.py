import logging
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)

_DEFAULT_FEEDS = (
    "https://quantocracy.com/feed/",
    "https://alphaarchitect.com/feed/",
)


class BlogSource(ResearchSource):
    def __init__(self, feeds=None, timeout: float = 20.0, source_name: str = "blog"):
        self._feeds = list(feeds or _DEFAULT_FEEDS)
        self._timeout = timeout
        self._source_name = source_name

    @property
    def source_name(self) -> str:
        return self._source_name

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        query_text = self._query_text(query)
        try:
            import requests
            for feed in self._feeds:
                response = requests.get(feed, timeout=self._timeout, headers={"User-Agent": "QuantResearchBot/1.0"})
                response.raise_for_status()
                results.extend(self._parse_feed(response.text, feed, query_text, max_results - len(results)))
                if len(results) >= max_results:
                    break
        except Exception as e:
            logger.warning(f"Blog source search failed: {e}")
        return results[:max_results]

    def _parse_feed(self, xml_text: str, feed_url: str, query_text: str, max_results: int) -> List[Dict[str, Any]]:
        if max_results <= 0:
            return []
        root = ET.fromstring(xml_text)
        items = root.findall(".//item")
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//atom:entry", ns)
        results = []
        for item in items:
            title = self._find_text(item, ("title", "{http://www.w3.org/2005/Atom}title"))
            description = self._find_text(item, ("description", "summary", "{http://www.w3.org/2005/Atom}summary"))
            link = self._find_text(item, ("link", "{http://www.w3.org/2005/Atom}link"))
            if not link:
                atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.attrib.get("href", "") if atom_link is not None else ""
            haystack = f"{title} {description}".lower()
            if query_text and not any(token in haystack for token in query_text.split()):
                continue
            results.append({
                "title": title,
                "description": self._clean_html(description)[:800],
                "source": self._source_name,
                "source_url": link or feed_url,
                "authors": self._find_text(item, ("author", "creator", "{http://www.w3.org/2005/Atom}author")),
                "published_date": self._find_text(item, ("pubDate", "published", "{http://www.w3.org/2005/Atom}published")),
                "metadata": {"feed_url": feed_url, "source_family": "blog"},
            })
            if len(results) >= max_results:
                break
        return results

    def _query_text(self, query: Dict[str, Any]) -> str:
        if not isinstance(query, dict):
            return ""
        for key in ("query", "q", "keywords", "text"):
            value = query.get(key)
            if value:
                text = " ".join(str(item) for item in value) if isinstance(value, (list, tuple)) else str(value)
                return " ".join(text.lower().split())
        return "momentum factor daily alpha"

    def _find_text(self, item, names) -> str:
        for name in names:
            found = item.find(name)
            if found is not None:
                return " ".join("".join(found.itertext()).split())
        return ""

    def _clean_html(self, value: str) -> str:
        import re
        return " ".join(re.sub(r"<[^>]+>", " ", value or "").split())
