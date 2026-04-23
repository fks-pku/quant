"""Unit tests for PortfolioCoordinator."""

import pytest

from quant.features.portfolio.coordinator import PortfolioCoordinator, StrategyAllocation
from quant.domain.models.position import Position
from quant.domain.models.order import Order


def _make_coordinator():
    return PortfolioCoordinator(total_risk_budget=1.0, max_portfolio_leverage=1.5)


class TestPortfolioCoordinatorRegistration:
    def test_register_strategy(self):
        coord = _make_coordinator()
        coord.register_strategy("MomentumEOD", priority=1, max_risk_pct=0.30)
        assert "MomentumEOD" in coord._strategies
        assert coord._strategies["MomentumEOD"].priority == 1

    def test_priority_ordering(self):
        coord = _make_coordinator()
        coord.register_strategy("StrategyB", priority=2, max_risk_pct=0.30)
        coord.register_strategy("StrategyA", priority=1, max_risk_pct=0.50)
        assert coord._priority_order[0] == "StrategyA"
        assert coord._priority_order[1] == "StrategyB"


class TestPortfolioCoordinatorPositions:
    def test_update_strategy_position(self):
        coord = _make_coordinator()
        coord.register_strategy("MomentumEOD", priority=1, max_risk_pct=0.30)
        pos = Position(symbol="AAPL", quantity=10, avg_cost=150, market_value=1500,
                       unrealized_pnl=0, realized_pnl=0)
        coord.update_strategy_position("MomentumEOD", "AAPL", pos)
        combined = coord.get_combined_positions()
        assert "AAPL" in combined
        assert combined["AAPL"].quantity == 10

    def test_aggregate_across_strategies(self):
        coord = _make_coordinator()
        coord.register_strategy("StrategyA", priority=1, max_risk_pct=0.50)
        coord.register_strategy("StrategyB", priority=2, max_risk_pct=0.50)

        pos_a = Position(symbol="AAPL", quantity=10, avg_cost=150, market_value=1500,
                         unrealized_pnl=0, realized_pnl=0)
        pos_b = Position(symbol="AAPL", quantity=5, avg_cost=155, market_value=775,
                         unrealized_pnl=0, realized_pnl=0)

        coord.update_strategy_position("StrategyA", "AAPL", pos_a)
        coord.update_strategy_position("StrategyB", "AAPL", pos_b)

        combined = coord.get_combined_positions()
        assert "AAPL" in combined
        assert combined["AAPL"].quantity == 15

    def test_remove_strategy_position(self):
        coord = _make_coordinator()
        coord.register_strategy("StrategyA", priority=1, max_risk_pct=0.50)
        pos = Position(symbol="AAPL", quantity=10, avg_cost=150, market_value=1500,
                       unrealized_pnl=0, realized_pnl=0)
        coord.update_strategy_position("StrategyA", "AAPL", pos)
        coord.remove_strategy_position("StrategyA", "AAPL")
        assert "AAPL" not in coord.get_combined_positions()


class TestPortfolioCoordinatorRiskCheck:
    def test_passes_within_budget(self):
        coord = _make_coordinator()
        coord.register_strategy("StrategyA", priority=1, max_risk_pct=0.50)
        order = Order(symbol="AAPL", quantity=10, side="BUY", order_type="MARKET", price=150.0)
        result = coord.check_combined_risk(order, "StrategyA", nav=100000)
        assert result.passed is True

    def test_fails_unregistered_strategy(self):
        coord = _make_coordinator()
        order = Order(symbol="AAPL", quantity=10, side="BUY", order_type="MARKET", price=150.0)
        result = coord.check_combined_risk(order, "NonExistent", nav=100000)
        assert result.passed is False
        assert result.check_name == "strategy_not_registered"

    def test_fails_concentration_limit(self):
        coord = _make_coordinator()
        coord.register_strategy("StrategyA", priority=1, max_risk_pct=1.0)
        order = Order(symbol="AAPL", quantity=10000, side="BUY", order_type="MARKET", price=150.0)
        result = coord.check_combined_risk(order, "StrategyA", nav=100000)
        assert result.passed is False


class TestPortfolioCoordinatorSummary:
    def test_allocation_summary(self):
        coord = _make_coordinator()
        coord.register_strategy("MomentumEOD", priority=1, max_risk_pct=0.30)
        coord.register_strategy("MeanReversion", priority=2, max_risk_pct=0.50)
        summary = coord.get_strategy_allocation_summary(nav=100000)
        assert len(summary) == 2
        assert summary[0]["name"] == "MomentumEOD"
        assert summary[0]["priority"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
