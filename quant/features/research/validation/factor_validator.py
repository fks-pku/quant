import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from quant.features.research.models import StrategySpec, ValidationReport
from quant.features.research.validation.cross_sectional import (
    compute_cross_sectional_ic,
    compute_fama_macbeth_tstat,
    compute_ic_decay,
    compute_icir,
    detect_market,
)
from quant.features.research.validation.ff_decomposition import decompose_alpha

logger = logging.getLogger(__name__)


class FactorValidator:
    def __init__(
        self,
        market_data_port: Any,
        config: Optional[Dict[str, Any]] = None,
        factor_data_port: Any = None,
    ):
        self._market_data = market_data_port
        self._config = config or {}
        self._factor_data = factor_data_port
        self._min_obs = self._config.get("min_observations", 252)
        self._exec_lag = self._config.get("execution_lag_days", 1)
        self._min_stocks = self._config.get("min_stocks", 20)
        self._min_cs_dates = max(100, self._config.get("min_cross_sectional_dates", 100))
        self._factor_validation_enabled = bool(self._config.get("factor_validation_enabled", False))

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

        symbols = self._resolve_universe(spec)
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

        if {"symbol", "date"}.issubset(data.columns):
            return self._validate_cross_sectional(spec, data, compute_signal)

        return self._validate_single_symbol(spec, data, compute_signal)

    def _resolve_universe(self, spec: StrategySpec) -> List[str]:
        fallback = list(spec.universe)
        if not fallback:
            return fallback
        if not hasattr(self._market_data, "get_universe_symbols"):
            return fallback
        try:
            universe = self._market_data.get_universe_symbols(detect_market(fallback[0]))
            return list(universe) if universe else fallback
        except Exception as e:
            logger.warning(f"Universe fetch failed: {e}")
            return fallback

    def _validate_single_symbol(self, spec: StrategySpec, data: pd.DataFrame, compute_signal: Any) -> ValidationReport:
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

    def _validate_cross_sectional(self, spec: StrategySpec, data: pd.DataFrame, compute_signal: Any) -> ValidationReport:
        frame = data.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values(["date", "symbol"])

        signal_matrix = compute_signal(spec.signal_formula_key, frame, spec.lookback_days)
        if signal_matrix is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"])

        close_prices = frame.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
        signal_matrix = signal_matrix.shift(self._exec_lag)
        forward_returns = close_prices.pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)
        daily_ic = compute_cross_sectional_ic(
            signal_matrix,
            forward_returns,
            min_stocks=self._min_stocks,
        )
        valid_ic = daily_ic.dropna()
        if len(valid_ic) < self._min_cs_dates:
            return self._error_report(
                spec,
                [f"Insufficient valid cross-sectional dates: {len(valid_ic)} < {self._min_cs_dates}"],
            )

        rank_ic = float(valid_ic.mean())
        rank_ic_ir = compute_icir(valid_ic)
        ttest = stats.ttest_1samp(valid_ic, 0.0) if len(valid_ic) > 1 else None
        rank_ic_p = float(ttest.pvalue) if ttest is not None and not pd.isna(ttest.pvalue) else 1.0
        long_short_series = self._long_short_series(signal_matrix, forward_returns)
        ff, factor_errors = self._decompose_against_factors(long_short_series)

        return ValidationReport(
            strategy_id=spec.strategy_id,
            status="validated",
            rank_ic=rank_ic,
            rank_ic_ir=rank_ic_ir,
            ic_decay=compute_ic_decay(
                signal_matrix,
                close_prices,
                horizons=[1, 5, 10, 21],
                execution_lag=self._exec_lag,
                min_stocks=self._min_stocks,
            ),
            fdr_adjusted_p=rank_ic_p,
            fdr_significant=rank_ic_p < 0.05,
            ff_alpha_monthly=ff["alpha_monthly"],
            ff_alpha_tstat=ff["tstat"],
            ff_r2=ff["r2"],
            long_short_spread=float(long_short_series.mean()) if not long_short_series.empty else 0.0,
            hit_rate=float((valid_ic > 0).mean()),
            data_start=str(close_prices.index[0]),
            data_end=str(close_prices.index[-1]),
            n_observations=len(valid_ic),
            fama_macbeth_tstat=compute_fama_macbeth_tstat(
                signal_matrix,
                forward_returns,
                min_stocks=self._min_stocks,
            ),
            errors=factor_errors,
        )

    def _long_short_spread(self, signals: pd.DataFrame, forward_returns: pd.DataFrame) -> float:
        spreads = self._long_short_series(signals, forward_returns)
        return float(spreads.mean()) if not spreads.empty else 0.0

    def _long_short_series(self, signals: pd.DataFrame, forward_returns: pd.DataFrame) -> pd.Series:
        common_index = signals.index.intersection(forward_returns.index)
        common_columns = signals.columns.intersection(forward_returns.columns)
        spreads = []
        dates = []
        for date in common_index:
            paired = pd.concat(
                [
                    signals.loc[date, common_columns].rename("signal"),
                    forward_returns.loc[date, common_columns].rename("return"),
                ],
                axis=1,
            ).dropna()
            if len(paired) < self._min_stocks:
                continue
            high = paired["signal"].quantile(0.8)
            low = paired["signal"].quantile(0.2)
            long_returns = paired.loc[paired["signal"] >= high, "return"]
            short_returns = paired.loc[paired["signal"] <= low, "return"]
            if long_returns.empty or short_returns.empty:
                continue
            spreads.append(float(long_returns.mean() - short_returns.mean()))
            dates.append(date)
        return pd.Series(spreads, index=pd.to_datetime(dates)).sort_index() if spreads else pd.Series(dtype=float)

    def _decompose_against_factors(self, strategy_returns: pd.Series) -> tuple[Dict[str, float], List[str]]:
        zeros = {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
        if self._factor_data is None or strategy_returns.empty:
            errors = ["factor_data_unavailable"] if self._factor_validation_enabled else []
            return zeros, errors
        try:
            factors = self._factor_data.get_factors(
                ["MKT", "SMB", "HML", "RMW", "CMA", "Mom", "RF"],
                str(strategy_returns.index.min().date()),
                str(strategy_returns.index.max().date()),
            )
            if factors is None:
                errors = ["factor_data_unavailable"] if self._factor_validation_enabled else []
                return zeros, errors
            return decompose_alpha(strategy_returns, factors), []
        except Exception as e:
            logger.warning(f"Factor decomposition unavailable: {e}")
            errors = ["factor_data_unavailable"] if self._factor_validation_enabled else []
            return zeros, errors

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
