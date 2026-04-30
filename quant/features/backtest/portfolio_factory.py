"""Portfolio and RiskEngine creation — single and sub-portfolio modes."""

from typing import Dict, Any, List, Optional

from quant.features.backtest.entities import _BacktestContext


def create_portfolio_contexts(
    strategies: List[Any],
    initial_cash: float,
    strategy_allocations: Optional[Dict[str, float]],
    config: Dict[str, Any],
    event_bus: Any,
    currency: str,
) -> tuple:
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio

    master = Portfolio(initial_cash=initial_cash, currency=currency)
    use_subs = strategy_allocations is not None and len(strategies) > 1

    if use_subs:
        portfolio_map: Dict[str, Any] = {}
        risk_map: Dict[str, Any] = {}
        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            alloc_pct = strategy_allocations.get(sname, 0.0)
            alloc_cash = initial_cash * alloc_pct
            sub = SubPortfolio(strategy_name=sname, allocated_capital=alloc_cash, master=master)
            portfolio_map[sname] = sub
            risk_map[sname] = RiskEngine(config, sub, event_bus)
        primary_portfolio = master
    else:
        portfolio_map = {}
        risk_map = {}
        shared_risk = RiskEngine(config, master, event_bus)
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
