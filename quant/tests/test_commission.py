"""Unit tests for commission module."""

import datetime as dt
import math

import pytest

from quant.features.backtest.commission import (
    calculate_commission,
    get_rate_for_date,
    _calculate_cn_commission,
    _calculate_hk_commission,
    _calculate_us_commission,
    HK_COMMISSION_RATE,
    HK_STAMP_DUTY_RATE,
    HK_SFC_LEVY_RATE,
    HK_CLEARING_RATE,
    HK_TRADING_FEE_RATE,
    HK_MIN_COMMISSION,
    HK_TRADING_SYSTEM_FEE,
    CN_COMMISSION_RATE,
    CN_FUND_MIN_COMMISSION,
    CN_STAMP_DUTY_RATE,
    CN_TRANSFER_FEE_RATE,
    CN_REGULATOR_FEE_RATE,
    CN_MIN_COMMISSION,
    US_SEC_FEE_RATE,
    US_FINRA_TAF_PER_SHARE,
    VOLUME_PARTICIPATION_LIMIT,
    CN_STAMP_DUTY_RATE_HISTORY,
    HK_STAMP_DUTY_RATE_HISTORY,
)
from quant.features.backtest.entities import CommissionConfig


class TestCNCommission:
    def test_buy_min_floor(self):
        breakdown = _calculate_cn_commission(100, "BUY")
        assert breakdown["commission"] == CN_MIN_COMMISSION

    def test_buy_above_min(self):
        price, qty = 50.0, 1000
        expected = max(price * qty * CN_COMMISSION_RATE, CN_MIN_COMMISSION)
        breakdown = _calculate_cn_commission(price * qty, "BUY")
        assert breakdown["commission"] == pytest.approx(expected, rel=1e-4)

    def test_buy_no_stamp_duty(self):
        breakdown = _calculate_cn_commission(50.0 * 1000, "BUY")
        assert breakdown["stamp_duty"] == 0.0

    def test_sell_stamp_duty(self):
        price, qty = 50.0, 1000
        breakdown = _calculate_cn_commission(price * qty, "SELL")
        expected = price * qty * CN_STAMP_DUTY_RATE
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_transfer_fee(self):
        breakdown = _calculate_cn_commission(50.0 * 1000, "BUY")
        expected = 50.0 * 1000 * CN_TRANSFER_FEE_RATE
        assert breakdown["transfer_fee"] == pytest.approx(expected, rel=1e-4)

    def test_regulator_fee(self):
        breakdown = _calculate_cn_commission(50.0 * 1000, "BUY")
        expected = 50.0 * 1000 * CN_REGULATOR_FEE_RATE
        assert breakdown["regulator_fee"] == pytest.approx(expected, rel=1e-4)

    def test_four_fee_keys(self):
        breakdown = _calculate_cn_commission(50000, "BUY")
        assert {"commission", "stamp_duty", "transfer_fee", "regulator_fee"}.issubset(set(breakdown.keys()))


