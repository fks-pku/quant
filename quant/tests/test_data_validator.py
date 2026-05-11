"""Tests for data_validator.py — pure data validation module."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.features.backtest.data_validator import DataValidator, ValidationReport


START = datetime(2025, 1, 2)


def _make_good_data(n_days=10, symbol="AAPL", start_price=150.0):
    rows = []
    price = start_price
    for i in range(n_days):
        ts = START + timedelta(days=i)
        price += 0.5
        rows.append({
            "symbol": symbol,
            "timestamp": ts,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 1000000,
        })
    return pd.DataFrame(rows)


class TestValidationReport:
    def test_ok_when_no_errors(self):
        r = ValidationReport()
        assert r.ok is True
        assert r.has_warnings is False

    def test_not_ok_with_errors(self):
        r = ValidationReport(errors=["bad"])
        assert r.ok is False

    def test_has_warnings(self):
        r = ValidationReport(warnings=["hmm"])
        assert r.ok is True
        assert r.has_warnings is True

    def test_summary(self):
        r = ValidationReport(errors=["e1"], warnings=["w1"], stats={"rows": 10})
        s = r.summary()
        assert "e1" in s
        assert "w1" in s
        assert "rows: 10" in s


class TestCheckEmpty:
    def test_none_data(self):
        report = DataValidator.validate(None)
        assert not report.ok
        assert any("empty" in e.lower() for e in report.errors)

    def test_empty_df(self):
        report = DataValidator.validate(pd.DataFrame())
        assert not report.ok

    def test_good_data_passes(self):
        report = DataValidator.validate(_make_good_data())
        assert report.ok


class TestCheckColumns:
    def test_missing_required_columns(self):
        df = pd.DataFrame({"timestamp": [START], "symbol": ["AAPL"], "close": [100.0]})
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("Missing required columns" in e for e in report.errors)

    def test_missing_symbol_returns_report_without_exception(self):
        df = pd.DataFrame({
            "timestamp": [START],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000000],
        })
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("symbol" in e for e in report.errors)

    def test_missing_timestamp_returns_report_without_exception(self):
        df = pd.DataFrame({
            "symbol": ["AAPL"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000000],
        })
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("timestamp" in e for e in report.errors)

    def test_extra_columns_ok(self):
        df = _make_good_data()
        df["extra"] = 42
        report = DataValidator.validate(df)
        assert report.ok


class TestCheckTimestamp:
    def test_null_timestamp(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "timestamp"] = pd.NaT
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("null" in e.lower() for e in report.errors)

    def test_unparseable_timestamp_returns_report(self):
        df = _make_good_data(n_days=1)
        df["timestamp"] = df["timestamp"].astype(object)
        df.loc[0, "timestamp"] = "bad-date"
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("cannot be parsed" in e for e in report.errors)


class TestCheckDuplicates:
    def test_duplicate_symbol_date(self):
        df = _make_good_data(n_days=5)
        dup_row = df.iloc[[0]].copy()
        df = pd.concat([df, dup_row], ignore_index=True)
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("Duplicate" in e for e in report.errors)

    def test_same_date_different_symbol_ok(self):
        df1 = _make_good_data(n_days=5, symbol="AAPL")
        df2 = _make_good_data(n_days=5, symbol="MSFT")
        df = pd.concat([df1, df2], ignore_index=True)
        report = DataValidator.validate(df)
        assert report.ok

    def test_different_dates_same_symbol_ok(self):
        report = DataValidator.validate(_make_good_data(n_days=10))
        assert report.ok


class TestCheckNegativePrices:
    def test_negative_open(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "open"] = -1.0
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("Negative open" in e for e in report.errors)

    def test_negative_close(self):
        df = _make_good_data(n_days=3)
        df.loc[1, "close"] = -5.0
        report = DataValidator.validate(df)
        assert not report.ok

    def test_negative_high(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "high"] = -10.0
        report = DataValidator.validate(df)
        assert not report.ok

    def test_negative_low(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "low"] = -1.0
        report = DataValidator.validate(df)
        assert not report.ok

    def test_nan_price_is_error(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "open"] = np.nan
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("open" in e and "NaN" in e for e in report.errors)

    def test_non_numeric_price_returns_report(self):
        df = _make_good_data(n_days=3)
        df["open"] = df["open"].astype(object)
        df.loc[0, "open"] = "bad"
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("open" in e and "numeric" in e for e in report.errors)


class TestCheckOHLCLogic:
    def test_high_less_than_low(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "high"] = 100.0
        df.loc[0, "low"] = 200.0
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("high < low" in e for e in report.errors)

    def test_high_less_than_close(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "high"] = df.loc[0, "close"] - 10
        report = DataValidator.validate(df)
        assert not report.ok

    def test_low_greater_than_open(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "low"] = df.loc[0, "open"] + 50
        report = DataValidator.validate(df)
        assert not report.ok


class TestCheckZeroClose:
    def test_zero_close_is_warning(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "close"] = 0.0
        df.loc[0, "low"] = 0.0
        df.loc[0, "high"] = df.loc[0, "open"]
        report = DataValidator.validate(df)
        assert report.ok
        assert any("Zero close" in w for w in report.warnings)


class TestCheckVolumeSanity:
    def test_negative_volume_is_error(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "volume"] = -1000
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("Negative volume" in e for e in report.errors)

    def test_non_numeric_volume_returns_report(self):
        df = _make_good_data(n_days=3)
        df["volume"] = df["volume"].astype(object)
        df.loc[0, "volume"] = "bad"
        report = DataValidator.validate(df)
        assert not report.ok
        assert any("volume" in e and "numeric" in e for e in report.errors)

    def test_zero_volume_is_warning(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "volume"] = 0
        report = DataValidator.validate(df)
        assert report.ok
        assert any("Zero volume" in w for w in report.warnings)


class TestCheckDateGaps:
    def test_large_gap_is_warning(self):
        rows = []
        for i, day_offset in enumerate([0, 1, 2, 30, 31]):
            ts = START + timedelta(days=day_offset)
            rows.append({
                "symbol": "AAPL", "timestamp": ts,
                "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0,
                "volume": 1000000,
            })
        df = pd.DataFrame(rows)
        report = DataValidator.validate(df)
        assert report.ok
        assert any("gap" in w.lower() for w in report.warnings)

    def test_continuous_dates_no_gap_warning(self):
        report = DataValidator.validate(_make_good_data(n_days=5))
        gap_warnings = [w for w in report.warnings if "gap" in w.lower()]
        assert len(gap_warnings) == 0


class TestCheckPriceJumps:
    def test_extreme_jump_is_warning(self):
        df = _make_good_data(n_days=5)
        new_close = df.loc[1, "close"] * 3
        df.loc[2, "close"] = new_close
        df.loc[2, "high"] = new_close + 1
        df.loc[2, "low"] = new_close - 1
        df.loc[2, "open"] = new_close
        report = DataValidator.validate(df)
        assert report.ok
        assert any("price change" in w.lower() for w in report.warnings)

    def test_normal_change_no_jump_warning(self):
        df = _make_good_data(n_days=10, start_price=100.0)
        jump_warnings = [w for w in DataValidator.validate(df).warnings if "price change" in w.lower()]
        assert len(jump_warnings) == 0


class TestCheckConsecutiveSameClose:
    def test_consecutive_same_close_warning(self):
        rows = []
        for i in range(25):
            ts = START + timedelta(days=i)
            rows.append({
                "symbol": "AAPL", "timestamp": ts,
                "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0,
                "volume": 1000000,
            })
        df = pd.DataFrame(rows)
        report = DataValidator.validate(df)
        assert report.ok
        assert any("consecutive" in w.lower() for w in report.warnings)

    def test_different_close_no_warning(self):
        report = DataValidator.validate(_make_good_data(n_days=25, start_price=100.0))
        consec_warnings = [w for w in report.warnings if "consecutive" in w.lower()]
        assert len(consec_warnings) == 0


class TestCollectStats:
    def test_stats_populated(self):
        report = DataValidator.validate(_make_good_data(n_days=10, symbol="AAPL"))
        assert report.stats["total_rows"] == 10
        assert report.stats["symbols"] == ["AAPL"]
        assert "date_range" in report.stats
        assert report.stats["unique_dates"] == 10

    def test_multi_symbol_stats(self):
        df1 = _make_good_data(n_days=5, symbol="AAPL")
        df2 = _make_good_data(n_days=5, symbol="MSFT")
        df = pd.concat([df1, df2], ignore_index=True)
        report = DataValidator.validate(df)
        assert report.stats["total_rows"] == 10
        assert sorted(report.stats["symbols"]) == ["AAPL", "MSFT"]

    def test_collect_stats_tolerates_missing_optional_columns(self):
        report = ValidationReport()
        df = pd.DataFrame({"close": [100.0]})
        DataValidator._collect_stats(df, report)
        assert report.stats["total_rows"] == 1
        assert "symbols" not in report.stats


class TestPreflight:
    def test_preflight_raises_on_error(self):
        df = pd.DataFrame({"timestamp": [START], "symbol": ["AAPL"], "close": [100.0]})
        with pytest.raises(ValueError, match="preflight failed"):
            DataValidator.preflight(df)

    def test_preflight_passes_on_good_data(self):
        report = DataValidator.preflight(_make_good_data())
        assert report.ok

    def test_preflight_passes_on_warnings(self):
        df = _make_good_data(n_days=3)
        df.loc[0, "close"] = 0.0
        df.loc[0, "low"] = 0.0
        df.loc[0, "high"] = df.loc[0, "open"]
        report = DataValidator.preflight(df)
        assert report.ok
        assert report.has_warnings


class TestEdgeCases:
    def test_single_row(self):
        df = pd.DataFrame([{
            "symbol": "AAPL", "timestamp": START,
            "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0,
            "volume": 1000000,
        }])
        report = DataValidator.validate(df)
        assert report.ok
        assert report.stats["total_rows"] == 1

    def test_timestamp_as_string_parsable(self):
        df = _make_good_data(n_days=3)
        df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        report = DataValidator.validate(df)
        assert report.ok

    def test_multiple_errors_reported(self):
        df = pd.DataFrame([{
            "symbol": "AAPL", "timestamp": START,
            "open": -1.0, "high": -2.0, "low": -3.0, "close": -4.0,
            "volume": -100,
        }])
        report = DataValidator.validate(df)
        assert len(report.errors) >= 2
