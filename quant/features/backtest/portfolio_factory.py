"""Portfolio and RiskEngine creation — single and sub-portfolio modes."""

import logging
from typing import Dict, Any, List, Optional, Tuple, Type

from quant.features.backtest.entities import _BacktestContext


def _strategy_name(strategy) -> str:
    return getattr(strategy, 'name', strategy.__class__.__name__)


def create_portfolio_contexts(
    strategies: List[Any],
    initial_cash: float,
    strategy_allocations: Optional[Dict[str, float]],
    config: Dict[str, Any],
    event_bus: Any,
    currency: str,
    portfolio_class: Optional[Type] = None,
    risk_engine_class: Optional[Type] = None,
    sub_portfolio_class: Optional[Type] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Any, bool]:
    if portfolio_class is None or risk_engine_class is None or sub_portfolio_class is None:
        raise ValueError(
            "portfolio_class, risk_engine_class, and sub_portfolio_class are required. "
            "Backtester and WalkForwardEngine provide defaults from quant.features.trading."
        )

    master = portfolio_class(initial_cash=initial_cash, currency=currency)
    use_subs = strategy_allocations is not None or len(strategies) > 1

    if use_subs:
        portfolio_map: Dict[str, Any] = {}
        risk_map: Dict[str, Any] = {}
        strategy_names = [
            _strategy_name(strategy)
            for strategy in strategies
        ]
        if len(strategy_names) != len(set(strategy_names)):
            duplicates = sorted({
                name for name in strategy_names
                if strategy_names.count(name) > 1
            })
            raise ValueError(f"Duplicate strategy names are not allowed: {duplicates}")
        if strategy_allocations is None:
            equal_weight = 1.0 / len(strategies)
            strategy_allocations = {
                sname: equal_weight for sname in strategy_names
            }
        unknown_allocations = set(strategy_allocations) - set(strategy_names)
        if unknown_allocations:
            raise ValueError(
                f"strategy allocations contain unknown strategies: {sorted(unknown_allocations)}"
            )
        missing_allocations = set(strategy_names) - set(strategy_allocations)
        if missing_allocations:
            raise ValueError(
                f"strategy allocations missing strategies: {sorted(missing_allocations)}"
            )
        for sname, alloc_pct in strategy_allocations.items():
            if alloc_pct < 0 or alloc_pct > 1.0:
                raise ValueError(
                    f"Strategy '{sname}' allocation must be in [0, 1], got {alloc_pct}"
                )
        allocation_sum = sum(strategy_allocations[sname] for sname in strategy_names)
        if allocation_sum > 1.0 + 1e-12:
            raise ValueError(f"strategy allocations sum to {allocation_sum:.4f}, must be <= 1.0")
        for strategy in strategies:
            sname = _strategy_name(strategy)
            alloc_pct = strategy_allocations.get(sname, 0.0)
            if alloc_pct <= 0:
                logging.getLogger(__name__).warning(
                    "Strategy '%s' has allocation %.4f — orders may be rejected", sname, alloc_pct
                )
            alloc_cash = initial_cash * alloc_pct
            sub = sub_portfolio_class(strategy_name=sname, allocated_capital=alloc_cash, master=master)
            portfolio_map[sname] = sub
            risk_map[sname] = risk_engine_class(config, sub, event_bus)
        primary_portfolio = master
    else:
        portfolio_map = {}
        risk_map = {}
        shared_risk = risk_engine_class(config, master, event_bus)
        for strategy in strategies:
            sname = _strategy_name(strategy)
            portfolio_map[sname] = master
            risk_map[sname] = shared_risk
        primary_portfolio = master

    return portfolio_map, risk_map, primary_portfolio, use_subs


def create_context(
    portfolio: Any,
    risk_engine: Any,
    event_bus: Any,
    data_provider: Any,
    *,
    base_slippage_bps: float = 5.0,
    execution_cost_model: Any = None,
    market_impact_factor: float = 0.0,
    strategy_name: Optional[str] = None,
) -> Any:
    return _BacktestContext(
        portfolio=portfolio,
        risk_engine=risk_engine,
        event_bus=event_bus,
        data_provider=data_provider,
        base_slippage_bps=base_slippage_bps,
        execution_cost_model=execution_cost_model,
        market_impact_factor=market_impact_factor,
        strategy_name=strategy_name,
    )
