import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from typing import List, Dict, Any

from quant.research.sources.base import SourceAdapter

ARXIV_API = "http://export.arxiv.org/api/query"


class ArxivAdapter(SourceAdapter):
    def __init__(self, lookback_days: int = 7, rate_limit_seconds: float = 3.0):
        self.lookback_days = lookback_days
        self.rate_limit = rate_limit_seconds
        self._last_request_time = 0.0

    @property
    def name(self) -> str:
        return "arxiv"

    async def _rate_limited_request(self, url: str, session: aiohttp.ClientSession) -> str:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.rate_limit:
            await asyncio.sleep(self.rate_limit - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()
        async with session.get(url) as response:
            return await response.text()

    async def search(self, keywords: List[str], max_results: int) -> List[Dict[str, Any]]:
        keywords_str = "+".join(keywords)
        url = (f"{ARXIV_API}?search_query=all:{keywords_str}"
               f"&start=0&max_results={max_results}"
               f"&sortBy=submittedDate&sortOrder=descending")

        results = []
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                xml_text = await self._rate_limited_request(url, session)
                root = ET.fromstring(xml_text)

                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    title_el = entry.find("atom:title", ns)
                    summary_el = entry.find("atom:summary", ns)
                    published_el = entry.find("atom:published", ns)
                    link_el = entry.find("atom:id", ns)

                    result = {
                        "title": title_el.text.strip().replace("\n", " ") if title_el is not None else "",
                        "url": link_el.text if link_el is not None else "",
                        "description": summary_el.text.strip() if summary_el is not None else "",
                        "published_date": published_el.text[:10] if published_el is not None else None,
                        "metadata": {
                            "authors": [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
                        }
                    }
                    results.append(result)

                    if len(results) >= max_results:
                        break
            except Exception:
                pass

        return results

    async def fetch_full_content(self, url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url) as response:
                    return await response.text()
            except Exception:
                return ""
