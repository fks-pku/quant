"""Multi-strategy portfolio coordinator with priority-based risk budget allocation."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import logging

from quant.models.position import Position
from quant.models.order import Order


logger = logging.getLogger(__name__)


@dataclass
class StrategyAllocation:
    name: str
    priority: int
    max_risk_pct: float
    current_positions: Dict[str, Position] = field(default_factory=dict)
    current_risk_used: float = 0.0


@dataclass
class RiskCheckResult:
    passed: bool
    is_hard_limit: bool
    check_name: str
    message: str
    current_value: float
    limit_value: float


class PortfolioCoordinator:
    """Coordinates multiple strategies with priority-based allocation."""

    def __init__(self, total_risk_budget: float = 1.0, max_portfolio_leverage: float = 1.5):
        self.total_risk_budget = total_risk_budget
        self.max_portfolio_leverage = max_portfolio_leverage
        self._strategies: Dict[str, StrategyAllocation] = {}
        self._priority_order: List[str] = []
        self._combined_positions: Dict[str, Position] = {}
        self._logger = logging.getLogger("PortfolioCoordinator")

    def register_strategy(self, name: str, priority: int, max_risk_pct: float) -> None:
        self._strategies[name] = StrategyAllocation(
            name=name,
            priority=priority,
            max_risk_pct=max_risk_pct,
        )
        self._priority_order = sorted(
            self._strategies.keys(),
            key=lambda n: self._strategies[n].priority
        )
        self._logger.info(f"Registered strategy: {name} (priority={priority}, max_risk={max_risk_pct:.0%})")

    def update_strategy_position(self, strategy_name: str, symbol: str, position: Position) -> None:
        if strategy_name not in self._strategies:
            return
        self._strategies[strategy_name].current_positions[symbol] = position
        self._rebuild_combined_positions()

    def remove_strategy_position(self, strategy_name: str, symbol: str) -> None:
        if strategy_name not in self._strategies:
            return
        self._strategies[strategy_name].current_positions.pop(symbol, None)
        self._rebuild_combined_positions()

    def get_combined_positions(self) -> Dict[str, Position]:
        return self._combined_positions.copy()

    def get_strategy_positions(self, strategy_name: str) -> Dict[str, Position]:
        if strategy_name not in self._strategies:
            return {}
        return self._strategies[strategy_name].current_positions.copy()

    def check_combined_risk(self, order: Order, strategy_name: str, nav: float) -> RiskCheckResult:
        if strategy_name not in self._strategies:
            return RiskCheckResult(
                passed=False,
                is_hard_limit=True,
                check_name="strategy_not_registered",
                message=f"Strategy {strategy_name} not registered",
                current_value=0.0,
                limit_value=0.0,
            )

        allocation = self._strategies[strategy_name]

        risk_budget = self.total_risk_budget * allocation.max_risk_pct
        order_value = abs(order.quantity * (order.price or 0))
        current_risk = self._calculate_strategy_risk(allocation, nav)
        new_risk = current_risk + order_value

        if new_risk > risk_budget * nav:
            return RiskCheckResult(
                passed=False,
                is_hard_limit=True,
                check_name="strategy_risk_budget",
                message=f"Strategy {strategy_name}: risk ${new_risk:.2f} exceeds budget ${risk_budget * nav:.2f}",
                current_value=new_risk,
                limit_value=risk_budget * nav,
            )

        total_exposure = sum(
            abs(p.market_value) for p in self._combined_positions.values()
        )
        new_exposure = total_exposure + order_value
        leverage = new_exposure / nav if nav > 0 else 0

        if leverage > self.max_portfolio_leverage:
            return RiskCheckResult(
                passed=False,
                is_hard_limit=True,
                check_name="portfolio_leverage",
                message=f"Portfolio leverage {leverage:.2f}x exceeds limit {self.max_portfolio_leverage:.2f}x",
                current_value=leverage,
                limit_value=self.max_portfolio_leverage,
            )

        symbol_exposure = self._combined_positions.get(order.symbol)
        existing_value = abs(symbol_exposure.market_value) if symbol_exposure else 0
        combined_value = existing_value + order_value
        if nav > 0 and combined_value / nav > 0.10:
            return RiskCheckResult(
                passed=False,
                is_hard_limit=True,
                check_name="combined_position_concentration",
                message=f"Combined position {order.symbol}: ${combined_value:.2f} exceeds 10% of NAV",
                current_value=combined_value,
                limit_value=nav * 0.10,
            )

        return RiskCheckResult(
            passed=True,
            is_hard_limit=False,
            check_name="combined_risk",
            message="All checks passed",
            current_value=new_risk,
            limit_value=risk_budget * nav,
        )

    def get_strategy_allocation_summary(self, nav: float) -> List[Dict[str, Any]]:
        summary = []
        for name in self._priority_order:
            alloc = self._strategies[name]
            risk = self._calculate_strategy_risk(alloc, nav)
            summary.append({
                "name": name,
                "priority": alloc.priority,
                "max_risk_pct": alloc.max_risk_pct,
                "current_risk": risk,
                "risk_budget": self.total_risk_budget * alloc.max_risk_pct * nav,
                "num_positions": len(alloc.current_positions),
            })
        return summary

    def _calculate_strategy_risk(self, allocation: StrategyAllocation, nav: float) -> float:
        return sum(abs(p.market_value) for p in allocation.current_positions.values())

    def _rebuild_combined_positions(self) -> None:
        self._combined_positions.clear()
        for alloc in self._strategies.values():
            for symbol, pos in alloc.current_positions.items():
                if symbol in self._combined_positions:
                    existing = self._combined_positions[symbol]
                    total_qty = existing.quantity + pos.quantity
                    if total_qty != 0:
                        new_avg_cost = (
                            (existing.avg_cost * existing.quantity + pos.avg_cost * pos.quantity)
                            / total_qty
                        )
                    else:
                        new_avg_cost = 0
                    self._combined_positions[symbol] = Position(
                        symbol=symbol,
                        quantity=total_qty,
                        avg_cost=new_avg_cost,
                        market_value=existing.market_value + pos.market_value,
                        unrealized_pnl=existing.unrealized_pnl + pos.unrealized_pnl,
                        realized_pnl=existing.realized_pnl + pos.realized_pnl,
                        sector=pos.sector or existing.sector,
                    )
                else:
                    self._combined_positions[symbol] = Position(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        avg_cost=pos.avg_cost,
                        market_value=pos.market_value,
                        unrealized_pnl=pos.unrealized_pnl,
                        realized_pnl=pos.realized_pnl,
                        sector=pos.sector,
                    )
