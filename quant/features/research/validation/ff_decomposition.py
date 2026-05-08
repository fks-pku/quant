import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def decompose_alpha(returns: Any, factor_data: Any) -> Dict[str, float]:
    logger.info("FF decomposition not yet implemented")
    return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
