"""Unit tests for RiskEngine."""

import pytest
from datetime import datetime

from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine, RiskCheckResult
from quant.infrastructure.events import EventBus


def _make_risk_engine(portfolio=None, config=None):
    portfolio = portfolio or Portfolio(initial_cash=100000)
    config = config or {
        "risk": {
            "max_position_pct": 0.05,
            "max_sector_pct": 0.25,
            "max_daily_loss_pct": 0.02,
            "max_leverage": 1.5,
            "max_orders_minute": 30,
        }
    }
    bus = EventBus()
    return RiskEngine(config, portfolio, bus)


class TestRiskEnginePositionSize:
    def test_passes_when_within_limit(self):
        engine = _make_risk_engine()
        nav = engine.portfolio.nav
        approved, results = engine.check_order("AAPL", 10, 100.0, 1000.0)
        assert approved is True

    def test_fails_when_exceeds_limit(self):
        engine = _make_risk_engine()
        nav = engine.portfolio.nav
        huge_value = nav * 0.10
        approved, results = engine.check_order("AAPL", 100, 100.0, huge_value)
        assert approved is False
        pos_check = [r for r in results if r.check_name == "max_position_size"]
        assert len(pos_check) == 1
        assert pos_check[0].passed is False


class TestRiskEngineSectorExposure:
    def test_sector_check_passes(self):
        engine = _make_risk_engine()
        approved, results = engine.check_order("AAPL", 10, 100.0, 1000.0, sector="Technology")
        sector_checks = [r for r in results if r.check_name == "max_sector_exposure"]
        assert len(sector_checks) == 1
        assert sector_checks[0].passed is True

    def test_sector_check_with_existing_exposure(self):
        engine = _make_risk_engine()
        engine.portfolio.update_position("MSFT", quantity=100, price=200, cost=20000, sector="Technology")
        large_value = engine.portfolio.nav * 0.20
        approved, results = engine.check_order("AAPL", 10, 100.0, large_value, sector="Technology")
        assert approved is False


class TestRiskEngineDailyLoss:
    def test_passes_when_no_loss(self):
        engine = _make_risk_engine()
        approved, results = engine.check_order("AAPL", 1, 100.0, 100.0)
        daily_checks = [r for r in results if r.check_name == "max_daily_loss"]
        assert daily_checks[0].passed is True

    def test_fails_after_large_loss(self):
        portfolio = Portfolio(initial_cash=100000)
        portfolio._starting_nav = 100000
        portfolio.cash = 95000
        engine = _make_risk_engine(portfolio=portfolio)
        approved, results = engine.check_order("AAPL", 1, 100.0, 100.0)
        daily_checks = [r for r in results if r.check_name == "max_daily_loss"]
        assert daily_checks[0].passed is False


class TestRiskEngineLeverage:
    def test_passes_when_low_leverage(self):
        engine = _make_risk_engine()
        approved, results = engine.check_order("AAPL", 1, 100.0, 100.0)
        lev_checks = [r for r in results if r.check_name == "max_leverage"]
        assert lev_checks[0].passed is True


class TestRiskEngineOrderRate:
    def test_passes_under_rate_limit(self):
        engine = _make_risk_engine()
        approved, results = engine.check_order("AAPL", 1, 100.0, 100.0)
        rate_checks = [r for r in results if r.check_name == "max_order_rate"]
        assert rate_checks[0].passed is True


class TestRiskEngineRecordOrder:
    def test_record_order_increments_count(self):
        engine = _make_risk_engine()
        engine.record_order()
        assert len(engine._order_timestamps) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
