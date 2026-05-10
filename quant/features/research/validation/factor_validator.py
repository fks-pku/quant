import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from quant.features.research.models import StrategySpec, ValidationReport
from quant.features.research.validation.cross_sectional import (
    compute_cross_sectional_ic,
    compute_fama_macbeth_tstat,
    compute_ic_decay,
    compute_icir,
    detect_market,
)
from quant.features.research.validation.ff_decomposition import decompose_alpha
from quant.features.research.validation.sensitivity import run_sensitivity_sweep

logger = logging.getLogger(__name__)

try:
    from scipy import stats as _scipy_stats
except Exception:
    _scipy_stats = None


def _normal_cdf(value: float) -> float:
    import math

    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _two_sided_p_from_stat(value: float) -> float:
    if not np.isfinite(value):
        return 1.0
    return float(max(0.0, min(1.0, 2.0 * (1.0 - _normal_cdf(abs(value))))))


def _spearmanr(left: Any, right: Any) -> tuple[float, float]:
    if _scipy_stats is not None:
        corr, p_value = _scipy_stats.spearmanr(left, right)
        return float(corr), float(p_value)
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 3:
        return 0.0, 1.0
    corr = frame["left"].rank(method="average").corr(frame["right"].rank(method="average"))
    if pd.isna(corr):
        return 0.0, 1.0
    if abs(float(corr)) >= 1.0:
        return float(corr), 0.0
    denom = max(1e-12, 1.0 - float(corr) ** 2)
    stat = float(corr) * np.sqrt((len(frame) - 2) / denom)
    return float(corr), _two_sided_p_from_stat(stat)


def _ttest_1samp_pvalue(values: pd.Series, target: float = 0.0) -> float:
    clean = pd.Series(values).dropna()
    if len(clean) < 2:
        return 1.0
    if _scipy_stats is not None:
        result = _scipy_stats.ttest_1samp(clean, target)
        return float(result.pvalue) if not pd.isna(result.pvalue) else 1.0
    std = clean.std()
    if pd.isna(std) or std == 0:
        return 0.0 if float(clean.mean()) != target else 1.0
    stat = (float(clean.mean()) - target) / (float(std) / np.sqrt(len(clean)))
    return _two_sided_p_from_stat(stat)


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
        self._sensitivity_enabled = bool(self._config.get("sensitivity_enabled", False))

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
            report = self._validate_cross_sectional(spec, data, compute_signal)
        else:
            report = self._validate_single_symbol(spec, data, compute_signal)

        return self._with_sensitivity(spec, report)

    def _resolve_universe(self, spec: StrategySpec) -> List[str]:
        fallback = list(spec.universe)
        if not fallback:
            return fallback
        if not hasattr(self._market_data, "get_universe_symbols"):
            return fallback
        try:
            universe = self._market_data.get_universe_symbols(detect_market(fallback[0]))
            return list(universe) if universe is not None else fallback
        except Exception as e:
            logger.warning(f"Universe fetch failed: {e}")
            return fallback

    def _validate_single_symbol(self, spec: StrategySpec, data: pd.DataFrame, compute_signal: Any) -> ValidationReport:
        from quant.features.research.validation.signal_library import adjusted_price_series

        signal = compute_signal(spec.signal_formula_key, data, spec.lookback_days)
        if signal is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"])

        close = adjusted_price_series(data, "close")
        forward_return = close.pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)
        signal = signal.shift(self._exec_lag)

        common_idx = signal.dropna().index.intersection(forward_return.dropna().index)
        if len(common_idx) < self._min_obs:
            return self._error_report(spec, [f"Insufficient valid observations: {len(common_idx)}"])

        sig = signal.loc[common_idx]
        fwd = forward_return.loc[common_idx]

        rank_ic, rank_ic_p = _spearmanr(sig, fwd)
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
        from quant.features.research.validation.signal_library import adjusted_price_matrix

        frame = data.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values(["date", "symbol"])

        signal_matrix = compute_signal(spec.signal_formula_key, frame, spec.lookback_days)
        if signal_matrix is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"])

        close_prices = adjusted_price_matrix(frame, "close")
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
        rank_ic_p = _ttest_1samp_pvalue(valid_ic, 0.0)
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

    def _with_sensitivity(self, spec: StrategySpec, report: ValidationReport) -> ValidationReport:
        if not self._sensitivity_enabled or report.status != "validated":
            return report
        try:
            sensitivity = run_sensitivity_sweep(
                spec,
                self._market_data,
                {"lookback_days": spec.lookback_days, "horizon_days": spec.horizon_days},
                self._config,
            )
            status = "stable" if sensitivity.is_stable else "unstable"
            errors = list(report.errors or [])
            errors.append(f"sensitivity: {status} (max_degradation={sensitivity.max_degradation_pct:.1f}%)")
            return replace(report, errors=errors)
        except Exception as e:
            logger.warning(f"Sensitivity sweep unavailable: {e}")
            return report

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
