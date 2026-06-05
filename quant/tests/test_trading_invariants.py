"""Invariant tests for trading module — Portfolio, SubPortfolio, RiskEngine."""
from datetime import date, datetime, timedelta

import pytest

from quant.features.trading.portfolio import Portfolio
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.features.trading.risk import RiskEngine


D1 = date(2025, 1, 2)
D2 = date(2025, 1, 3)
D3 = date(2025, 1, 4)


# ---------------------------------------------------------------------------
# CASE-1: Portfolio BUY-HOLD-SELL
# ---------------------------------------------------------------------------

class TestCase1PortfolioBuyHoldSell:
    def test_t1_01_buy_creates_position(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 150.0, 15000.0, trade_date=D1)
        pos = pf.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == pytest.approx(100)
        assert pos.avg_cost == pytest.approx(150.0)

    def test_t1_02_price_update_unrealized(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 150.0, 15000.0, trade_date=D1)
        pos = pf.get_position("AAPL")
        pos.update_market_price(160.0)
        assert pos.market_value == pytest.approx(16000.0)
        assert pos.unrealized_pnl == pytest.approx(1000.0)

    def test_t1_03_sell_realized_pnl(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 150.0, 15000.0, trade_date=D1)
        pf.update_position("AAPL", -100, 160.0, 0, realized_pnl=1000.0)
        pos = pf.get_position("AAPL")
        assert pos.quantity == pytest.approx(0, abs=1e-6)
        assert pos.avg_cost == pytest.approx(0, abs=1e-6)
        assert pos.realized_pnl == pytest.approx(1000.0)

    def test_t1_04_nav_identity(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 150.0, 15000.0, trade_date=D1)
        pf.get_position("AAPL").update_market_price(160.0)
        assert pf.nav == pytest.approx(pf.cash + sum(p.market_value for p in pf.positions.values()))


# ---------------------------------------------------------------------------
# CASE-2: Portfolio multi-lot + partial sell
# ---------------------------------------------------------------------------

class TestCase2PortfolioMultiLot:
    def test_t2_01_two_buys(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 100.0, 10000.0, trade_date=D1)
        pf.update_position("AAPL", 100, 120.0, 12000.0, trade_date=D2)
        pos = pf.get_position("AAPL")
        assert pos.quantity == pytest.approx(200)
        assert pos.avg_cost == pytest.approx(110.0)

    def test_t2_02_partial_sell(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 100.0, 10000.0, trade_date=D1)
        pf.update_position("AAPL", 100, 120.0, 12000.0, trade_date=D2)
        pf.update_position("AAPL", -150, 130.0, 0, realized_pnl=4500.0)
        pos = pf.get_position("AAPL")
        assert pos.quantity == pytest.approx(50)
        assert pos.realized_pnl == pytest.approx(4500.0)

    def test_t2_03_remaining_lots(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 100.0, 10000.0, trade_date=D1)
        pf.update_position("AAPL", 100, 120.0, 12000.0, trade_date=D2)
        pf.update_position("AAPL", -150, 130.0, 0, realized_pnl=4500.0)
        pos = pf.get_position("AAPL")
        total_lot_qty = sum(lot.qty for lot in pos._lots.values())
        assert total_lot_qty == pytest.approx(50)

    def test_t2_04_recalc_avg_cost(self):
        pf = Portfolio(initial_cash=100_000)
        pf.update_position("AAPL", 100, 100.0, 10000.0, trade_date=D1)
        pf.update_position("AAPL", 100, 120.0, 12000.0, trade_date=D2)
        pf.update_position("AAPL", -150, 130.0, 0, realized_pnl=4500.0)
        pos = pf.get_position("AAPL")
        assert pos.avg_cost == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# CASE-3: SubPortfolio capital isolation
# ---------------------------------------------------------------------------

