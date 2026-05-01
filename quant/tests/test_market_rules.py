"""Unit tests for market_rules module."""

from datetime import date, datetime
import pytest

from quant.features.backtest.market_rules import (
    get_market,
    get_lot_size,
    is_price_at_limit,
    get_settled_quantity,
    select_currency,
    is_suspended,
    DEFAULT_LOT_SIZE,
    MARKET_CURRENCY,
    IPO_NO_LIMIT_CALENDAR_DAYS,
    fifo_lot_slices,
    get_earliest_lot_time,
)

from quant.domain.models.position import Position


class TestMarketDetection:
    def test_cn_main_board(self):
        assert get_market("600519") == "CN"

    def test_cn_shenzhen(self):
        assert get_market("000001") == "CN"

    def test_cn_chinext(self):
        assert get_market("300750") == "CN"

    def test_hk_five_digit(self):
        assert get_market("00700") == "HK"

    def test_hk_prefix(self):
        assert get_market("HK.00700") == "HK"

    def test_us_alpha(self):
        assert get_market("AAPL") == "US"


class TestLotSize:
    def test_us_always_1(self):
        assert get_lot_size("AAPL", {}) == 1
        assert get_lot_size("MSFT", {"MSFT": 100}) == 1

    def test_cn_default(self):
        assert get_lot_size("600519", {}) == 100

    def test_cn_custom_from_dict(self):
        assert get_lot_size("600519", {"600519": 200}) == 200

    def test_hk_from_dict(self):
        assert get_lot_size("HK.00700", {"HK.00700": 1000}) == 1000

    def test_zero_or_none_uses_default(self):
        assert get_lot_size("600519", {"600519": 0}) == 100
        assert get_lot_size("600519", {"600519": None}) == 100


class TestPriceLimit:
    def test_us_no_limit(self):
        assert is_price_at_limit("AAPL", 150.0, 100.0) is False

    def test_hk_no_limit(self):
        assert is_price_at_limit("00700", 150.0, 100.0) is False

    def test_cn_limit_up_10pct(self):
        prev_close = 100.0
        limit_price = prev_close * 1.10 + 0.01
        assert is_price_at_limit("600519", round(limit_price, 2), prev_close) is True

    def test_cn_limit_down_10pct(self):
        prev_close = 100.0
        limit_price = prev_close * 0.90 - 0.01
        assert is_price_at_limit("600519", round(limit_price, 2), prev_close) is True

    def test_cn_normal_price_passes(self):
        assert is_price_at_limit("600519", 100.0, 100.0) is False

    def test_cn_chinext_20pct(self):
        prev_close = 50.0
        limit_price = prev_close * 1.20 + 0.01
        assert is_price_at_limit("300750", round(limit_price, 2), prev_close) is True

    def test_cn_bse_30pct(self):
        prev_close = 10.0
        limit_price = prev_close * 1.30 + 0.01
        assert is_price_at_limit("830799", round(limit_price, 2), prev_close) is True

    def test_ipo_exempt(self):
        ipo_date = date(2025, 1, 2)
        prev_close = 100.0
        limit_price = prev_close * 1.10 + 0.05
        limit_rounded = round(limit_price, 2)
        assert is_price_at_limit(
            "600519", limit_rounded, prev_close,
            date(2025, 1, 3), {"600519": ipo_date}
        ) is False

    def test_ipo_no_exempt_after_grace_period(self):
        ipo_date = date(2025, 1, 2)
        prev_close = 100.0
        limit_price = prev_close * 1.10 + 0.05
        limit_rounded = round(limit_price, 2)
        assert is_price_at_limit(
            "600519", limit_rounded, prev_close,
            date(2025, 1, 12), {"600519": ipo_date}
        ) is True

    def test_zero_prev_close(self):
        assert is_price_at_limit("600519", 110.0, 0) is False


class TestSettlement:
    def test_us_t0(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert get_settled_quantity("AAPL", pos, date.today()) == 100

    def test_cn_t1_not_settled_same_day(self):
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        today = date(2025, 1, 2)
        pos.add_buy_lot(today, 1000)
        assert get_settled_quantity("600519", pos, today) == 0

    def test_cn_t1_settled_next_day(self):
        pos = Position(symbol="600519", quantity=1000, avg_cost=50.0)
        today = date(2025, 1, 2)
        pos.add_buy_lot(today, 1000)
        assert get_settled_quantity("600519", pos, date(2025, 1, 3)) == 1000


class TestCurrency:
    def test_single_cn(self):
        assert select_currency(["600519"]) == "CNY"

    def test_single_hk(self):
        assert select_currency(["HK.00700"]) == "HKD"

    def test_single_us(self):
        assert select_currency(["AAPL"]) == "USD"

    def test_mixed_falls_back_usd(self):
        assert select_currency(["600519", "AAPL"]) == "USD"

    def test_empty(self):
        assert select_currency([]) == "USD"


class TestSuspended:
    def test_zero_volume(self):
        assert is_suspended({"volume": 0, "open": 100, "close": 100}) is True

    def test_zero_close_and_open(self):
        assert is_suspended({"volume": 1000, "open": 0, "close": 0}) is True

    def test_normal_bar(self):
        assert is_suspended({"volume": 1000, "open": 100, "close": 100}) is False


class TestFIFO:
    def test_single_lot_full_sell(self):
        pos = Position(symbol="AAPL")
        pos.add_buy_lot(date(2025, 1, 2), 100, 150.0)
        slices = fifo_lot_slices(pos, 100)
        assert len(slices) == 1
        assert slices[0][0] == date(2025, 1, 2)
        assert slices[0][1] == 100
        assert slices[0][2] == 150.0

    def test_multi_lot_partial_sell(self):
        pos = Position(symbol="AAPL")
        pos.add_buy_lot(date(2025, 1, 2), 100, 150.0)
        pos.add_buy_lot(date(2025, 1, 3), 100, 160.0)
        slices = fifo_lot_slices(pos, 150)
        assert len(slices) == 2
        assert slices[0][1] == 100
        assert slices[0][2] == 150.0
        assert slices[1][1] == 50
        assert slices[1][2] == 160.0

    def test_exact_match(self):
        pos = Position(symbol="AAPL")
        pos.add_buy_lot(date(2025, 1, 2), 50, 150.0)
        pos.add_buy_lot(date(2025, 1, 3), 50, 160.0)
        slices = fifo_lot_slices(pos, 100)
        assert len(slices) == 2
        total_qty = sum(s[1] for s in slices)
        assert total_qty == 100


class TestEarliestLotTime:
    def test_with_lots(self):
        pos = Position(symbol="AAPL")
        pos.add_buy_lot(date(2025, 1, 2), 100, 150.0)
        pos.add_buy_lot(date(2025, 1, 5), 50, 160.0)
        earliest = get_earliest_lot_time(pos)
        assert earliest == datetime(2025, 1, 2)

    def test_no_lots(self):
        pos = Position(symbol="AAPL")
        assert get_earliest_lot_time(pos) is None


class TestRoundHalfUp:
    def test_cn_half_up_not_bankers(self):
        prev_close = 10.05
        assert is_price_at_limit("600519", round(prev_close * 1.10, 2), prev_close) is True

    def test_cn_exact_limit_rounded_up(self):
        prev_close = 33.33
        upper = prev_close * 1.10
        assert is_price_at_limit("600519", upper, prev_close) is True
