import re
from typing import Any, Dict, List, Optional

from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, StrategySpec, EvaluationReport, RawStrategy

_FORMULA_MAP = {
    "ashare_short_reversal_5d": {
        "formula_key": "ashare_short_reversal_5d",
        "strategy_type": "mean_reversion",
        "required_fields": ["close"],
        "lookback_days": 5,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "ashare_volume_exhaustion_reversal": {
        "formula_key": "ashare_volume_exhaustion_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["close", "volume"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "ashare_volume_dryup_pullback": {
        "formula_key": "ashare_volume_dryup_pullback",
        "strategy_type": "mean_reversion",
        "required_fields": ["close", "volume"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "ashare_lottery_demand_avoidance": {
        "formula_key": "ashare_lottery_demand_avoidance",
        "strategy_type": "factor",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "execution_lag_days": 1,
    },
    "ashare_low_volatility_defensive": {
        "formula_key": "ashare_low_volatility_defensive",
        "strategy_type": "factor",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "execution_lag_days": 1,
    },
    "ashare_gap_down_reversal": {
        "formula_key": "ashare_gap_down_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["open", "close"],
        "lookback_days": 2,
        "horizon_days": 3,
        "execution_lag_days": 1,
    },
    "ashare_volatility_scaled_reversal": {
        "formula_key": "ashare_volatility_scaled_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "ashare_liquidity_weighted_low_volatility": {
        "formula_key": "ashare_liquidity_weighted_low_volatility",
        "strategy_type": "factor",
        "required_fields": ["close", "turnover"],
        "lookback_days": 20,
        "horizon_days": 10,
        "execution_lag_days": 1,
    },
    "ashare_low_volatility_momentum": {
        "formula_key": "ashare_low_volatility_momentum",
        "strategy_type": "momentum",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 10,
        "execution_lag_days": 1,
    },
    "ashare_range_contraction_breakout": {
        "formula_key": "ashare_range_contraction_breakout",
        "strategy_type": "breakout",
        "required_fields": ["high", "low", "close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "ashare_gap_down_liquid_reversal": {
        "formula_key": "ashare_gap_down_liquid_reversal",
        "strategy_type": "mean_reversion",
        "required_fields": ["open", "close", "turnover"],
        "lookback_days": 20,
        "horizon_days": 3,
        "execution_lag_days": 1,
    },
    "ashare_turnover_stability_factor": {
        "formula_key": "ashare_turnover_stability_factor",
        "strategy_type": "factor",
        "required_fields": ["turnover"],
        "lookback_days": 20,
        "horizon_days": 10,
        "execution_lag_days": 1,
    },
    "joinquant_small_cap_ma_stop": {
        "formula_key": "joinquant_small_cap_size_factor",
        "strategy_type": "factor",
        "required_fields": ["close", "market_cap"],
        "lookback_days": 50,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_001": {
        "formula_key": "worldquant_alpha_001",
        "strategy_type": "worldquant_factor",
        "required_fields": ["close"],
        "lookback_days": 20,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_002": {
        "formula_key": "worldquant_alpha_002",
        "strategy_type": "worldquant_factor",
        "required_fields": ["volume", "open", "close"],
        "lookback_days": 6,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_003": {
        "formula_key": "worldquant_alpha_003",
        "strategy_type": "worldquant_factor",
        "required_fields": ["open", "volume"],
        "lookback_days": 10,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_004": {
        "formula_key": "worldquant_alpha_004",
        "strategy_type": "worldquant_factor",
        "required_fields": ["low"],
        "lookback_days": 9,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_006": {
        "formula_key": "worldquant_alpha_006",
        "strategy_type": "worldquant_factor",
        "required_fields": ["open", "volume"],
        "lookback_days": 10,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
    "worldquant_alpha_010": {
        "formula_key": "worldquant_alpha_010",
        "strategy_type": "worldquant_factor",
        "required_fields": ["close"],
        "lookback_days": 4,
        "horizon_days": 5,
        "execution_lag_days": 1,
    },
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

_SUPPORTED_TYPES = {"momentum", "mean_reversion", "breakout", "factor", "worldquant_factor"}


class StrategySpecBuilder:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._formula_map = config.get("formulas", _FORMULA_MAP) if config else _FORMULA_MAP
        self._default_universe = _a_share_symbols((config or {}).get("default_universe"))

    def build(self, raw: RawStrategy, report: EvaluationReport, universe: Optional[List[str]] = None) -> StrategySpec:
        worldquant_formula = _worldquant_formula_key(raw)
        metadata_formula = _metadata_formula_key(raw)
        strategy_type = "worldquant_factor" if worldquant_formula else report.strategy_type
        formula_lookup_key = worldquant_formula or metadata_formula or strategy_type
        strategy_id = _strategy_id(raw.title)
        resolved_universe = self._resolve_universe(universe, report)
        formula = self._formula_map.get(formula_lookup_key)
        if formula is not None:
            strategy_type = formula["strategy_type"]

        if strategy_type not in _SUPPORTED_TYPES:
            return StrategySpec(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                signal_formula_key="",
                universe=resolved_universe,
                horizon_days=0,
                lookback_days=0,
                execution_lag_days=0,
                required_fields=[],
                status="unsupported_type",
                reason=f"Strategy type '{strategy_type}' not in supported types",
            )

        if formula is None:
            return StrategySpec(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                signal_formula_key="",
                universe=resolved_universe,
                horizon_days=0,
                lookback_days=0,
                execution_lag_days=0,
                required_fields=[],
                status="missing_formula",
                reason=f"No formula mapping for '{formula_lookup_key}'",
            )

        return StrategySpec(
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            signal_formula_key=formula["formula_key"],
            universe=resolved_universe,
            horizon_days=formula["horizon_days"],
            lookback_days=formula["lookback_days"],
            execution_lag_days=formula["execution_lag_days"],
            required_fields=formula["required_fields"],
            status="ready",
        )

    def _resolve_universe(self, universe: Optional[List[str]], report: EvaluationReport) -> List[str]:
        explicit = _a_share_symbols(universe)
        if explicit:
            return explicit
        recommended = _a_share_symbols(report.recommended_symbols)
        if self._default_universe and (not recommended or recommended == DEFAULT_A_SHARE_SYMBOLS):
            return list(self._default_universe)
        return recommended or list(DEFAULT_A_SHARE_SYMBOLS)


def _strategy_id(title: str) -> str:
    hyphen_replaced = title.replace("-", " ")
    cleaned = re.sub(r"[^a-zA-Z0-9_\s]", "", hyphen_replaced)
    underscored = re.sub(r"\s+", "_", cleaned.strip()).lower()
    return re.sub(r"_+", "_", underscored).strip("_")[:50].strip("_") or "strategy_candidate"


def _a_share_universe(symbols: Optional[List[str]]) -> List[str]:
    cn_symbols = _a_share_symbols(symbols)
    return cn_symbols or list(DEFAULT_A_SHARE_SYMBOLS)


def _a_share_symbols(symbols: Optional[List[str]]) -> List[str]:
    return [str(symbol) for symbol in symbols or [] if re.fullmatch(r"\d{6}", str(symbol))]


def _worldquant_formula_key(raw: RawStrategy) -> str:
    metadata = raw.metadata or {}
    if str(raw.source).lower() != "worldquant101" and metadata.get("external_library") != "worldquant_101_formulaic_alphas":
        return ""
    alpha_number = metadata.get("alpha_number")
    try:
        alpha_number = int(alpha_number)
    except (TypeError, ValueError):
        return ""
    return f"worldquant_alpha_{alpha_number:03d}"


def _metadata_formula_key(raw: RawStrategy) -> str:
    metadata = raw.metadata or {}
    formula_key = str(metadata.get("formula_key", "") or "").strip()
    return formula_key
