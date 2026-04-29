"""Tests for canonical symbol detection utilities."""
import pytest

from quant.shared.utils.symbol_utils import (
    is_cn_symbol,
    is_hk_symbol,
    detect_market,
    cn_price_limit_pct,
    normalize_symbol_for_backtest,
)


class TestIsCnSymbol:
    @pytest.mark.parametrize("sym", ["600519", "000001", "300750", "688981", "830799"])
    def test_cn_symbols(self, sym):
        assert is_cn_symbol(sym) is True

    @pytest.mark.parametrize("sym", ["00700", "AAPL", "0388", "1", "6005190", "60051"])
    def test_non_cn_symbols(self, sym):
        assert is_cn_symbol(sym) is False


class TestIsHkSymbol:
    @pytest.mark.parametrize("sym", ["00700", "00005", "0388", "001", "1", "HK.00700"])
    def test_hk_symbols(self, sym):
        assert is_hk_symbol(sym) is True

    @pytest.mark.parametrize("sym", ["600519", "AAPL"])
    def test_non_hk_symbols(self, sym):
        assert is_hk_symbol(sym) is False


class TestDetectMarket:
    @pytest.mark.parametrize("sym", ["600519", "000001", "300750", "688981", "830799"])
    def test_cn(self, sym):
        assert detect_market(sym) == "CN"

    @pytest.mark.parametrize("sym", ["00700", "0388", "1", "HK.00700", "HK.0388"])
    def test_hk(self, sym):
        assert detect_market(sym) == "HK"

    @pytest.mark.parametrize("sym", ["AAPL", "SPY", "US.AAPL"])
    def test_us(self, sym):
        assert detect_market(sym) == "US"


class TestCnPriceLimitPct:
    def test_star_market_20(self):
        assert cn_price_limit_pct("688981") == 0.20

    def test_chinext_20(self):
        assert cn_price_limit_pct("300750") == 0.20

    def test_bse_30(self):
        assert cn_price_limit_pct("830799") == 0.30

    def test_main_board_10(self):
        assert cn_price_limit_pct("600519") == 0.10

    def test_main_board_shenzhen_10(self):
        assert cn_price_limit_pct("000001") == 0.10


class TestNormalizeSymbolForBacktest:
    def test_cn_passthrough(self):
        assert normalize_symbol_for_backtest("600519") == "600519"

    def test_5digit_hk_gets_prefix(self):
        assert normalize_symbol_for_backtest("00700") == "HK.00700"

    def test_4digit_hk_gets_prefix_padded(self):
        assert normalize_symbol_for_backtest("0388") == "HK.00388"

    def test_3digit_hk_gets_prefix_padded(self):
        assert normalize_symbol_for_backtest("001") == "HK.00001"

    def test_1digit_hk_gets_prefix_padded(self):
        assert normalize_symbol_for_backtest("1") == "HK.00001"

    def test_us_alpha_gets_prefix(self):
        assert normalize_symbol_for_backtest("AAPL") == "US.AAPL"

    def test_prefixed_passthrough(self):
        assert normalize_symbol_for_backtest("HK.00700") == "HK.00700"
        assert normalize_symbol_for_backtest("US.AAPL") == "US.AAPL"
