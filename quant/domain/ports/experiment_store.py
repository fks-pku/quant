from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional


class ExperimentStore(ABC):
    @abstractmethod
    def start_run(self, strategy_id: str, metadata: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def record_metrics(self, run_id: str, metrics: Iterable[Dict[str, Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def complete_run(self, run_id: str, status: str, error: str = "") -> None:
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_runs(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_metrics(self, run_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError
