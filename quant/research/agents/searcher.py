import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any

from quant.research.models import RawIdea
from quant.research.sources.base import SourceAdapter


class SearcherAgent:
    def __init__(
        self,
        llm_adapter,
        sources: List[SourceAdapter],
        max_concurrent: int = 3,
    ):
        self.llm = llm_adapter
        self.sources = sources
        self.max_concurrent = max_concurrent

    def _generate_keywords(self, topic: str | None) -> List[str]:
        if topic:
            return topic.split()
        return ["quantitative", "trading", "strategy", "alpha", "factor"]

    async def _search_source(
        self,
        source: SourceAdapter,
        keywords: List[str],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        try:
            return await source.search(keywords, max_results)
        except Exception:
            return []

    async def search(
        self,
        topic: str | None = None,
        max_ideas: int = 20,
    ) -> List[RawIdea]:
        keywords = self._generate_keywords(topic)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def bounded_search(source: SourceAdapter):
            async with semaphore:
                return await self._search_source(source, keywords, max_ideas)

        tasks = [bounded_search(s) for s in self.sources]
        source_results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_ideas = []
        seen_titles = set()

        for result_set in source_results:
            if isinstance(result_set, Exception):
                continue
            for item in result_set:
                title = item.get("title", "")[:100]
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    raw_ideas.append(RawIdea(
                        id=str(uuid.uuid4()),
                        source=item.get("source", "unknown"),
                        source_url=item.get("url", ""),
                        title=title,
                        description=item.get("description", "")[:500],
                        published_date=item.get("published_date"),
                        metadata=item.get("metadata", {}),
                        discovered_at=datetime.now(),
                    ))

        return raw_ideas[:max_ideas]
