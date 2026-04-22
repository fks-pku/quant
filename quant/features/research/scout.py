import time
import random
import hashlib
import logging
import requests
from abc import ABC, abstractmethod
from typing import List, Dict
from xml.etree import ElementTree as ET

from quant.features.research.models import RawStrategy

logger = logging.getLogger(__name__)


class SourceAdapter(ABC):
    @abstractmethod
    def search(self, max_results: int = 10) -> List[RawStrategy]:
        ...


class ArxivAdapter(SourceAdapter):
    def __init__(self, category: str = "q-fin.TR"):
        self.category = category
        self.base_url = "http://export.arxiv.org/api/query"

    def search(self, max_results: int = 10) -> List[RawStrategy]:
        url = f"{self.base_url}?search_query=cat:{self.category}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return self._parse_xml(resp.text)
        except Exception as e:
            logger.warning(f"arXiv search failed: {e}")
            return []

    def _parse_xml(self, xml_text: str) -> List[RawStrategy]:
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
                results.append(RawStrategy(title=title, description=summary, source="arxiv", source_url=url, authors=authors, published_date=published))
        return results


class SSRNAdapter(SourceAdapter):
    def search(self, max_results: int = 10) -> List[RawStrategy]:
        logger.warning("SSRN adapter not yet implemented")
        return []


class StrategyScout:
    def __init__(self):
        self._adapters: Dict[str, SourceAdapter] = {
            "arxiv": ArxivAdapter(),
            "ssrn": SSRNAdapter(),
        }

    def search(self, sources: List[str] = None, max_results: int = 10) -> List[RawStrategy]:
        sources = sources or list(self._adapters.keys())
        all_results: List[RawStrategy] = []
        for source in sources:
            adapter = self._adapters.get(source)
            if not adapter:
                continue
            try:
                results = adapter.search(max_results=max_results)
                all_results.extend(results)
                time.sleep(random.uniform(3, 5))
            except Exception as e:
                logger.warning(f"Source {source} search failed: {e}")
        return all_results

    @staticmethod
    def hash_strategy(raw: RawStrategy) -> str:
        text = f"{raw.title.lower().strip()}::{raw.description.lower().strip()[:200]}"
        return hashlib.md5(text.encode()).hexdigest()
