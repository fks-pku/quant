"""Unit tests for commission module."""

import pytest

from quant.features.backtest.commission import (
    calculate_commission,
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
    CN_STAMP_DUTY_RATE,
    CN_TRANSFER_FEE_RATE,
    CN_REGULATOR_FEE_RATE,
    CN_MIN_COMMISSION,
    US_SEC_FEE_RATE,
    US_FINRA_TAF_PER_SHARE,
    VOLUME_PARTICIPATION_LIMIT,
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

    def test_buy_no_stamp_duty(self):
        breakdown = _calculate_hk_commission(100000, "BUY")
        assert breakdown["stamp_duty"] == 0.0

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

    def test_hk_symbol_uses_hk_rules(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("00700", 100.0, 1000, "BUY", cfg)
        assert "sfc_levy" in breakdown
        assert "system_fee" in breakdown

    def test_us_symbol_uses_us_rules(self):
        cfg = CommissionConfig()
        breakdown = calculate_commission("AAPL", 150.0, 100, "BUY", cfg)
        assert isinstance(breakdown["commission"], float)
