"""Tests that PortfolioCoordinator imports RiskCheckResult from risk module."""

from quant.features.trading.risk import RiskCheckResult as RiskModuleResult
from quant.features.portfolio.coordinator import PortfolioCoordinator


def test_import_portfolio_coordinator():
    coord = PortfolioCoordinator()
    assert coord is not None


def test_risk_checkresult_is_same_class():
    import quant.features.portfolio.coordinator as pc_module
    import inspect

    src = inspect.getsource(pc_module)
    assert "RiskCheckResult" in src


def test_check_combined_risk_returns_risk_checkresult():
    from quant.shared.models.order import Order

    coord = PortfolioCoordinator(total_risk_budget=1.0, max_portfolio_leverage=1.5)
    coord.register_strategy("strat_a", priority=1, max_risk_pct=0.5)

    order = Order(symbol="AAPL", quantity=10, side="BUY", order_type="MARKET", price=100.0)
    result = coord.check_combined_risk(order, "strat_a", nav=100000.0)

    assert isinstance(result, RiskModuleResult)
    assert result.passed is True
    assert result.check_name == "combined_risk"
