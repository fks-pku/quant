import logging
from typing import List, Dict, Optional

from quant.api.state.runtime import AVAILABLE_STRATEGIES, _save_strategy_state

logger = logging.getLogger(__name__)


class CandidatePool:
    def list_candidates(self) -> List[Dict]:
        return [info for info in AVAILABLE_STRATEGIES.values() if info.get("status") == "candidate"]

    def list_rejected(self) -> List[Dict]:
        return [info for info in AVAILABLE_STRATEGIES.values() if info.get("status") == "rejected"]

    def promote(self, strategy_id: str) -> bool:
        for name, info in AVAILABLE_STRATEGIES.items():
            if info["id"] == strategy_id:
                if info.get("status") != "candidate":
                    logger.warning(f"Cannot promote {strategy_id}: status is {info.get('status')}")
                    return False
                info["status"] = "paused"
                _save_strategy_state()
                logger.info(f"Promoted {strategy_id} from candidate to paused")
                return True
        logger.warning(f"Strategy {strategy_id} not found for promotion")
        return False

    def reject(self, strategy_id: str, reason: str = "") -> bool:
        for name, info in AVAILABLE_STRATEGIES.items():
            if info["id"] == strategy_id:
                if info.get("status") != "candidate":
                    logger.warning(f"Cannot reject {strategy_id}: status is {info.get('status')}")
                    return False
                info["status"] = "rejected"
                meta = info.setdefault("research_meta", {})
                meta["rejection_reason"] = reason
                _save_strategy_state()
                logger.info(f"Rejected {strategy_id}: {reason}")
                return True
        logger.warning(f"Strategy {strategy_id} not found for rejection")
        return False

    def get_research_meta(self, strategy_id: str) -> Optional[Dict]:
        for info in AVAILABLE_STRATEGIES.values():
            if info["id"] == strategy_id:
                return info.get("research_meta")
        return None