class TestHKCommission:
    def test_min_floor(self):
        breakdown = _calculate_hk_commission(100, "BUY")
        assert breakdown["commission"] == HK_MIN_COMMISSION

    def test_buy_has_stamp_duty(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        expected = math.ceil(100000 * HK_STAMP_DUTY_RATE)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_sell_stamp_duty(self):
        breakdown = _calculate_hk_commission(100000, "SELL")
        expected = 100000 * HK_STAMP_DUTY_RATE
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_sfc_levy(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        expected = 100000 * HK_SFC_LEVY_RATE
        assert breakdown["sfc_levy"] == pytest.approx(expected, rel=1e-4)

    def test_clearing(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        expected = 100000 * HK_CLEARING_RATE
        assert breakdown["clearing"] == pytest.approx(expected, rel=1e-4)

    def test_trading_fee(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        expected = 100000 * HK_TRADING_FEE_RATE
        assert breakdown["trading_fee"] == pytest.approx(expected, rel=1e-4)

    def test_system_fee_fixed(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        assert breakdown["system_fee"] == HK_TRADING_SYSTEM_FEE

    def test_six_fee_keys(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        assert {"commission", "stamp_duty", "sfc_levy", "clearing", "trading_fee", "system_fee"}.issubset(set(breakdown.keys()))


class TestUSCommission:
    def test_per_share_rate(self):
        qty = 1000
        cfg = CommissionConfig()
        breakdown = _calculate_us_commission(qty, qty * 150.0, "BUY", cfg)
        expected = qty * 0.005
        assert breakdown["commission"] == pytest.approx(expected, rel=1e-4)

    def test_min_floor(self):
        cfg = CommissionConfig()
        breakdown = _calculate_us_commission(1, 150.0, "BUY", cfg)
        assert breakdown["commission"] == 1.0

    def test_buy_no_sec_fee(self):
        cfg = CommissionConfig()
        breakdown = _calculate_us_commission(1000, 1000 * 150.0, "BUY", cfg)
        assert "sec_fee" not in breakdown
        assert "finra_taf" not in breakdown

    def test_sell_sec_fee(self):
        qty = 1000
        trade_value = qty * 150.0
        cfg = CommissionConfig()
        breakdown = _calculate_us_commission(qty, trade_value, "SELL", cfg)
        expected = max(trade_value * US_SEC_FEE_RATE, 0.0)
        assert breakdown["sec_fee"] == pytest.approx(expected, rel=1e-4)

    def test_sell_finra_taf(self):
        qty = 1000
        cfg = CommissionConfig()
        breakdown = _calculate_us_commission(qty, qty * 150.0, "SELL", cfg)
        expected = qty * US_FINRA_TAF_PER_SHARE
        assert breakdown["finra_taf"] == pytest.approx(expected, rel=1e-4)

    def test_percent_mode(self):
        qty = 100
        trade_value = qty * 150.0
        cfg = CommissionConfig(US={"type": "percent", "percent": 0.001, "min_per_order": 1.0})
        breakdown = _calculate_us_commission(qty, trade_value, "BUY", cfg)
        expected = trade_value * 0.001
        assert breakdown["commission"] == pytest.approx(expected, rel=1e-4)


class TestCalculateCommissionBySymbol:
    def test_cn_symbol_uses_cn_rules(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("600519", 50.0, 1000, "BUY", cfg)
        assert "transfer_fee" in breakdown
        assert "regulator_fee" in breakdown

    def test_cn_etf_sell_exempts_stock_stamp_duty(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("159915", 10.0, 1000, "SELL", cfg)
        assert breakdown["stamp_duty"] == 0.0
        assert breakdown["transfer_fee"] == 0.0
        assert breakdown["regulator_fee"] == 0.0

    def test_cn_lof_sell_exempts_stock_stamp_duty(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("501018", 10.0, 1000, "SELL", cfg)
        assert breakdown["stamp_duty"] == 0.0
        assert breakdown["transfer_fee"] == 0.0
        assert breakdown["regulator_fee"] == 0.0

    def test_cn_fund_commission_keeps_live_minimum_when_configured_lower(self):
        cfg = CommissionConfig(CN={"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0})
        breakdown = calculate_commission("510300", 10.0, 1000, "BUY", cfg)
        assert breakdown["commission"] == pytest.approx(CN_FUND_MIN_COMMISSION, rel=1e-4)

    def test_default_cn_fund_commission_uses_realistic_minimum(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("510300", 10.0, 100, "BUY", cfg)
        assert breakdown["commission"] == pytest.approx(5.0)

    def test_hk_symbol_uses_hk_rules(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("00700", 100.0, 1000, "BUY", cfg)
        assert "sfc_levy" in breakdown
        assert "system_fee" in breakdown

    def test_us_symbol_uses_us_rules(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("AAPL", 150.0, 100, "BUY", cfg)
        assert isinstance(breakdown["commission"], float)

    def test_volume_participation_limit_constant(self):
        from quant.features.backtest.commission import VOLUME_PARTICIPATION_LIMIT
        assert VOLUME_PARTICIPATION_LIMIT == 0.05


class TestDateAwareCNStampDuty:
    """CN stamp duty: 0.1% before 2023-08-28, halved to 0.05% after."""

    def test_stamp_duty_before_cutoff(self):
        trade_date = dt.date(2023, 8, 27)
        price, qty = 50.0, 1000
        breakdown = _calculate_cn_commission(price * qty, "SELL", trade_date=trade_date)
        expected = price * qty * 0.001
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_on_cutoff(self):
        trade_date = dt.date(2023, 8, 28)
        price, qty = 50.0, 1000
        breakdown = _calculate_cn_commission(price * qty, "SELL", trade_date=trade_date)
        expected = price * qty * 0.0005
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_after_cutoff(self):
        trade_date = dt.date(2024, 6, 15)
        price, qty = 50.0, 1000
        breakdown = _calculate_cn_commission(price * qty, "SELL", trade_date=trade_date)
        expected = price * qty * 0.0005
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_no_stamp_duty_for_buy_regardless_of_date(self):
        breakdown_before = _calculate_cn_commission(50000, "BUY", trade_date=dt.date(2023, 8, 27))
        breakdown_after = _calculate_cn_commission(50000, "BUY", trade_date=dt.date(2024, 1, 15))
        assert breakdown_before["stamp_duty"] == 0.0
        assert breakdown_after["stamp_duty"] == 0.0


class TestDateAwareHKStampDuty:
    """HK stamp duty: 0.1% before 2021-08-01, raised to 0.13% until 2023-11-16, back to 0.1%."""

    def test_stamp_duty_before_first_raise(self):
        trade_date = dt.date(2020, 6, 1)
        breakdown = _calculate_hk_commission(100000, "BUY", trade_date=trade_date)
        expected = math.ceil(100000 * 0.001)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_during_raised_period(self):
        trade_date = dt.date(2022, 6, 1)
        breakdown = _calculate_hk_commission(100000, "BUY", trade_date=trade_date)
        expected = math.ceil(100000 * 0.0013)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_after_reduction(self):
        trade_date = dt.date(2024, 6, 1)
        breakdown = _calculate_hk_commission(100000, "BUY", trade_date=trade_date)
        expected = math.ceil(100000 * 0.001)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_transition_on_raise_date(self):
        breakdown = _calculate_hk_commission(100000, "BUY", trade_date=dt.date(2021, 8, 1))
        expected = math.ceil(100000 * 0.0013)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)

    def test_stamp_duty_transition_on_reduction_date(self):
        breakdown = _calculate_hk_commission(100000, "BUY", trade_date=dt.date(2023, 11, 17))
        expected = math.ceil(100000 * 0.001)
        assert breakdown["stamp_duty"] == pytest.approx(expected, rel=1e-4)


class TestGetRateForDate:
    """Unit tests for the get_rate_for_date helper function."""

    def test_returns_default_when_table_empty(self):
        rate = get_rate_for_date({}, dt.date(2023, 1, 1), 0.005)
        assert rate == 0.005

    def test_returns_latest_when_trade_date_none(self):
        table = {
            dt.date(2020, 1, 1): 0.001,
            dt.date(2023, 8, 28): 0.0005,
        }
        rate = get_rate_for_date(table, None, 0.001)
        assert rate == 0.0005

    def test_returns_default_when_no_entry_before_date(self):
        table = {dt.date(2023, 8, 28): 0.0005}
        rate = get_rate_for_date(table, dt.date(2020, 1, 1), 0.001)
        assert rate == 0.001

    def test_returns_correct_rate_for_date_between_entries(self):
        table = {
            dt.date(2021, 8, 1): 0.0013,
            dt.date(2023, 11, 17): 0.001,
        }
        rate = get_rate_for_date(table, dt.date(2022, 6, 1), 0.001)
        assert rate == 0.0013

    def test_returns_correct_rate_on_entry_date(self):
        table = {dt.date(2023, 8, 28): 0.0005}
        rate = get_rate_for_date(table, dt.date(2023, 8, 28), 0.001)
        assert rate == 0.0005
