"""Invariant tests for domain models — Position, Trade."""
from datetime import date, datetime

import pytest

from quant.domain.models.position import Position
from quant.domain.models.trade import Trade


D1 = date(2025, 1, 2)
D2 = date(2025, 1, 3)
D3 = date(2025, 1, 4)


# ---------------------------------------------------------------------------
# CASE-1: Position BUY avg_cost
# ---------------------------------------------------------------------------

class TestCase1PositionBuyAvgCost:
    def test_d1_01_first_fill(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 150.0, D1)
        assert pos.quantity == pytest.approx(100)
        assert pos.avg_cost == pytest.approx(150.0)

    def test_d1_02_second_fill(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 150.0, D1)
        pos.update_from_fill(100, 170.0, D2)
        assert pos.quantity == pytest.approx(200)
        assert pos.avg_cost == pytest.approx(160.0)

    def test_d1_03_lots(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 150.0, D1)
        pos.update_from_fill(100, 170.0, D2)
        assert len(pos._lots) == 2
        assert pos._lots[D1].qty == pytest.approx(100)
        assert pos._lots[D1].price == pytest.approx(150.0)
        assert pos._lots[D2].qty == pytest.approx(100)
        assert pos._lots[D2].price == pytest.approx(170.0)


# ---------------------------------------------------------------------------
# CASE-2: Position SELL FIFO + realized_pnl
# ---------------------------------------------------------------------------

class TestCase2PositionSellFIFO:
    def test_d2_01_remaining_qty(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 100.0, D1)
        pos.update_from_fill(100, 120.0, D2)
        pos.update_from_fill(-150, 130.0)
        assert pos.quantity == pytest.approx(50)

    def test_d2_02_realized_pnl(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 100.0, D1)
        pos.update_from_fill(100, 120.0, D2)
        pos.update_from_fill(-150, 130.0)
        assert pos.realized_pnl == pytest.approx(3500.0)

    def test_d2_03_remaining_qty(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 100.0, D1)
        pos.update_from_fill(100, 120.0, D2)
        pos.update_from_fill(-150, 130.0)
        assert pos.quantity == pytest.approx(50)

    def test_d2_04_win_count(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 100.0, D1)
        pos.update_from_fill(100, 120.0, D2)
        pos.update_from_fill(-150, 130.0)
        assert pos.win_count == 2

    def test_d2_05_win_rate(self):
        pos = Position(symbol="AAPL")
        pos.update_from_fill(100, 100.0, D1)
        pos.update_from_fill(100, 120.0, D2)
        pos.update_from_fill(-150, 130.0)
        assert pos.win_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CASE-3: Position settled_quantity (T+1)
# ---------------------------------------------------------------------------

class TestCase3PositionSettled:
    def test_d3_01_same_day_zero(self):
        pos = Position(symbol="600519")
        pos.update_from_fill(100, 50.0, D1)
        assert pos.settled_quantity(D1) == pytest.approx(0)

    def test_d3_02_next_day_settled(self):
        pos = Position(symbol="600519")
        pos.update_from_fill(100, 50.0, D1)
        assert pos.settled_quantity(D2) == pytest.approx(100)

    def test_d3_03_stays_settled(self):
        pos = Position(symbol="600519")
        pos.update_from_fill(100, 50.0, D1)
        assert pos.settled_quantity(D3) == pytest.approx(100)


# ---------------------------------------------------------------------------
# CASE-4: Position full close
# ---------------------------------------------------------------------------

class TestCase4PositionFullClose:
    def test_d4_01_zero_state(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(D1, 100, 150.0)
        pos.update_from_fill(-100, 160.0)
        assert pos.quantity == pytest.approx(0, abs=1e-6)
        assert pos.avg_cost == pytest.approx(0, abs=1e-6)

    def test_d4_02_realized_correct(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(D1, 100, 150.0)
        pos.update_from_fill(-100, 160.0)
        assert pos.realized_pnl == pytest.approx(1000.0)

    def test_d4_03_is_flat(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(D1, 100, 150.0)
        pos.update_from_fill(-100, 160.0)
        assert pos.is_flat is True

    def test_d4_04_realized_pnl(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.add_buy_lot(D1, 100, 150.0)
        pos.update_from_fill(-100, 160.0)
        assert pos.realized_pnl == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# CASE-5: Position stock dividend
# ---------------------------------------------------------------------------

class TestCase5StockDividend:
    def test_d5_01_quantity_doubled(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_stock_dividend(1.0)
        assert pos.quantity == pytest.approx(200)

    def test_d5_02_lot_adjusted(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_stock_dividend(1.0)
        assert pos._lots[D1].qty == pytest.approx(200)
        assert pos._lots[D1].price == pytest.approx(50.0)

    def test_d5_03_avg_cost(self):
        pos = Position(symbol="600519", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_stock_dividend(1.0)
        assert pos.avg_cost == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# CASE-6: Position cash dividend
# ---------------------------------------------------------------------------

class TestCase6CashDividend:
    def test_d6_01_quantity_unchanged(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_cash_dividend(2.0)
        assert pos.quantity == pytest.approx(100)

    def test_d6_02_lot_price_adjusted(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_cash_dividend(2.0)
        assert pos._lots[D1].price == pytest.approx(98.0)

    def test_d6_03_avg_cost(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=100.0)
        pos.add_buy_lot(D1, 100, 100.0)
        pos.adjust_lots_for_cash_dividend(2.0)
        assert pos.avg_cost == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# CASE-7: Trade.from_entry_exit
# ---------------------------------------------------------------------------

class TestCase7TradeCalculation:
    def test_d7_01_pnl(self):
        t = Trade.from_entry_exit(
            "AAPL", 100, 150.0, 160.0,
            datetime(2025, 1, 2), datetime(2025, 1, 3),
            "SELL", commission=10.0,
        )
        assert t.pnl == pytest.approx(990.0)

    def test_d7_02_realized_equals_pnl(self):
        t = Trade.from_entry_exit(
            "AAPL", 100, 150.0, 160.0,
            datetime(2025, 1, 2), datetime(2025, 1, 3),
            "SELL", commission=10.0,
        )
        assert t.realized_pnl == pytest.approx(t.pnl)

    def test_d7_03_is_win(self):
        t = Trade.from_entry_exit(
            "AAPL", 100, 150.0, 160.0,
            datetime(2025, 1, 2), datetime(2025, 1, 3),
            "SELL", commission=10.0,
        )
        assert t.is_win is True

    def test_d7_04_duration(self):
        t = Trade.from_entry_exit(
            "AAPL", 100, 150.0, 160.0,
            datetime(2025, 1, 2), datetime(2025, 1, 3),
            "SELL", commission=10.0,
        )
        assert t.duration_days == pytest.approx(1.0)
