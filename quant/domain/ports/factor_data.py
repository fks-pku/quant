from abc import ABC, abstractmethod
from typing import Any, Dict, List


class FactorData(ABC):
    @abstractmethod
    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def list_factors(self) -> List[Dict[str, Any]]:
        raise NotImplementedError
