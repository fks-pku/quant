"""Standalone data validation for backtest input — pure functions, zero internal dependencies."""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
MAX_DAILY_PRICE_CHANGE_PCT = 0.50
CONSECUTIVE_SAME_CLOSE_WINDOW = 20


@dataclass
class ValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            lines.extend(f"  - {e}" for e in self.errors)
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            lines.extend(f"  - {w}" for w in self.warnings)
        if self.stats:
            lines.append("STATS:")
            for k, v in self.stats.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines) if lines else "All checks passed."


class DataValidator:

    @staticmethod
    def validate(data: pd.DataFrame) -> ValidationReport:
        report = ValidationReport()
        if DataValidator._check_empty(data, report):
            return report
        DataValidator._check_columns(data, report)
        if not REQUIRED_COLUMNS.issubset(data.columns):
            DataValidator._collect_stats(data, report)
            return report
        DataValidator._check_timestamp(data, report)
        DataValidator._check_duplicates(data, report)
        DataValidator._check_negative_prices(data, report)
        DataValidator._check_ohlc_logic(data, report)
        DataValidator._check_zero_close(data, report)
        DataValidator._check_volume_sanity(data, report)
        DataValidator._check_date_gaps(data, report)
        DataValidator._check_price_jumps(data, report)
        DataValidator._check_consecutive_same_close(data, report)
        DataValidator._collect_stats(data, report)
        return report

    @staticmethod
    def preflight(data: pd.DataFrame) -> ValidationReport:
        report = DataValidator.validate(data)
        if report.errors:
            raise ValueError(
                "Data preflight failed:\n" + "\n".join(f"  - {e}" for e in report.errors)
            )
        return report

    @staticmethod
    def _check_empty(data: pd.DataFrame, report: ValidationReport) -> bool:
        if data is None or data.empty:
            report.errors.append("Data is empty or None")
            return True
        return False

    @staticmethod
    def _check_columns(data: pd.DataFrame, report: ValidationReport) -> None:
        missing = REQUIRED_COLUMNS - set(data.columns)
        if missing:
            report.errors.append(f"Missing required columns: {sorted(missing)}")

    @staticmethod
    def _check_timestamp(data: pd.DataFrame, report: ValidationReport) -> None:
        if "timestamp" not in data.columns:
            return
        ts = data["timestamp"]
        null_count = ts.isna().sum()
        if null_count > 0:
            report.errors.append(f"Timestamp has {null_count} null values")

        if not pd.api.types.is_datetime64_any_dtype(ts):
            try:
                pd.to_datetime(ts)
            except Exception:
                report.errors.append("Timestamp column cannot be parsed as datetime")

    @staticmethod
    def _check_duplicates(data: pd.DataFrame, report: ValidationReport) -> None:
        if "timestamp" not in data.columns or "symbol" not in data.columns:
            return
        if not pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
            ts = pd.to_datetime(data["timestamp"])
        else:
            ts = data["timestamp"]

        keys = data["symbol"].astype(str) + "_" + ts.dt.date.astype(str)
        dup_mask = keys.duplicated(keep=False)
        dup_count = dup_mask.sum()
        if dup_count > 0:
            dup_symbols = data.loc[dup_mask, "symbol"].unique().tolist()
            report.errors.append(
                f"Duplicate (symbol, date) found: {dup_count} rows affected, "
                f"symbols: {dup_symbols[:5]}{'...' if len(dup_symbols) > 5 else ''}"
            )

    @staticmethod
    def _check_negative_prices(data: pd.DataFrame, report: ValidationReport) -> None:
        for col in ("open", "high", "low", "close"):
            if col not in data.columns:
                continue
            neg_count = (data[col] < 0).sum()
            if neg_count > 0:
                report.errors.append(f"Negative {col}: {neg_count} rows")

    @staticmethod
    def _check_ohlc_logic(data: pd.DataFrame, report: ValidationReport) -> None:
        required = {"open", "high", "low", "close"}
        if not required.issubset(data.columns):
            return

        high_lt_low = data["high"] < data["low"]
        if high_lt_low.any():
            report.errors.append(f"high < low: {high_lt_low.sum()} rows")

        oc = data[["open", "close"]]
        high_lt_oc = data["high"] < oc.max(axis=1)
        if high_lt_oc.any():
            report.errors.append(f"high < max(open, close): {high_lt_oc.sum()} rows")

        low_gt_oc = data["low"] > oc.min(axis=1)
        if low_gt_oc.any():
            report.errors.append(f"low > min(open, close): {low_gt_oc.sum()} rows")

    @staticmethod
    def _check_zero_close(data: pd.DataFrame, report: ValidationReport) -> None:
        if "close" not in data.columns:
            return
        zero_count = (data["close"] == 0).sum()
        if zero_count > 0:
            report.warnings.append(f"Zero close price: {zero_count} rows (possible suspension)")

    @staticmethod
    def _check_volume_sanity(data: pd.DataFrame, report: ValidationReport) -> None:
        if "volume" not in data.columns:
            return
        neg_count = (data["volume"] < 0).sum()
        if neg_count > 0:
            report.errors.append(f"Negative volume: {neg_count} rows")

        zero_count = (data["volume"] == 0).sum()
        if zero_count > 0:
            total = len(data)
            pct = zero_count / total * 100
            report.warnings.append(
                f"Zero volume: {zero_count} rows ({pct:.1f}% of data, possible suspension)"
            )

    @staticmethod
    def _check_date_gaps(data: pd.DataFrame, report: ValidationReport) -> None:
        if "timestamp" not in data.columns or "symbol" not in data.columns:
            return

        if not pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
            ts = pd.to_datetime(data["timestamp"])
        else:
            ts = data["timestamp"]

        for symbol in data["symbol"].unique():
            sym_ts = ts[data["symbol"] == symbol].dropna().sort_values().reset_index(drop=True)
            if len(sym_ts) < 2:
                continue
            dates = sym_ts.dt.date
            gaps = dates.diff().dropna()
            large_gaps = gaps[gaps.apply(lambda d: d.days > 3 if hasattr(d, "days") else False)]
            if not large_gaps.empty:
                report.warnings.append(
                    f"{symbol}: {len(large_gaps)} date gap(s) > 3 calendar days "
                    f"(max {large_gaps.max().days} days)"
                )

    @staticmethod
    def _check_price_jumps(data: pd.DataFrame, report: ValidationReport) -> None:
        if not {"timestamp", "symbol", "close"}.issubset(data.columns):
            return

        if not pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
            ts = pd.to_datetime(data["timestamp"])
        else:
            ts = data["timestamp"]

        for symbol in data["symbol"].unique():
            mask = data["symbol"] == symbol
            sym_data = data.loc[mask].copy()
            sym_data["_ts"] = ts[mask]
            sym_data = sym_data.sort_values("_ts")
            close = sym_data["close"]
            prev_close = close.shift(1)
            pct_change = (close - prev_close) / prev_close.replace(0, np.nan).abs()
            extreme = pct_change.abs() > MAX_DAILY_PRICE_CHANGE_PCT
            extreme_count = extreme.sum()
            if extreme_count > 0:
                report.warnings.append(
                    f"{symbol}: {extreme_count} day(s) with >{MAX_DAILY_PRICE_CHANGE_PCT*100:.0f}% "
                    f"price change (possible stock split or data error)"
                )

    @staticmethod
    def _check_consecutive_same_close(data: pd.DataFrame, report: ValidationReport) -> None:
        if not {"timestamp", "symbol", "close", "volume"}.issubset(data.columns):
            return

        if not pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
            ts = pd.to_datetime(data["timestamp"])
        else:
            ts = data["timestamp"]

        for symbol in data["symbol"].unique():
            mask = data["symbol"] == symbol
            sym_data = data.loc[mask].copy()
            sym_data["_ts"] = ts[mask]
            sym_data = sym_data.sort_values("_ts")

            same_close = (
                (sym_data["close"] == sym_data["close"].shift(1))
                & (sym_data["volume"] > 0)
            )
            consecutive = same_close.rolling(CONSECUTIVE_SAME_CLOSE_WINDOW).sum()
            if (consecutive >= CONSECUTIVE_SAME_CLOSE_WINDOW).any():
                report.warnings.append(
                    f"{symbol}: {CONSECUTIVE_SAME_CLOSE_WINDOW}+ consecutive same close "
                    f"with volume > 0 (possible data padding)"
                )

    @staticmethod
    def _collect_stats(data: pd.DataFrame, report: ValidationReport) -> None:
        report.stats["total_rows"] = len(data)
        if "symbol" in data.columns:
            report.stats["symbols"] = sorted(data["symbol"].unique().tolist())

        if "timestamp" in data.columns:
            try:
                if not pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
                    ts = pd.to_datetime(data["timestamp"])
                else:
                    ts = data["timestamp"]
            except Exception:
                return
            report.stats["date_range"] = (
                str(ts.min().date()) + " ~ " + str(ts.max().date())
            )
            report.stats["unique_dates"] = ts.dt.date.nunique()

        if "symbol" in data.columns:
            report.stats["rows_per_symbol"] = (
                data.groupby("symbol").size().to_dict()
            )
