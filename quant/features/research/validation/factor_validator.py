import math
from dataclasses import replace
from typing import Any, Mapping, Optional

import pandas as pd

from quant.domain.ports import FactorData, ResearchMarketData
from quant.features.research.models import StrategySpec, ValidationReport
from quant.features.research.validation.fdr import benjamini_hochberg
from quant.features.research.validation.ff_decomposition import empty_factor_decomposition
from quant.features.research.validation.signal_library import build_validation_frame


class FactorValidator:
    def __init__(
        self,
        market_data: ResearchMarketData,
        factor_data: Optional[FactorData] = None,
        config: Optional[Mapping[str, Any]] = None,
    ):
        self.market_data = market_data
        self.factor_data = factor_data
        self.config = dict(config or {})

    def validate(self, spec: StrategySpec, start: str, end: str) -> ValidationReport:
        return self.validate_many([spec], start, end)[0]

    def validate_many(self, specs: list[StrategySpec], start: str, end: str) -> list[ValidationReport]:
        pairs = [self._validate_one(spec, start, end) for spec in specs]
        p_values = [p_value for _, p_value in pairs]
        fdr_results = benjamini_hochberg(p_values, alpha=self._max_fdr_p())
        reports = []
        for (report, _), fdr in zip(pairs, fdr_results):
            reports.append(self._with_fdr(report, float(fdr["adjusted_p"]), bool(fdr["significant"])))
        return reports

    def _validate_one(self, spec: StrategySpec, start: str, end: str) -> tuple[ValidationReport, float]:
        if spec.status != "ready":
            return self._empty_report(spec, spec.status, start, end, [spec.reason or spec.status]), 1.0

        bars = self.market_data.get_daily_bars(list(spec.universe), start, end)
        frame = build_validation_frame(bars, spec)
        min_obs = int(self.config.get("min_observations", self.config.get("validation_min_obs", 252)))
        if len(frame) < min_obs:
            return self._empty_report(
                spec,
                "insufficient_data",
                start,
                end,
                [f"insufficient observations: {len(frame)} < {min_obs}"],
                n_observations=len(frame),
                frame=frame,
            ), 1.0

        ic_by_date = self._rank_ic_by_date(frame)
        rank_ic = float(ic_by_date.mean()) if not ic_by_date.empty else 0.0
        rank_ic_std = float(ic_by_date.std(ddof=1)) if len(ic_by_date) > 1 else 0.0
        rank_ic_ir = rank_ic / rank_ic_std if rank_ic_std > 0 else 0.0
        p_value = self._two_sided_p_value(rank_ic, rank_ic_std, len(ic_by_date))
        long_short_spread = self._long_short_spread(frame)
        hit_rate = self._hit_rate(frame)
        factors = empty_factor_decomposition()

        return ValidationReport(
            strategy_id=spec.strategy_id,
            status="pending_fdr",
            rank_ic=rank_ic,
            rank_ic_ir=rank_ic_ir,
            ic_decay=(rank_ic,),
            fdr_adjusted_p=1.0,
            fdr_significant=False,
            ff_alpha_monthly=factors["ff_alpha_monthly"],
            ff_alpha_tstat=factors["ff_alpha_tstat"],
            ff_r2=factors["ff_r2"],
            long_short_spread=long_short_spread,
            hit_rate=hit_rate,
            data_start=self._date_value(frame, "signal_date", "min", start),
            data_end=self._date_value(frame, "signal_date", "max", end),
            n_observations=len(frame),
            errors=(),
        ), p_value

    def _with_fdr(self, report: ValidationReport, adjusted_p: float, significant: bool) -> ValidationReport:
        if report.status not in {"pending_fdr", "pass", "fail"}:
            return report
        errors = self._threshold_errors(report.rank_ic, significant, report.hit_rate)
        return replace(
            report,
            status="pass" if not errors else "fail",
            fdr_adjusted_p=adjusted_p,
            fdr_significant=significant,
            errors=tuple(errors),
        )

    def _threshold_errors(self, rank_ic: float, fdr_significant: bool, hit_rate: float) -> list[str]:
        thresholds = dict(self.config.get("thresholds", {}))
        min_abs_rank_ic = float(thresholds.get("min_abs_rank_ic", self.config.get("min_abs_rank_ic", 0.02)))
        min_hit_rate = float(thresholds.get("min_hit_rate", self.config.get("min_hit_rate", 0.52)))
        errors = []
        if abs(rank_ic) < min_abs_rank_ic:
            errors.append(f"abs(rank_ic) {abs(rank_ic):.4f} < {min_abs_rank_ic:.4f}")
        if not fdr_significant:
            errors.append("fdr significance threshold not met")
        if hit_rate < min_hit_rate:
            errors.append(f"hit_rate {hit_rate:.4f} < {min_hit_rate:.4f}")
        return errors

    def _max_fdr_p(self) -> float:
        thresholds = dict(self.config.get("thresholds", {}))
        return float(thresholds.get("max_fdr_p", self.config.get("max_fdr_p", 0.05)))

    @staticmethod
    def _rank_ic_by_date(frame: pd.DataFrame) -> pd.Series:
        values = []
        dates = []
        for date, group in frame.groupby("signal_date"):
            if len(group) < 2 or group["signal"].nunique() < 2 or group["forward_return"].nunique() < 2:
                continue
            value = group["signal"].rank().corr(group["forward_return"].rank())
            if pd.notna(value):
                dates.append(date)
                values.append(float(value))
        return pd.Series(values, index=dates, dtype="float64")

    @staticmethod
    def _two_sided_p_value(mean_ic: float, std_ic: float, n: int) -> float:
        if n <= 1 or std_ic <= 0:
            return 0.0 if abs(mean_ic) > 0 else 1.0
        t_stat = abs(mean_ic) / (std_ic / math.sqrt(n))
        return math.erfc(t_stat / math.sqrt(2.0))

    @staticmethod
    def _long_short_spread(frame: pd.DataFrame) -> float:
        spreads = []
        for _, group in frame.groupby("signal_date"):
            if len(group) < 2:
                continue
            ordered = group.sort_values("signal")
            spreads.append(float(ordered["forward_return"].iloc[-1] - ordered["forward_return"].iloc[0]))
        return float(pd.Series(spreads).mean()) if spreads else 0.0

    @staticmethod
    def _hit_rate(frame: pd.DataFrame) -> float:
        aligned = frame["signal"] * frame["forward_return"]
        if aligned.empty:
            return 0.0
        return float((aligned > 0).mean())

    @staticmethod
    def _date_value(frame: pd.DataFrame, column: str, op: str, fallback: str) -> str:
        if frame.empty or column not in frame.columns:
            return fallback
        value = getattr(frame[column], op)()
        return pd.Timestamp(value).date().isoformat()

    def _empty_report(
        self,
        spec: StrategySpec,
        status: str,
        start: str,
        end: str,
        errors: list[str],
        n_observations: int = 0,
        frame: Optional[pd.DataFrame] = None,
    ) -> ValidationReport:
        return ValidationReport(
            strategy_id=spec.strategy_id,
            status=status,
            rank_ic=0.0,
            rank_ic_ir=0.0,
            ic_decay=(),
            fdr_adjusted_p=1.0,
            fdr_significant=False,
            ff_alpha_monthly=0.0,
            ff_alpha_tstat=0.0,
            ff_r2=0.0,
            long_short_spread=0.0,
            hit_rate=0.0,
            data_start=self._date_value(frame, "signal_date", "min", start) if frame is not None else start,
            data_end=self._date_value(frame, "signal_date", "max", end) if frame is not None else end,
            n_observations=n_observations,
            errors=errors,
        )
