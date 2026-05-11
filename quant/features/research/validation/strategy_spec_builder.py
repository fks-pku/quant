import re
from typing import Any, Dict, List, Optional

from quant.features.research.models import StrategySpec, EvaluationReport, RawStrategy

_FORMULA_MAP = {
    "momentum": {
        "formula_key": "momentum_close_return",
        "strategy_type": "momentum",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "mean_reversion": {
        "formula_key": "mean_reversion_close_to_ma",
        "strategy_type": "mean_reversion",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "breakout": {
        "formula_key": "volatility_breakout_atr",
        "strategy_type": "breakout",
        "required_fields": ["high", "low", "close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
}

_SUPPORTED_TYPES = {"momentum", "mean_reversion", "breakout"}


class StrategySpecBuilder:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._formula_map = config.get("formulas", _FORMULA_MAP) if config else _FORMULA_MAP

    def build(self, raw: RawStrategy, report: EvaluationReport, universe: Optional[List[str]] = None) -> StrategySpec:
        strategy_type = report.strategy_type
        strategy_id = _strategy_id(raw.title)

        if strategy_type not in _SUPPORTED_TYPES:
            return StrategySpec(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                signal_formula_key="",
                universe=universe or report.recommended_symbols,
                horizon_days=0,
                lookback_days=0,
                execution_lag_days=0,
                required_fields=[],
                status="unsupported_type",
                reason=f"Strategy type '{strategy_type}' not in supported types",
            )

        formula = self._formula_map.get(strategy_type)
        if formula is None:
            return StrategySpec(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                signal_formula_key="",
                universe=universe or report.recommended_symbols,
                horizon_days=0,
                lookback_days=0,
                execution_lag_days=0,
                required_fields=[],
                status="missing_formula",
                reason=f"No formula mapping for '{strategy_type}'",
            )

        return StrategySpec(
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            signal_formula_key=formula["formula_key"],
            universe=universe or report.recommended_symbols,
            horizon_days=formula["horizon_days"],
            lookback_days=formula["lookback_days"],
            execution_lag_days=formula["execution_lag_days"],
            required_fields=formula["required_fields"],
            status="ready",
        )


def _strategy_id(title: str) -> str:
    hyphen_replaced = title.replace("-", " ")
    cleaned = re.sub(r"[^a-zA-Z0-9_\s]", "", hyphen_replaced)
    underscored = re.sub(r"\s+", "_", cleaned.strip()).lower()
    return re.sub(r"_+", "_", underscored).strip("_")[:50].strip("_") or "strategy_candidate"
