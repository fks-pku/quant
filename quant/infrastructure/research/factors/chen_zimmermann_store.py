import logging
from typing import Any, Dict, List

from quant.domain.ports.factor_data import FactorData

logger = logging.getLogger(__name__)


class ChenZimmermannStore(FactorData):
    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        logger.info("Chen-Zimmermann factor store not yet populated")
        return None

    def list_factors(self) -> List[Dict[str, Any]]:
        return []
