import logging
from typing import Any, Dict, List
from urllib.parse import quote
from xml.etree import ElementTree as ET

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)


class ArxivSource(ResearchSource):
    def __init__(self, category: str = "q-fin.TR"):
        self._category = category
        self._base_url = "http://export.arxiv.org/api/query"

    @property
    def source_name(self) -> str:
        return "arxiv"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        search_query = self._search_query(query)
        url = f"{self._base_url}?search_query={quote(search_query)}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        try:
            import requests
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return self._parse_xml(resp.text)
        except Exception as e:
            logger.warning(f"arXiv search failed: {e}")
            return []

    def _parse_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)
        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", default="", namespaces=ns).strip()
            summary = entry.findtext("atom:summary", default="", namespaces=ns).strip()
            url = entry.findtext("atom:id", default="", namespaces=ns).strip()
            author_el = entry.find("atom:author", ns)
            authors = author_el.findtext("atom:name", default="", namespaces=ns).strip() if author_el is not None else ""
            published = entry.findtext("atom:published", default="", namespaces=ns).strip()
            if title:
                results.append({
                    "title": title,
                    "description": summary,
                    "source": "arxiv",
                    "source_url": url,
                    "authors": authors,
                    "published_date": published,
                })
        return results

    def _search_query(self, query: Dict[str, Any]) -> str:
        text = ""
        if isinstance(query, dict):
            for key in ("query", "q", "keywords", "text"):
                value = query.get(key)
                if value:
                    text = " ".join(str(item) for item in value) if isinstance(value, (list, tuple)) else str(value)
                    break
        if not text:
            return f"cat:{self._category}"
        return f'all:"{text}" AND cat:{self._category}'
