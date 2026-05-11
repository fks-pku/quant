import logging
import re
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
        sort_by = self._sort_by(query)
        sort_order = self._sort_order(query)
        url = f"{self._base_url}?search_query={quote(search_query)}&start=0&max_results={max_results}&sortBy={sort_by}&sortOrder={sort_order}"
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
        category = self._category_for(query)
        raw_query = self._raw_query(query)
        if not raw_query:
            return f"cat:{category}"
        if self._looks_like_arxiv_query(raw_query):
            if "cat:" in raw_query:
                return raw_query
            return f"({raw_query}) AND cat:{category}"
        tokens = self._query_tokens(raw_query)
        if not tokens:
            return f"cat:{category}"
        return " AND ".join(f"all:{token}" for token in tokens) + f" AND cat:{category}"

    def _raw_query(self, query: Dict[str, Any]) -> str:
        if isinstance(query, dict):
            for key in ("search_query", "query", "q", "keywords", "text"):
                value = query.get(key)
                if value:
                    return " ".join(str(item) for item in value) if isinstance(value, (list, tuple)) else str(value)
        return ""

    def _category_for(self, query: Dict[str, Any]) -> str:
        if isinstance(query, dict) and query.get("category"):
            return str(query.get("category"))
        return self._category

    def _sort_by(self, query: Dict[str, Any]) -> str:
        if isinstance(query, dict) and query.get("sort_by"):
            return str(query.get("sort_by"))
        return "relevance" if self._raw_query(query) else "submittedDate"

    def _sort_order(self, query: Dict[str, Any]) -> str:
        if isinstance(query, dict) and query.get("sort_order"):
            return str(query.get("sort_order"))
        return "descending"

    def _looks_like_arxiv_query(self, text: str) -> bool:
        return bool(re.search(r"\b(all|ti|abs|au|cat):", text))

    def _query_tokens(self, text: str) -> List[str]:
        tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stopwords = {"and", "or", "the", "for", "with", "using", "to", "of", "in", "on"}
        return [token for token in tokens if token not in stopwords]
