from abc import ABC, abstractmethod
from typing import Any, Dict, List


class ResearchSource(ABC):
    @abstractmethod
    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def source_name(self) -> str:
        raise NotImplementedError
