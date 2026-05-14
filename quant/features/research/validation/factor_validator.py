from __future__ import annotations

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


def _ttest_1samp_stat_pvalue(values: pd.Series, target: float = 0.0) -> tuple[float, float]:
    clean = pd.Series(values).dropna()
    if len(clean) < 2:
        return 0.0, 1.0
    if _scipy_stats is not None:
        result = _scipy_stats.ttest_1samp(clean, target)
        statistic = float(result.statistic) if not pd.isna(result.statistic) else 0.0
        p_value = float(result.pvalue) if not pd.isna(result.pvalue) else 1.0
        if not np.isfinite(statistic):
            statistic = float(np.sign(float(clean.mean()) - target) * 1e12)
        return statistic, p_value
    std = clean.std()
    if pd.isna(std) or std == 0:
        if float(clean.mean()) == target:
            return 0.0, 1.0
        return float(np.sign(float(clean.mean()) - target) * 1e12), 0.0
    stat = (float(clean.mean()) - target) / (float(std) / np.sqrt(len(clean)))
    return float(stat), _two_sided_p_from_stat(stat)


def _ttest_1samp_pvalue(values: pd.Series, target: float = 0.0) -> float:
    return _ttest_1samp_stat_pvalue(values, target)[1]


def _corr_tstat(corr: float, n_observations: int) -> float:
    if n_observations < 3 or not np.isfinite(corr):
        return 0.0
    if abs(corr) >= 1.0:
        corr = float(np.sign(corr) * (1.0 - 1e-12))
    denom = max(1e-12, 1.0 - corr ** 2)
    return float(corr * np.sqrt((n_observations - 2) / denom))


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
        self._top_bucket_size = max(1, int(self._config.get("top_bucket_size", 20) or 20))
        self._factor_validation_enabled = bool(self._config.get("factor_validation_enabled", False))
        self._sensitivity_enabled = bool(self._config.get("sensitivity_enabled", False))
        self._start_date = str(self._config.get("start_date", "2012-01-01"))
        self._end_date = str(self._config.get("end_date", "2025-12-31"))

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
        seed_universe_size = len(list(spec.universe or []))
        raw_data = self._market_data.get_daily_bars(
            symbols=symbols,
            start=self._start_date,
            end=self._end_date,
        )
        if raw_data is None:
            return self._error_report(
                spec,
                ["No market data returned"],
                self._universe_metadata(symbols, None, seed_universe_size),
            )

        data = raw_data if isinstance(raw_data, pd.DataFrame) else pd.DataFrame(raw_data)
        universe_metadata = self._universe_metadata(symbols, data, seed_universe_size)
        if len(data) < self._min_obs:
            return self._error_report(spec, [f"Insufficient data: {len(data)} < {self._min_obs}"], universe_metadata)

        if {"symbol", "date"}.issubset(data.columns):
            report = self._validate_cross_sectional(spec, data, compute_signal, universe_metadata)
        else:
            report = self._validate_single_symbol(spec, data, compute_signal, universe_metadata)

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

    def _universe_metadata(self, symbols: List[str], data: Any, seed_universe_size: int) -> Dict[str, Any]:
        data_rows = len(data) if data is not None else 0
        data_symbol_count = 0
        if isinstance(data, pd.DataFrame) and "symbol" in data.columns:
            data_symbol_count = int(data["symbol"].astype(str).nunique())
        elif data_rows:
            data_symbol_count = min(len(symbols), 1)
        source = "daily_cn_ochl resolved full universe" if len(symbols) > seed_universe_size else "StrategySpec universe"
        return {
            "universe_size": int(len(symbols)),
            "universe_sample": [str(symbol) for symbol in symbols[:10]],
            "universe_source": source,
            "data_rows": int(data_rows),
            "data_symbol_count": int(data_symbol_count),
        }

    def _validate_single_symbol(
        self,
        spec: StrategySpec,
        data: pd.DataFrame,
        compute_signal: Any,
        universe_metadata: Dict[str, Any],
    ) -> ValidationReport:
        from quant.features.research.validation.signal_library import adjusted_price_series

        signal = compute_signal(spec.signal_formula_key, data, spec.lookback_days)
        if signal is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"], universe_metadata)

        close = adjusted_price_series(data, "close")
        forward_return = close.pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)

        common_idx = signal.dropna().index.intersection(forward_return.dropna().index)
        if len(common_idx) < self._min_obs:
            return self._error_report(spec, [f"Insufficient valid observations: {len(common_idx)}"], universe_metadata)

        sig = signal.loc[common_idx]
        fwd = forward_return.loc[common_idx]

        rank_ic, rank_ic_p = _spearmanr(sig, fwd)
        rank_ic_tstat = _corr_tstat(float(rank_ic), len(common_idx))
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
            rank_ic_tstat=rank_ic_tstat,
            rank_ic_p_value=float(rank_ic_p),
            **universe_metadata,
        )

    def _validate_cross_sectional(
        self,
        spec: StrategySpec,
        data: pd.DataFrame,
        compute_signal: Any,
        universe_metadata: Dict[str, Any],
    ) -> ValidationReport:
        from quant.features.research.validation.signal_library import adjusted_price_matrix, field_matrix

        frame = data.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values(["date", "symbol"])

        signal_matrix = compute_signal(spec.signal_formula_key, frame, spec.lookback_days)
        if signal_matrix is None:
            return self._error_report(spec, [f"Unsupported formula: {spec.signal_formula_key}"], universe_metadata)

        close_prices = adjusted_price_matrix(frame, "close")
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
                universe_metadata,
            )

        rank_ic = float(valid_ic.mean())
        rank_ic_ir = compute_icir(valid_ic)
        rank_ic_tstat, rank_ic_p = _ttest_1samp_stat_pvalue(valid_ic, 0.0)
        long_short_series = self._long_short_series(signal_matrix, forward_returns)
        portfolio_diagnostics = self._portfolio_diagnostics(
            spec,
            signal_matrix,
            forward_returns,
            close_prices,
            field_matrix(frame, "volume") if "volume" in frame.columns else None,
            str(close_prices.index[0].date()),
            str(close_prices.index[-1].date()),
        )
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
            rank_ic_tstat=rank_ic_tstat,
            rank_ic_p_value=rank_ic_p,
            fama_macbeth_tstat=compute_fama_macbeth_tstat(
                signal_matrix,
                forward_returns,
                min_stocks=self._min_stocks,
            ),
            portfolio_diagnostics=portfolio_diagnostics,
            errors=factor_errors,
            **universe_metadata,
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

    def _portfolio_diagnostics(
        self,
        spec: StrategySpec,
        signals: pd.DataFrame,
        forward_returns: pd.DataFrame,
        close_prices: pd.DataFrame,
        volume: Optional[pd.DataFrame],
        start: str,
        end: str,
    ) -> Dict[str, Any]:
        cost_bps = float(self._config.get("portfolio_diagnostic_cost_bps", 10.0) or 0.0)
        top_bucket, top_bucket_turnover_series, top_bucket_counts = self._top_n_portfolio_returns(
            signals,
            close_prices,
            self._top_bucket_size,
            spec.horizon_days,
        )
        top1_pct, top1_turnover_series = self._top_pct_portfolio_returns(
            signals,
            close_prices,
            0.01,
            spec.horizon_days,
        )
        after_cost = self._apply_turnover_cost(top_bucket, top_bucket_turnover_series, cost_bps)
        top1_after_cost = self._apply_turnover_cost(top1_pct, top1_turnover_series, cost_bps)
        benchmark_symbol, benchmark_returns, benchmark_coverage = self._benchmark_daily_returns(
            spec,
            start,
            end,
            top_bucket.index,
        )
        excess = pd.Series(dtype=float)
        excess_after_cost = pd.Series(dtype=float)
        if benchmark_returns is not None and not benchmark_returns.empty and not top_bucket.empty:
            aligned = pd.concat(
                [top_bucket.rename("top"), after_cost.rename("after_cost"), benchmark_returns.rename("benchmark")],
                axis=1,
            ).dropna()
            if not aligned.empty:
                excess = aligned["top"] - aligned["benchmark"]
                excess_after_cost = aligned["after_cost"] - aligned["benchmark"]
        series_for_oos = excess_after_cost if not excess_after_cost.empty else after_cost
        top_bucket_mean = self._safe_mean(top_bucket)
        top_bucket_annualized = self._annualize_period_return(top_bucket_mean, 1)
        after_cost_mean = self._safe_mean(after_cost)
        after_cost_annualized = self._annualize_period_return(after_cost_mean, 1)
        top1_mean = self._safe_mean(top1_pct)
        top1_annualized = self._annualize_period_return(top1_mean, 1)
        top1_after_cost_mean = self._safe_mean(top1_after_cost)
        top1_after_cost_annualized = self._annualize_period_return(top1_after_cost_mean, 1)
        excess_mean = self._safe_mean(excess)
        excess_annualized = self._annualize_period_return(excess_mean, 1)
        excess_after_cost_mean = self._safe_mean(excess_after_cost)
        excess_after_cost_annualized = self._annualize_period_return(excess_after_cost_mean, 1)
        top_bucket_turnover = self._safe_mean(top_bucket_turnover_series)
        top1_turnover = self._safe_mean(top1_turnover_series)
        return {
            "kind": "top_bucket_long_only",
            "top_bucket_selection": "top_n",
            "top_bucket_target_count": self._top_bucket_size,
            "top_bucket_selected_count_mean": self._safe_mean(top_bucket_counts),
            "top_bucket_selected_count_min": self._safe_min_int(top_bucket_counts),
            "top_bucket_selected_count_max": self._safe_max_int(top_bucket_counts),
            "holding_horizon_days": spec.horizon_days,
            "rebalance_frequency": "daily signal / holding horizon gate",
            "cost_bps_per_turn": cost_bps,
            "return_frequency": "daily portfolio returns",
            "top_bucket_mean_return": top_bucket_mean,
            "top_bucket_annualized_return": top_bucket_annualized,
            "top_bucket_sharpe": self._period_sharpe(top_bucket, 1),
            "top_bucket_after_cost_sharpe": self._period_sharpe(after_cost, 1),
            "top_bucket_turnover": top_bucket_turnover,
            "top_bucket_hit_rate": self._hit_rate(top_bucket),
            "top_bucket_after_cost_mean_return": after_cost_mean,
            "top_bucket_after_cost_annualized_return": after_cost_annualized,
            "top_bucket_max_drawdown": self._max_drawdown(top_bucket),
            "top_bucket_after_cost_max_drawdown": self._max_drawdown(after_cost),
            "top_bucket_calmar_ratio": self._calmar_ratio(top_bucket_annualized, top_bucket),
            "top_bucket_after_cost_calmar_ratio": self._calmar_ratio(after_cost_annualized, after_cost),
            "top1_pct": 0.01,
            "top1_pct_mean_return": top1_mean,
            "top1_pct_annualized_return": top1_annualized,
            "top1_pct_sharpe": self._period_sharpe(top1_pct, 1),
            "top1_pct_after_cost_sharpe": self._period_sharpe(top1_after_cost, 1),
            "top1_pct_turnover": top1_turnover,
            "top1_pct_hit_rate": self._hit_rate(top1_pct),
            "top1_pct_after_cost_mean_return": top1_after_cost_mean,
            "top1_pct_after_cost_annualized_return": top1_after_cost_annualized,
            "top1_pct_max_drawdown": self._max_drawdown(top1_pct),
            "top1_pct_after_cost_max_drawdown": self._max_drawdown(top1_after_cost),
            "top1_pct_calmar_ratio": self._calmar_ratio(top1_annualized, top1_pct),
            "top1_pct_after_cost_calmar_ratio": self._calmar_ratio(top1_after_cost_annualized, top1_after_cost),
            "benchmark_symbol": benchmark_symbol or "",
            "benchmark_coverage": benchmark_coverage,
            "benchmark_excess_mean_return": excess_mean,
            "benchmark_excess_annualized_return": excess_annualized,
            "benchmark_excess_sharpe": self._period_sharpe(excess, 1),
            "benchmark_excess_after_cost_sharpe": self._period_sharpe(excess_after_cost, 1),
            "benchmark_excess_after_cost_mean_return": excess_after_cost_mean,
            "benchmark_excess_after_cost_annualized_return": excess_after_cost_annualized,
            "benchmark_excess_max_drawdown": self._max_drawdown(excess),
            "benchmark_excess_after_cost_max_drawdown": self._max_drawdown(excess_after_cost),
            "benchmark_excess_calmar_ratio": self._calmar_ratio(excess_annualized, excess),
            "benchmark_excess_after_cost_calmar_ratio": self._calmar_ratio(excess_after_cost_annualized, excess_after_cost),
            "rolling_oos": self._rolling_oos(series_for_oos, 1),
            "pnl_attribution_bridge": self._pnl_attribution_bridge(
                spec,
                signals,
                close_prices,
                volume,
                cost_bps,
            ),
            "long_short_usage": "alpha_diagnostic_only",
        }

    def _top_bucket_series(self, signals: pd.DataFrame, forward_returns: pd.DataFrame) -> pd.Series:
        return self._top_n_series(signals, forward_returns, self._top_bucket_size)

    def _top_n_series(self, signals: pd.DataFrame, forward_returns: pd.DataFrame, top_n: int) -> pd.Series:
        if top_n <= 0:
            return pd.Series(dtype=float)
        common_index = signals.index.intersection(forward_returns.index)
        common_columns = signals.columns.intersection(forward_returns.columns)
        values = []
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
            long_returns = paired.sort_values("signal", ascending=False).head(min(int(top_n), len(paired)))["return"]
            if long_returns.empty:
                continue
            values.append(float(long_returns.mean()))
            dates.append(date)
        return pd.Series(values, index=pd.to_datetime(dates)).sort_index() if values else pd.Series(dtype=float)

    def _top_pct_series(self, signals: pd.DataFrame, forward_returns: pd.DataFrame, top_pct: float) -> pd.Series:
        if top_pct <= 0.0 or top_pct > 1.0:
            return pd.Series(dtype=float)
        common_index = signals.index.intersection(forward_returns.index)
        common_columns = signals.columns.intersection(forward_returns.columns)
        values = []
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
            n_top = max(1, int(np.ceil(len(paired) * top_pct)))
            long_returns = paired.sort_values("signal", ascending=False).head(n_top)["return"]
            if long_returns.empty:
                continue
            values.append(float(long_returns.mean()))
            dates.append(date)
        return pd.Series(values, index=pd.to_datetime(dates)).sort_index() if values else pd.Series(dtype=float)

    def _top_pct_portfolio_returns(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        top_pct: float,
        horizon_days: int,
    ) -> tuple[pd.Series, pd.Series]:
        if top_pct <= 0.0 or top_pct > 1.0:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        daily_returns = prices.pct_change(fill_method=None).shift(-self._exec_lag - 1)
        common_index = signals.index.intersection(daily_returns.index)
        common_columns = signals.columns.intersection(daily_returns.columns)
        active: List[Dict[Any, float]] = []
        previous_weights: Dict[Any, float] | None = None
        return_values = []
        return_dates = []
        turnover_values = []
        turnover_dates = []
        for date in common_index:
            signal = signals.loc[date, common_columns].dropna()
            if len(signal) >= self._min_stocks:
                n_top = max(1, int(np.ceil(len(signal) * top_pct)))
                selected = signal.sort_values(ascending=False).head(n_top).index
                weight = 1.0 / float(len(selected))
                active.append({symbol: weight for symbol in selected})
                active = active[-max(1, int(horizon_days)):]
            if not active:
                continue
            current_weights = self._average_cohort_weights(active)
            if previous_weights is not None:
                turnover_values.append(self._one_way_turnover(previous_weights, current_weights))
                turnover_dates.append(date)
            next_returns = daily_returns.loc[date, list(current_weights)].dropna()
            if next_returns.empty:
                previous_weights = current_weights
                continue
            available_weights = {symbol: current_weights[symbol] for symbol in next_returns.index}
            total_weight = sum(available_weights.values())
            if total_weight <= 0.0:
                previous_weights = current_weights
                continue
            portfolio_return = sum((weight / total_weight) * float(next_returns[symbol]) for symbol, weight in available_weights.items())
            return_values.append(float(portfolio_return))
            return_dates.append(date)
            previous_weights = current_weights
        returns = pd.Series(return_values, index=pd.to_datetime(return_dates)).sort_index() if return_values else pd.Series(dtype=float)
        turnover = pd.Series(turnover_values, index=pd.to_datetime(turnover_dates)).sort_index() if turnover_values else pd.Series(dtype=float)
        return returns, turnover

    def _top_n_portfolio_returns(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        top_n: int,
        horizon_days: int,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        if top_n <= 0:
            return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
        daily_returns = prices.pct_change(fill_method=None).shift(-self._exec_lag - 1)
        common_index = signals.index.intersection(daily_returns.index)
        common_columns = signals.columns.intersection(daily_returns.columns)
        active: List[Dict[Any, float]] = []
        previous_weights: Dict[Any, float] | None = None
        return_values = []
        return_dates = []
        turnover_values = []
        turnover_dates = []
        selected_count_values = []
        selected_count_dates = []
        for date in common_index:
            signal = signals.loc[date, common_columns].dropna()
            if len(signal) >= self._min_stocks:
                selected = signal.sort_values(ascending=False).head(min(int(top_n), len(signal))).index
                weight = 1.0 / float(len(selected))
                active.append({symbol: weight for symbol in selected})
                active = active[-max(1, int(horizon_days)):]
                selected_count_values.append(float(len(selected)))
                selected_count_dates.append(date)
            if not active:
                continue
            current_weights = self._average_cohort_weights(active)
            if previous_weights is not None:
                turnover_values.append(self._one_way_turnover(previous_weights, current_weights))
                turnover_dates.append(date)
            next_returns = daily_returns.loc[date, list(current_weights)].dropna()
            if next_returns.empty:
                previous_weights = current_weights
                continue
            available_weights = {symbol: current_weights[symbol] for symbol in next_returns.index}
            total_weight = sum(available_weights.values())
            if total_weight <= 0.0:
                previous_weights = current_weights
                continue
            portfolio_return = sum((weight / total_weight) * float(next_returns[symbol]) for symbol, weight in available_weights.items())
            return_values.append(float(portfolio_return))
            return_dates.append(date)
            previous_weights = current_weights
        returns = pd.Series(return_values, index=pd.to_datetime(return_dates)).sort_index() if return_values else pd.Series(dtype=float)
        turnover = pd.Series(turnover_values, index=pd.to_datetime(turnover_dates)).sort_index() if turnover_values else pd.Series(dtype=float)
        counts = pd.Series(selected_count_values, index=pd.to_datetime(selected_count_dates)).sort_index() if selected_count_values else pd.Series(dtype=float)
        return returns, turnover, counts

    def _pnl_attribution_bridge(
        self,
        spec: StrategySpec,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        volume: Optional[pd.DataFrame],
        cost_bps: float,
    ) -> List[Dict[str, Any]]:
        layers: List[Dict[str, Any]] = []
        layer_specs = [
            (
                "ideal_top20_close_to_close",
                "理想 top20 close-to-close",
                "每日信号 top20 等权，下一交易日 close-to-close，不加执行约束。",
                {"selector": "top_n", "positive_only": False, "execution_lag_days": 0, "holding_days": 1, "require_volume": False},
            ),
            (
                "strategy_selection_rule",
                "真实选股规则",
                "使用策略生成器规则：top 1% capped 20，且 signal > 0。",
                {"selector": "strategy_top_pct", "positive_only": True, "execution_lag_days": 0, "holding_days": 1, "require_volume": False},
            ),
            (
                "execution_lag",
                "加入执行滞后",
                f"信号生成后等待 {self._exec_lag} 个交易日再计算可实现收益。",
                {"selector": "strategy_top_pct", "positive_only": True, "execution_lag_days": self._exec_lag, "holding_days": 1, "require_volume": False},
            ),
            (
                "holding_cohort",
                "加入持有期 cohort",
                f"按 {spec.horizon_days} 日持有期滚动叠加 cohort，近似策略持仓节奏。",
                {"selector": "strategy_top_pct", "positive_only": True, "execution_lag_days": self._exec_lag, "holding_days": spec.horizon_days, "require_volume": False},
            ),
            (
                "volume_tradeable_proxy",
                "加入成交量可交易近似",
                "过滤当日 volume<=0 或价格缺失的标的；不是订单级成交量限制。",
                {"selector": "strategy_top_pct", "positive_only": True, "execution_lag_days": self._exec_lag, "holding_days": spec.horizon_days, "require_volume": True},
            ),
        ]
        previous_ann = None
        previous_sharpe = None
        latest_returns = pd.Series(dtype=float)
        latest_turnover = pd.Series(dtype=float)
        latest_counts = pd.Series(dtype=float)
        for key, label, note, options in layer_specs:
            returns, turnover, counts = self._bridge_portfolio_returns(
                signals,
                prices,
                volume,
                top_n=self._top_bucket_size,
                top_pct=0.01,
                **options,
            )
            layer = self._pnl_bridge_layer(key, label, note, returns, turnover, counts, previous_ann, previous_sharpe)
            layers.append(layer)
            previous_ann = layer["annualized_return"]
            previous_sharpe = layer["sharpe"]
            latest_returns = returns
            latest_turnover = turnover
            latest_counts = counts
        after_cost = self._apply_turnover_cost(latest_returns, latest_turnover, cost_bps)
        layers.append(
            self._pnl_bridge_layer(
                "turnover_cost",
                "加入估算换手成本",
                f"按组合诊断成本 {cost_bps:.1f} bps * one-way turnover 扣减；仍非订单级撮合。",
                after_cost,
                latest_turnover,
                latest_counts,
                previous_ann,
                previous_sharpe,
                cost_bps=cost_bps,
            )
        )
        return layers

    def _bridge_portfolio_returns(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        volume: Optional[pd.DataFrame],
        selector: str,
        top_n: int,
        top_pct: float,
        positive_only: bool,
        execution_lag_days: int,
        holding_days: int,
        require_volume: bool,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        daily_returns = prices.pct_change(fill_method=None).shift(-int(execution_lag_days) - 1)
        common_index = signals.index.intersection(daily_returns.index)
        common_columns = signals.columns.intersection(daily_returns.columns)
        if volume is not None:
            common_columns = common_columns.intersection(volume.columns)
        active: List[Dict[Any, float]] = []
        previous_weights: Dict[Any, float] | None = None
        return_values = []
        return_dates = []
        turnover_values = []
        turnover_dates = []
        selected_count_values = []
        selected_count_dates = []
        for date in common_index:
            signal = signals.loc[date, common_columns].dropna()
            raw_count = len(signal)
            if require_volume and volume is not None and raw_count:
                tradable = volume.loc[date, signal.index].fillna(0.0) > 0
                signal = signal.loc[tradable]
            if raw_count >= self._min_stocks:
                selected = self._bridge_selected_symbols(signal, selector, top_n, top_pct, positive_only)
                if selected:
                    weight = 1.0 / float(len(selected))
                    active.append({symbol: weight for symbol in selected})
                    active = active[-max(1, int(holding_days)):]
                    selected_count_values.append(float(len(selected)))
                else:
                    active = []
                    selected_count_values.append(0.0)
                selected_count_dates.append(date)
            current_weights = self._average_cohort_weights(active)
            if previous_weights is not None:
                turnover_values.append(self._one_way_turnover(previous_weights, current_weights))
                turnover_dates.append(date)
            if not current_weights:
                return_values.append(0.0)
                return_dates.append(date)
                previous_weights = current_weights
                continue
            next_returns = daily_returns.loc[date, list(current_weights)].dropna()
            if next_returns.empty:
                portfolio_return = 0.0
            else:
                available_weights = {symbol: current_weights[symbol] for symbol in next_returns.index}
                total_weight = sum(available_weights.values())
                portfolio_return = (
                    sum((weight / total_weight) * float(next_returns[symbol]) for symbol, weight in available_weights.items())
                    if total_weight > 0.0
                    else 0.0
                )
            return_values.append(float(portfolio_return))
            return_dates.append(date)
            previous_weights = current_weights
        returns = pd.Series(return_values, index=pd.to_datetime(return_dates)).sort_index() if return_values else pd.Series(dtype=float)
        turnover = pd.Series(turnover_values, index=pd.to_datetime(turnover_dates)).sort_index() if turnover_values else pd.Series(dtype=float)
        counts = pd.Series(selected_count_values, index=pd.to_datetime(selected_count_dates)).sort_index() if selected_count_values else pd.Series(dtype=float)
        return returns, turnover, counts

    def _bridge_selected_symbols(
        self,
        signal: pd.Series,
        selector: str,
        top_n: int,
        top_pct: float,
        positive_only: bool,
    ) -> List[Any]:
        clean = signal.replace([np.inf, -np.inf], np.nan).dropna()
        if positive_only:
            clean = clean[clean > 0]
        if clean.empty:
            return []
        if selector == "strategy_top_pct":
            count = max(1, min(int(top_n), int(np.ceil(len(signal.dropna()) * top_pct))))
        else:
            count = max(1, min(int(top_n), len(clean)))
        return list(clean.sort_values(ascending=False).head(count).index)

    def _pnl_bridge_layer(
        self,
        key: str,
        label: str,
        note: str,
        returns: pd.Series,
        turnover: pd.Series,
        counts: pd.Series,
        previous_ann: Optional[float],
        previous_sharpe: Optional[float],
        cost_bps: Optional[float] = None,
    ) -> Dict[str, Any]:
        clean = returns.dropna() if returns is not None else pd.Series(dtype=float)
        mean_return = self._safe_mean(clean)
        annualized = self._annualize_period_return(mean_return, 1)
        sharpe = self._period_sharpe(clean, 1)
        layer = {
            "key": key,
            "label": label,
            "note": note,
            "observations": int(len(clean)),
            "mean_return": mean_return,
            "annualized_return": annualized,
            "sharpe": sharpe,
            "max_drawdown": self._max_drawdown(clean),
            "calmar_ratio": self._calmar_ratio(annualized, clean),
            "hit_rate": self._hit_rate(clean),
            "turnover": self._safe_mean(turnover),
            "selected_count_mean": self._safe_mean(counts),
            "selected_count_min": self._safe_min_int(counts),
            "selected_count_max": self._safe_max_int(counts),
            "delta_annualized_return": annualized - previous_ann if previous_ann is not None else 0.0,
            "delta_sharpe": sharpe - previous_sharpe if previous_sharpe is not None else 0.0,
        }
        if cost_bps is not None:
            layer["cost_bps"] = float(cost_bps)
        return layer

    @staticmethod
    def _average_cohort_weights(cohorts: List[Dict[Any, float]]) -> Dict[Any, float]:
        weights: Dict[Any, float] = {}
        if not cohorts:
            return weights
        cohort_scale = 1.0 / float(len(cohorts))
        for cohort in cohorts:
            for symbol, weight in cohort.items():
                weights[symbol] = weights.get(symbol, 0.0) + weight * cohort_scale
        return weights

    @staticmethod
    def _one_way_turnover(previous: Dict[Any, float], current: Dict[Any, float]) -> float:
        symbols = set(previous).union(current)
        if not symbols:
            return 0.0
        return float(0.5 * sum(abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols))

    @staticmethod
    def _apply_turnover_cost(returns: pd.Series, turnover: pd.Series, cost_bps: float) -> pd.Series:
        if returns.empty:
            return returns
        aligned_turnover = turnover.reindex(returns.index).fillna(0.0)
        return returns - aligned_turnover * (cost_bps / 10000.0)

    def _top_pct_turnover(self, signals: pd.DataFrame, forward_returns: pd.DataFrame, top_pct: float) -> float:
        if top_pct <= 0.0 or top_pct > 1.0:
            return 0.0
        common_index = signals.index.intersection(forward_returns.index)
        common_columns = signals.columns.intersection(forward_returns.columns)
        previous: Dict[Any, float] | None = None
        turnover = []
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
            n_top = max(1, int(np.ceil(len(paired) * top_pct)))
            selected = paired.sort_values("signal", ascending=False).head(n_top).index
            weight = 1.0 / float(len(selected))
            current = {symbol: weight for symbol in selected}
            if previous is not None:
                symbols = set(previous).union(current)
                turnover.append(0.5 * sum(abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols))
            previous = current
        return float(np.mean(turnover)) if turnover else 0.0

    def _benchmark_daily_returns(
        self,
        spec: StrategySpec,
        start: str,
        end: str,
        target_index: pd.Index,
    ) -> tuple[str, pd.Series, Dict[str, Any]]:
        if not spec.universe or detect_market(spec.universe[0]) != "cn":
            return "", pd.Series(dtype=float), {}
        from quant.features.research.validation.signal_library import adjusted_price_matrix

        for symbol in ("000300", "510300"):
            try:
                raw = self._market_data.get_daily_bars([symbol], start, end)
                frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
            except Exception as e:
                logger.warning(f"Benchmark fetch failed for {symbol}: {e}")
                continue
            if frame.empty or "date" not in frame.columns:
                continue
            frame = frame.copy()
            frame["date"] = pd.to_datetime(frame["date"])
            prices = adjusted_price_matrix(frame, "close")
            if prices.empty:
                continue
            column = symbol if symbol in prices.columns else prices.columns[0]
            forward = prices[column].pct_change().shift(-self._exec_lag - 1)
            forward = forward.reindex(pd.to_datetime(target_index)).dropna()
            coverage = {
                "start": str(prices.index.min().date()),
                "end": str(prices.index.max().date()),
                "rows": int(prices[column].dropna().shape[0]),
                "fallback_used": symbol != "000300",
            }
            return symbol, forward, coverage
        return "", pd.Series(dtype=float), {}

    def _benchmark_forward_returns(
        self,
        spec: StrategySpec,
        start: str,
        end: str,
        target_index: pd.Index,
    ) -> tuple[str, pd.Series, Dict[str, Any]]:
        if not spec.universe or detect_market(spec.universe[0]) != "cn":
            return "", pd.Series(dtype=float), {}
        from quant.features.research.validation.signal_library import adjusted_price_matrix

        for symbol in ("000300", "510300"):
            try:
                raw = self._market_data.get_daily_bars([symbol], start, end)
                frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
            except Exception as e:
                logger.warning(f"Benchmark fetch failed for {symbol}: {e}")
                continue
            if frame.empty or "date" not in frame.columns:
                continue
            frame = frame.copy()
            frame["date"] = pd.to_datetime(frame["date"])
            prices = adjusted_price_matrix(frame, "close")
            if prices.empty:
                continue
            column = symbol if symbol in prices.columns else prices.columns[0]
            forward = prices[column].pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)
            forward = forward.reindex(pd.to_datetime(target_index)).dropna()
            coverage = {
                "start": str(prices.index.min().date()),
                "end": str(prices.index.max().date()),
                "rows": int(prices[column].dropna().shape[0]),
                "fallback_used": symbol != "000300",
            }
            return symbol, forward, coverage
        return "", pd.Series(dtype=float), {}

    @staticmethod
    def _safe_mean(series: pd.Series) -> float:
        return float(series.dropna().mean()) if series is not None and not series.dropna().empty else 0.0

    @staticmethod
    def _safe_min_int(series: pd.Series) -> int:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        return int(clean.min()) if not clean.empty else 0

    @staticmethod
    def _safe_max_int(series: pd.Series) -> int:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        return int(clean.max()) if not clean.empty else 0

    @staticmethod
    def _hit_rate(series: pd.Series) -> float:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        return float((clean > 0).mean()) if not clean.empty else 0.0

    @staticmethod
    def _annualize_period_return(mean_return: float, horizon_days: int) -> float:
        if mean_return <= -1.0:
            return -1.0
        periods = 252.0 / max(1, float(horizon_days))
        return float((1.0 + mean_return) ** periods - 1.0)

    @staticmethod
    def _period_sharpe(series: pd.Series, horizon_days: int) -> float:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        clean = clean.replace([np.inf, -np.inf], np.nan).dropna()
        if len(clean) < 2:
            return 0.0
        std = clean.std()
        if pd.isna(std) or std == 0:
            return 0.0
        periods = 252.0 / max(1, float(horizon_days))
        return float(clean.mean() / std * np.sqrt(periods))

    @staticmethod
    def _max_drawdown(series: pd.Series) -> float:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        clean = clean.replace([np.inf, -np.inf], np.nan).dropna().clip(lower=-0.999999)
        if clean.empty:
            return 0.0
        equity = (1.0 + clean).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        value = float(drawdown.min()) if not drawdown.empty else 0.0
        return value if np.isfinite(value) else 0.0

    def _calmar_ratio(self, annualized_return: float, returns: pd.Series) -> float:
        if not np.isfinite(annualized_return):
            return 0.0
        max_drawdown = abs(self._max_drawdown(returns))
        if max_drawdown <= 1e-12:
            return 0.0
        return float(annualized_return / max_drawdown)

    def _rolling_oos(self, series: pd.Series, horizon_days: int) -> List[Dict[str, Any]]:
        clean = series.dropna() if series is not None else pd.Series(dtype=float)
        if clean.empty:
            return []
        rows = []
        grouped = clean.groupby(clean.index.year)
        for year, chunk in grouped:
            mean_return = self._safe_mean(chunk)
            rows.append(
                {
                    "split": str(year),
                    "observations": int(len(chunk)),
                    "mean_return": mean_return,
                    "annualized_return": self._annualize_period_return(mean_return, horizon_days),
                    "hit_rate": self._hit_rate(chunk),
                }
            )
        return rows

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

    def _error_report(self, spec, errors, universe_metadata: Dict[str, Any] | None = None):
        universe_metadata = universe_metadata or {}
        return ValidationReport(
            strategy_id=spec.strategy_id,
            status="error",
            rank_ic=0.0, rank_ic_ir=0.0, ic_decay=[],
            fdr_adjusted_p=1.0, fdr_significant=False,
            ff_alpha_monthly=0.0, ff_alpha_tstat=0.0, ff_r2=0.0,
            long_short_spread=0.0, hit_rate=0.0,
            data_start="", data_end="", n_observations=0,
            errors=errors,
            **universe_metadata,
        )
