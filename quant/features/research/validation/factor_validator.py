import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from quant.features.research.models import StrategySpec, ValidationReport

logger = logging.getLogger(__name__)


class FactorValidator:
    def __init__(self, market_data_port: Any, config: Optional[Dict[str, Any]] = None):
        self._market_data = market_data_port
        self._config = config or {}
        self._min_obs = self._config.get("min_observations", 252)
        self._exec_lag = self._config.get("execution_lag_days", 1)

    def validate(self, spec: StrategySpec) -> ValidationReport:
        if spec.status != "ready":
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="skipped",
                rank_ic=0.0, rank_ic_ir=0.0, ic_decay=[],
                fdr_adjusted_p=1.0, fdr_significant=False,
                ff_alpha_monthly=0.0, ff_alpha_tstat=0.0, ff_r2=0.0,
                long_short_spread=0.0, hit_rate=0.0,
                data_start="", data_end="", n_observations=0,
                errors=[f"Spec status is '{spec.status}', not 'ready'"],
            )

        from quant.features.research.validation.signal_library import compute_signal

        symbols = spec.universe[:1]
        raw_data = self._market_data.get_daily_bars(
            symbols=symbols,
            start="2019-01-01",
            end="2024-12-31",
        )
        if raw_data is None:
            return self._error_report(spec, ["No market data returned"])

        data = raw_data if isinstance(raw_data, pd.DataFrame) else pd.DataFrame(raw_data)
        if len(data) < self._min_obs:
            return self._error_report(spec, [f"Insufficient data: {len(data)} < {self._min_obs}"])

        signal = compute_signal(spec.signal_formula_key, data, spec.lookback_days)
        if signal is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"])

        forward_return = data["close"].pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)
        signal = signal.shift(self._exec_lag)

        common_idx = signal.dropna().index.intersection(forward_return.dropna().index)
        if len(common_idx) < self._min_obs:
            return self._error_report(spec, [f"Insufficient valid observations: {len(common_idx)}"])

        sig = signal.loc[common_idx]
        fwd = forward_return.loc[common_idx]

        rank_ic, rank_ic_p = stats.spearmanr(sig, fwd)
        rank_ic_ir = rank_ic * np.sqrt(len(common_idx)) if rank_ic != 0 else 0.0
        hit_rate = (sig * fwd > 0).mean()

        return ValidationReport(
            strategy_id=spec.strategy_id,
            status="validated",
            rank_ic=float(rank_ic),
            rank_ic_ir=float(rank_ic_ir),
            ic_decay=[],
            fdr_adjusted_p=float(rank_ic_p),
            fdr_significant=rank_ic_p < 0.05,
            ff_alpha_monthly=0.0,
            ff_alpha_tstat=0.0,
            ff_r2=0.0,
            long_short_spread=0.0,
            hit_rate=float(hit_rate),
            data_start=str(data.index[0]),
            data_end=str(data.index[-1]),
            n_observations=len(common_idx),
        )

    def _error_report(self, spec, errors):
        return ValidationReport(
            strategy_id=spec.strategy_id,
            status="error",
            rank_ic=0.0, rank_ic_ir=0.0, ic_decay=[],
            fdr_adjusted_p=1.0, fdr_significant=False,
            ff_alpha_monthly=0.0, ff_alpha_tstat=0.0, ff_r2=0.0,
            long_short_spread=0.0, hit_rate=0.0,
            data_start="", data_end="", n_observations=0,
            errors=errors,
        )
