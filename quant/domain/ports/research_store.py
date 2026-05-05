"""Research persistence port."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ResearchStore(ABC):
    @abstractmethod
    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        ...

    @abstractmethod
    def has_seen(self, strategy_hash: str) -> bool:
        ...

    @abstractmethod
    def mark_seen(self, strategy_hash: str, raw: Any) -> None:
        ...

    @abstractmethod
    def write_discoveries(self, raw_strategies: Iterable[Any]) -> None:
        ...

    @abstractmethod
    def write_evaluations(self, evaluations: Iterable[Tuple[Any, Any, str, str]]) -> None:
        ...

    @abstractmethod
    def save_run_result(self, result: Any) -> None:
        ...
