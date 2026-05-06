from abc import ABC, abstractmethod
from typing import Any, Dict


class ResearchArtifactStore(ABC):
    @abstractmethod
    def save_json(self, run_id: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save_table(self, run_id: str, name: str, table: Any) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def load_artifact(self, artifact_id: str) -> Any:
        raise NotImplementedError
