import re
from typing import Any, Dict, Mapping, Optional

from quant.features.research.models import EvaluationReport, RawStrategy, StrategySpec


_DEFAULT_FORMULAS: Dict[str, Dict[str, Any]] = {
    "momentum_close_return": {
        "strategy_type": "momentum",
        "lookback_days": 20,
        "horizon_days": 5,
        "required_fields": ["close"],
    },
    "mean_reversion_close_to_ma": {
        "strategy_type": "mean_reversion",
        "lookback_days": 20,
        "horizon_days": 5,
        "required_fields": ["close"],
    },
    "volatility_breakout_atr": {
        "strategy_type": "breakout",
        "lookback_days": 20,
        "horizon_days": 5,
        "required_fields": ["high", "low", "close"],
    },
}


class StrategySpecBuilder:
    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.config = dict(config or {})
        formulas = self.config.get("formulas")
        self.formulas = dict(_DEFAULT_FORMULAS if formulas is None else formulas)

    def build(self, raw: RawStrategy, report: EvaluationReport) -> StrategySpec:
        strategy_id = self._strategy_id(raw.title)
        universe = list(report.recommended_symbols or self.config.get("default_universe", []))
        strategy_type = str(report.strategy_type or "unknown").strip().lower()
        manual_types = {str(item).strip().lower() for item in self.config.get("manual_spec_strategy_types", [])}

        if not universe:
            return self._spec(strategy_id, strategy_type, "", [], 0, 0, [], "missing_universe", "no recommended symbols")

        formula_key, formula = self._formula_for_type(strategy_type)
        if formula is None:
            default_supported = strategy_type in {item["strategy_type"] for item in _DEFAULT_FORMULAS.values()}
            if strategy_type in manual_types:
                status = "needs_manual_spec"
                reason = f"manual specification required for strategy_type={strategy_type}"
            else:
                status = "missing_formula" if default_supported else "unsupported_type"
                reason = f"no approved formula for strategy_type={strategy_type}"
            return self._spec(strategy_id, strategy_type, "", universe, 0, 0, [], status, reason)

        return self._spec(
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            signal_formula_key=formula_key,
            universe=universe,
            horizon_days=int(formula.get("horizon_days", self.config.get("horizon_days", 5))),
            lookback_days=int(formula.get("lookback_days", self.config.get("lookback_days", 20))),
            required_fields=list(formula.get("required_fields", self._required_fields(formula_key))),
            status="ready",
            reason="",
        )

    def _formula_for_type(self, strategy_type: str) -> tuple[str, Optional[Mapping[str, Any]]]:
        for key, formula in self.formulas.items():
            if str(formula.get("strategy_type", "")).strip().lower() == strategy_type:
                return key, formula
        return "", None

    def _spec(
        self,
        strategy_id: str,
        strategy_type: str,
        signal_formula_key: str,
        universe: list[str],
        horizon_days: int,
        lookback_days: int,
        required_fields: list[str],
        status: str,
        reason: str,
    ) -> StrategySpec:
        return StrategySpec(
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            signal_formula_key=signal_formula_key,
            universe=universe,
            horizon_days=horizon_days,
            lookback_days=lookback_days,
            execution_lag_days=int(self.config.get("execution_lag_days", 1)),
            required_fields=required_fields,
            status=status,
            reason=reason,
        )

    @staticmethod
    def _required_fields(formula_key: str) -> list[str]:
        return list(_DEFAULT_FORMULAS.get(formula_key, {}).get("required_fields", ["close"]))

    @staticmethod
    def _strategy_id(title: str) -> str:
        hyphen_replaced = title.replace("-", " ")
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", hyphen_replaced)
        return re.sub(r"\s+", "_", cleaned.strip()).lower()
