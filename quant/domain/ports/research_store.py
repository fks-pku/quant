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
    def upsert_hypothesis(self, info: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def get_hypothesis(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list_hypotheses(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def upsert_idea(self, raw: Any, status: str = "discovered", run_id: str = "", reason: str = "") -> None:
        ...

    @abstractmethod
    def list_ideas(self, status: Optional[Any] = None) -> List[Dict[str, Any]]:
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
    def write_initial_screening_table(self, rows: Iterable[Dict[str, Any]]) -> None:
        ...

    @abstractmethod
    def save_run_result(self, result: Any) -> None:
        ...
