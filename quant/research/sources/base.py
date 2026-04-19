from abc import ABC, abstractmethod
from typing import List, Dict, Any


class SourceAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def search(self, keywords: List[str], max_results: int) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_full_content(self, url: str) -> str:
        pass
