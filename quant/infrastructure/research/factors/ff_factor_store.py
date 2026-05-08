import logging
from typing import Any, Dict, List

from quant.domain.ports.factor_data import FactorData

logger = logging.getLogger(__name__)


class FFFactorStore(FactorData):
    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        logger.info("FF factor store not yet populated")
        return None

    def list_factors(self) -> List[Dict[str, Any]]:
        return []
