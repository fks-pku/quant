import logging
from typing import Any, Callable, Dict, List, Optional

from quant.domain.ports.research_store import ResearchStore

logger = logging.getLogger(__name__)


class CandidatePool:
    def __init__(
        self,
        strategy_registry: Optional[Dict[str, Any]] = None,
        on_state_change: Optional[Callable] = None,
        research_store: Optional[ResearchStore] = None,
    ):
        self._registry = strategy_registry if strategy_registry is not None else {}
        self._on_state_change = on_state_change
        self.research_store = research_store

    def list_candidates(self) -> List[Dict]:
        if self.research_store is not None:
            return self.research_store.list_by_status("candidate")
        return [info for info in self._registry.values() if info.get("status") == "candidate"]

    def list_rejected(self) -> List[Dict]:
        if self.research_store is not None:
            return self.research_store.list_by_status("rejected")
        return [info for info in self._registry.values() if info.get("status") == "rejected"]

    def promote(self, strategy_id: str) -> bool:
        if self.research_store is not None:
            info = self.research_store.get_candidate(strategy_id)
            if info is None or info.get("status") != "candidate":
                logger.warning(f"Cannot promote {strategy_id}: status is {info.get('status') if info else None}")
                return False
            if self.research_store.update_status(strategy_id, "paused"):
                self._set_registry_status(strategy_id, "paused")
                if self._on_state_change:
                    self._on_state_change()
                logger.info(f"Promoted {strategy_id} from candidate to paused")
                return True
        for name, info in self._registry.items():
            if info["id"] == strategy_id:
                if info.get("status") != "candidate":
                    logger.warning(f"Cannot promote {strategy_id}: status is {info.get('status')}")
                    return False
                info["status"] = "paused"
                if self._on_state_change:
                    self._on_state_change()
                logger.info(f"Promoted {strategy_id} from candidate to paused")
                return True
        logger.warning(f"Strategy {strategy_id} not found for promotion")
        return False

    def reject(self, strategy_id: str, reason: str = "") -> bool:
        if self.research_store is not None:
            info = self.research_store.get_candidate(strategy_id)
            if info is None or info.get("status") != "candidate":
                logger.warning(f"Cannot reject {strategy_id}: status is {info.get('status') if info else None}")
                return False
            if self.research_store.update_status(strategy_id, "rejected", reason=reason):
                self._set_registry_status(strategy_id, "rejected", reason=reason)
                if self._on_state_change:
                    self._on_state_change()
                logger.info(f"Rejected {strategy_id}: {reason}")
                return True
        for name, info in self._registry.items():
            if info["id"] == strategy_id:
                if info.get("status") != "candidate":
                    logger.warning(f"Cannot reject {strategy_id}: status is {info.get('status')}")
                    return False
                info["status"] = "rejected"
                meta = info.setdefault("research_meta", {})
                meta["rejection_reason"] = reason
                if self._on_state_change:
                    self._on_state_change()
                logger.info(f"Rejected {strategy_id}: {reason}")
                return True
        logger.warning(f"Strategy {strategy_id} not found for rejection")
        return False

    def get_research_meta(self, strategy_id: str) -> Optional[Dict]:
        if self.research_store is not None:
            info = self.research_store.get_candidate(strategy_id)
            return info.get("research_meta") if info else None
        for info in self._registry.values():
            if info["id"] == strategy_id:
                return info.get("research_meta")
        return None

    def _set_registry_status(self, strategy_id: str, status: str, reason: str = "") -> None:
        for info in self._registry.values():
            if info.get("id") == strategy_id:
                info["status"] = status
                if reason:
                    info.setdefault("research_meta", {})["rejection_reason"] = reason
                return