class TestCase3SubPortfolioCashSync:
    def test_t3_01_master_not_affected_by_sub_spending(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.cash -= 10_000
        subB.cash -= 20_000
        assert master.cash == pytest.approx(0)

    def test_t3_02_sub_cash_values(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.cash -= 10_000
        subB.cash -= 20_000
        assert subA.cash == pytest.approx(30_000)
        assert subB.cash == pytest.approx(40_000)

    def test_t3_03_total_nav_reflects_spending_without_assets(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.cash -= 10_000
        subB.cash -= 20_000
        total = master.cash + subA.nav + subB.nav
        assert total == pytest.approx(70_000)

    def test_t3_05_total_nav_with_positions(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=D1)
        subA.cash -= 15000
        subB.update_position("MSFT", quantity=50, price=200.0, cost=10000.0, trade_date=D1)
        subB.cash -= 10000
        total = master.cash + subA.nav + subB.nav
        assert total == pytest.approx(100_000)

    def test_t3_04_negative_clamped(self):
        master = Portfolio(initial_cash=100_000)
        sub = SubPortfolio("test", 10_000, master)
        sub.cash = -5_000
        assert sub.cash == pytest.approx(0)


# ---------------------------------------------------------------------------
# CASE-4: SubPortfolio position isolation
# ---------------------------------------------------------------------------

class TestCase4SubPortfolioIsolation:
    def test_t4_01_sub_a_qty(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.update_position("AAPL", 50, 150.0, 7500.0, trade_date=D1)
        subB.update_position("AAPL", 30, 150.0, 4500.0, trade_date=D1)
        subA.update_position("AAPL", -20, 160.0, 0, realized_pnl=200.0)
        assert subA.get_position("AAPL").quantity == pytest.approx(30)

    def test_t4_02_sub_b_qty(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.update_position("AAPL", 50, 150.0, 7500.0, trade_date=D1)
        subB.update_position("AAPL", 30, 150.0, 4500.0, trade_date=D1)
        assert subB.get_position("AAPL").quantity == pytest.approx(30)

    def test_t4_03_total_qty(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.update_position("AAPL", 50, 150.0, 7500.0, trade_date=D1)
        subB.update_position("AAPL", 30, 150.0, 4500.0, trade_date=D1)
        total = subA.get_position("AAPL").quantity + subB.get_position("AAPL").quantity
        assert total == pytest.approx(80)

    def test_t4_04_cross_isolation(self):
        master = Portfolio(initial_cash=100_000)
        subA = SubPortfolio("A", 40_000, master)
        subB = SubPortfolio("B", 60_000, master)
        subA.update_position("AAPL", 50, 150.0, 7500.0, trade_date=D1)
        subB.update_position("MSFT", 30, 200.0, 6000.0, trade_date=D1)
        assert subA.get_position("MSFT") is None
        assert subB.get_position("AAPL") is None


# ---------------------------------------------------------------------------
# CASE-5: RiskEngine position limit
# ---------------------------------------------------------------------------

class TestCase5RiskPositionLimit:
    def test_t5_01_within_limit_passes(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 0.20, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        approved, _ = re.check_order("AAPL", 100, 100.0, 10_000, side="BUY")
        assert approved is True

    def test_t5_02_exceeds_limit_rejected(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 0.10, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        re.record_order("AAPL", 10_000)
        approved, _ = re.check_order("AAPL", 100, 100.0, 2_000, side="BUY")
        assert approved is False

    def test_t5_04_reset_clears_pending(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 0.20, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        re.record_order("AAPL", 10_000)
        assert re._pending_order_values.get("AAPL", 0) > 0
        re.reset_daily()
        assert re._pending_order_values.get("AAPL", 0) == 0

    def test_t5_05_default_backtest_order_rate_does_not_reject_large_rebalance(self):
        pf = Portfolio(initial_cash=1_000_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999}}
        re = RiskEngine(config, pf, None)

        approvals = []
        for idx in range(35):
            symbol = f"T{idx:03d}"
            approved, _ = re.check_order(symbol, 1, 1.0, 1.0, side="BUY", as_of_date=D1)
            approvals.append(approved)
            if approved:
                re.record_order(symbol, 1.0, as_of_date=D1)

        assert all(approvals)

    def test_t5_06_explicit_backtest_order_rate_limit_is_preserved(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 2}}
        re = RiskEngine(config, pf, None)

        for idx in range(2):
            approved, _ = re.check_order(f"T{idx}", 1, 1.0, 1.0, side="BUY", as_of_date=D1)
            assert approved is True
            re.record_order(f"T{idx}", 1.0, as_of_date=D1)
        approved, _ = re.check_order("T2", 1, 1.0, 1.0, side="BUY", as_of_date=D1)

        assert approved is False


# ---------------------------------------------------------------------------
# CASE-5B: RiskEngine cash isolation
# ---------------------------------------------------------------------------

class TestCase5BRiskCashIsolation:
    def test_t5b_01_buy_cannot_exceed_available_cash(self):
        pf = Portfolio(initial_cash=100_000)
        pf.cash = 25_000
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)

        approved, results = re.check_order("AAPL", 300, 100.0, 30_000, side="BUY")

        cash_checks = [r for r in results if r.check_name == "available_cash"]
        assert approved is False
        assert any(not r.passed for r in cash_checks)

    def test_t5b_02_buy_can_use_returned_or_earned_cash(self):
        pf = Portfolio(initial_cash=100_000)
        pf.cash = 25_000
        pf.cash += 5_000
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)

        approved, results = re.check_order("AAPL", 300, 100.0, 30_000, side="BUY")

        cash_checks = [r for r in results if r.check_name == "available_cash"]
        assert approved is True
        assert all(r.passed for r in cash_checks)


# ---------------------------------------------------------------------------
# CASE-6: RiskEngine daily loss
# ---------------------------------------------------------------------------

class TestCase6RiskDailyLoss:
    def test_t6_01_within_loss_passes(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 0.05, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        pf.update_position("AAPL", 100, 100.0, 10000.0, trade_date=D1)
        pf.get_position("AAPL").update_market_price(97.0)
        approved, results = re.check_order("MSFT", 10, 100.0, 1000, side="BUY")
        loss_checks = [r for r in results if r.check_name == "max_daily_loss"]
        assert all(r.passed for r in loss_checks)

    def test_t6_02_exceeds_loss_rejected(self):
        pf = Portfolio(initial_cash=100_000)
        pf.reset_daily()
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 0.02, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        pf.update_position("AAPL", 500, 100.0, 50000.0, trade_date=D1)
        pos = pf.get_position("AAPL")
        pos.update_market_price(90.0)
        pf._starting_nav = 150_000
        approved, results = re.check_order("MSFT", 10, 100.0, 1000, side="BUY")
        assert approved is False


# ---------------------------------------------------------------------------
# CASE-7: RiskEngine CN T+1
# ---------------------------------------------------------------------------

class TestCase7RiskCNT1:
    def test_t7_01_same_day_rejected(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        pf.update_position("600519", 1000, 50.0, 50000.0, trade_date=D2)
        approved, results = re.check_order("600519", 1000, 50.0, 50000.0, side="SELL", as_of_date=D1)
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert any(not r.passed for r in t1_checks)

    def test_t7_02_next_day_passes(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        pf.update_position("600519", 1000, 50.0, 50000.0, trade_date=D1)
        approved, results = re.check_order("600519", 1000, 50.0, 50000.0, side="SELL", as_of_date=D2)
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert all(r.passed for r in t1_checks)

    def test_t7_03_us_no_t1(self):
        pf = Portfolio(initial_cash=100_000)
        config = {"risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999}}
        re = RiskEngine(config, pf, None)
        pf.update_position("AAPL", 100, 150.0, 15000.0, trade_date=D1)
        approved, results = re.check_order("AAPL", 100, 150.0, 15000.0, side="SELL", as_of_date=D1)
        t1_checks = [r for r in results if r.check_name == "cn_t1_settlement"]
        assert all(r.passed for r in t1_checks)
