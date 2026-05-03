"""Portfolio and RiskEngine creation — single and sub-portfolio modes."""

from typing import Dict, Any, List, Optional, Type

from quant.features.backtest.entities import _BacktestContext


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
) -> tuple:
    if portfolio_class is None or risk_engine_class is None or sub_portfolio_class is None:
        raise ValueError(
            "portfolio_class, risk_engine_class, and sub_portfolio_class are required. "
            "Backtester and WalkForwardEngine provide defaults from quant.features.trading."
        )

    master = portfolio_class(initial_cash=initial_cash, currency=currency)
    use_subs = strategy_allocations is not None and len(strategies) > 1

    if use_subs:
        portfolio_map: Dict[str, Any] = {}
        risk_map: Dict[str, Any] = {}
        import logging
        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            alloc_pct = strategy_allocations.get(sname, 0.0)
            if alloc_pct <= 0:
                logging.getLogger(__name__).warning(
                    "Strategy '%s' has allocation %.4f — orders may be rejected", sname, alloc_pct
                )
            if alloc_pct < 0 or alloc_pct > 1.0:
                raise ValueError(
                    f"Strategy '{sname}' allocation must be in [0, 1], got {alloc_pct}"
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
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            portfolio_map[sname] = master
            risk_map[sname] = shared_risk
        primary_portfolio = master

    return portfolio_map, risk_map, primary_portfolio, use_subs


def create_context(portfolio: Any, risk_engine: Any, event_bus: Any, data_provider: Any) -> Any:
    return _BacktestContext(
        portfolio=portfolio,
        risk_engine=risk_engine,
        event_bus=event_bus,
        data_provider=data_provider,
    )
