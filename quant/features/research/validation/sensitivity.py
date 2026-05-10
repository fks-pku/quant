from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from quant.features.research.validation.cross_sectional import compute_cross_sectional_ic, detect_market
from quant.features.research.validation.signal_library import adjusted_price_matrix, compute_signal


@dataclass(frozen=True)
class SensitivityReport:
    strategy_id: str
    base_ic: float
    base_params: Dict[str, Any]
    parameter_combinations: List[Dict[str, Any]]
    ic_surface: List[float]
    is_stable: bool
    max_degradation_pct: float
    optimal_params: Dict[str, Any]


def run_sensitivity_sweep(
    spec: Any,
    market_data_port: Any,
    base_params: Dict[str, Any],
    config: Dict[str, Any],
) -> SensitivityReport:
    cfg = config or {}
    lookback_grid = _grid_values(cfg, "lookback_grid", "sensitivity_lookback_grid", base_params.get("lookback_days", 20))
    horizon_grid = _grid_values(cfg, "horizon_grid", "sensitivity_horizon_grid", base_params.get("horizon_days", 5))
    combinations = [
        {"lookback_days": int(lookback), "horizon_days": int(horizon)}
        for lookback in lookback_grid
        for horizon in horizon_grid
    ]
    if not combinations:
        combinations = [dict(base_params)]
    base_combo = {
        "lookback_days": int(base_params.get("lookback_days", getattr(spec, "lookback_days", 20))),
        "horizon_days": int(base_params.get("horizon_days", getattr(spec, "horizon_days", 5))),
    }
    if not any(_same_params(params, base_combo) for params in combinations):
        combinations.append(base_combo)

    data = _load_market_frame(spec, market_data_port, cfg)
    ic_surface = [_compute_ic(spec, data, params, cfg) for params in combinations]
    base_ic = _base_ic(spec, data, base_combo, combinations, ic_surface, cfg)
    optimal_index = _optimal_index(ic_surface)
    optimal_ic = ic_surface[optimal_index] if ic_surface else 0.0
    informative = _has_informative_surface(ic_surface)
    max_degradation_pct = _max_degradation_pct(ic_surface, optimal_ic) if informative else 100.0
    threshold = float(cfg.get("sensitivity_max_degradation_pct", 30.0))

    return SensitivityReport(
        strategy_id=spec.strategy_id,
        base_ic=base_ic,
        base_params=dict(base_combo),
        parameter_combinations=combinations,
        ic_surface=ic_surface,
        is_stable=informative and max_degradation_pct <= threshold,
        max_degradation_pct=max_degradation_pct,
        optimal_params=dict(combinations[optimal_index]) if combinations else dict(base_params),
    )


def _grid_values(config: Dict[str, Any], primary: str, fallback: str, default: Any) -> List[Any]:
    values = config.get(primary)
    if values is None:
        values = config.get(fallback)
    if values is None:
        values = [default]
    return list(values)


def _same_params(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        int(left.get("lookback_days", -1)) == int(right.get("lookback_days", -2))
        and int(left.get("horizon_days", -1)) == int(right.get("horizon_days", -2))
    )


def _load_market_frame(spec: Any, market_data_port: Any, config: Dict[str, Any]) -> pd.DataFrame:
    symbols = list(getattr(spec, "universe", []) or [])
    if symbols and hasattr(market_data_port, "get_universe_symbols"):
        try:
            resolved = market_data_port.get_universe_symbols(detect_market(symbols[0]))
            if resolved is not None:
                symbols = list(resolved)
        except Exception:
            pass
    if not symbols:
        return pd.DataFrame()
    try:
        raw = market_data_port.get_daily_bars(
            symbols=symbols,
            start=str(config.get("sensitivity_start", "2019-01-01")),
            end=str(config.get("sensitivity_end", "2024-12-31")),
        )
    except Exception:
        return pd.DataFrame()
    if raw is None:
        return pd.DataFrame()
    frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    if frame.empty:
        return frame
    frame = frame.copy()
    if "date" not in frame.columns:
        frame = frame.reset_index().rename(columns={"index": "date"})
    if "symbol" not in frame.columns and len(symbols) == 1:
        frame["symbol"] = symbols[0]
    return frame


def _compute_ic(spec: Any, data: pd.DataFrame, params: Dict[str, Any], config: Dict[str, Any]) -> float:
    try:
        required = {"symbol", "date", "close"}
        if data.empty or not required.issubset(data.columns):
            return 0.0
        frame = data.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values(["date", "symbol"])
        if frame.empty:
            return 0.0
        lookback = int(params.get("lookback_days", getattr(spec, "lookback_days", 20)))
        horizon = int(params.get("horizon_days", getattr(spec, "horizon_days", 5)))
        execution_lag = int(config.get("execution_lag_days", getattr(spec, "execution_lag_days", 1)))
        min_stocks = int(config.get("min_stocks", 20))
        signals = compute_signal(spec.signal_formula_key, frame, lookback)
        if signals is None:
            return 0.0
        signals = signals.shift(execution_lag)
        prices = adjusted_price_matrix(frame, "close")
        forward_returns = prices.pct_change(horizon).shift(-horizon - execution_lag)
        daily_ic = compute_cross_sectional_ic(signals, forward_returns, min_stocks=min_stocks)
        valid = daily_ic.dropna()
        return float(valid.mean()) if not valid.empty else 0.0
    except Exception:
        return 0.0


def _base_ic(
    spec: Any,
    data: pd.DataFrame,
    base_params: Dict[str, Any],
    combinations: List[Dict[str, Any]],
    ic_surface: List[float],
    config: Dict[str, Any],
) -> float:
    for index, params in enumerate(combinations):
        if _same_params(params, base_params):
            return float(ic_surface[index])
    return _compute_ic(spec, data, base_params, config)


def _optimal_index(values: List[float]) -> int:
    if not values:
        return 0
    return max(range(len(values)), key=lambda index: abs(values[index]))


def _max_degradation_pct(values: List[float], optimal_ic: float) -> float:
    optimal_score = abs(optimal_ic)
    if not values or optimal_score <= 0:
        return 0.0
    degradations = [max(0.0, (optimal_score - abs(value)) / optimal_score * 100.0) for value in values]
    return float(max(degradations)) if degradations else 0.0


def _has_informative_surface(values: List[float]) -> bool:
    return any(abs(value) > 1e-12 for value in values)
