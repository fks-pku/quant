"""Backtest engine using Portfolio as single source of truth for positions."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import pandas as pd
import numpy as np

from quant.core.events import EventBus, EventType
from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.core.analytics import calculate_performance_metrics, PerformanceMetrics
from quant.models.trade import Trade


logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    final_nav: float
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    avg_trade_duration: timedelta
    equity_curve: pd.Series
    trades: List[Trade]
    metrics: PerformanceMetrics


@dataclass
class CommissionConfig:
    US: Dict[str, Any] = field(default_factory=lambda: {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0})
    HK: Dict[str, Any] = field(default_factory=lambda: {"type": "percent", "percent": 0.001, "min_per_order": 2.0})


class Backtester:
    """Backtester that uses Portfolio as the single source of truth for positions."""

    def __init__(self, config: Dict[str, Any], event_bus: Optional[EventBus] = None):
        self.config = config
        self.event_bus = event_bus or EventBus()
        self.slippage_bps = config.get("backtest", {}).get("slippage_bps", 5)

        commission_config = config.get("execution", {}).get("commission", {})
        self.commission = CommissionConfig(
            US=commission_config.get("US", {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}),
            HK=commission_config.get("HK", {"type": "percent", "percent": 0.001, "min_per_order": 2.0})
        )

    def run(
        self,
        start: datetime,
        end: datetime,
        strategies: List[Any],
        initial_cash: float = 100000,
        data_provider: Any = None,
        symbols: Optional[List[str]] = None,
    ) -> BacktestResult:
        portfolio = Portfolio(initial_cash=initial_cash, currency="USD")
        risk_engine = RiskEngine(self.config, portfolio, self.event_bus)
        symbols = symbols or []

        equity_curve_dates: List[datetime] = []
        equity_curve_values: List[float] = []
        all_trades: List[Trade] = []

        entry_prices: Dict[str, float] = {}
        entry_times: Dict[str, datetime] = {}
        last_prices: Dict[str, float] = {}

        for strategy in strategies:
            strategy.context = self._create_context(portfolio, risk_engine, data_provider)
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        current_date = start
        while current_date <= end:
            for strategy in strategies:
                if hasattr(strategy, "on_before_trading"):
                    strategy.on_before_trading(strategy.context, current_date.date())

            if data_provider:
                for symbol in symbols:
                    bar_data = {}
                    try:
                        bars = data_provider.get_bars(
                            symbol,
                            current_date,
                            current_date + timedelta(days=1),
                            self.config.get("data", {}).get("default_timeframe", "1d")
                        )
                        if bars is not None and not bars.empty:
                            for _, bar in bars.iterrows():
                                bar_data = bar.to_dict()
                                bar_data['timestamp'] = bar.name if hasattr(bar, 'name') else current_date
                                bar_data['symbol'] = symbol
                                last_prices[symbol] = bar_data.get('close', 0)

                                for strategy in strategies:
                                    if hasattr(strategy, "on_data"):
                                        strategy.on_data(strategy.context, bar_data)
                    except Exception:
                        pass

                    self._update_portfolio_prices(portfolio, last_prices)

                    for strategy in strategies:
                        if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                            om = strategy.context.order_manager
                            if om and hasattr(om, "_pending_orders"):
                                for order in list(om._pending_orders):
                                    trade = self._execute_order(
                                        order, portfolio, symbol, bar_data, entry_prices, entry_times
                                    )
                                    if trade:
                                        all_trades.append(trade)
                                        for s in strategies:
                                            if hasattr(s, "on_fill"):
                                                s.on_fill(s.context, trade)
                                om.clear_pending()

            nav = portfolio.nav
            equity_curve_dates.append(current_date)
            equity_curve_values.append(nav)

            for strategy in strategies:
                if hasattr(strategy, "on_after_trading"):
                    strategy.on_after_trading(strategy.context, current_date.date())

            current_date += timedelta(days=1)

        for strategy in strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

        equity_curve = pd.Series(equity_curve_values, index=equity_curve_dates)
        metrics = calculate_performance_metrics(equity_curve, all_trades)

        return BacktestResult(
            final_nav=metrics.equity_curve.iloc[-1] if not metrics.equity_curve.empty else initial_cash,
            total_return=metrics.total_return,
            sharpe_ratio=metrics.sharpe_ratio,
            sortino_ratio=metrics.sortino_ratio,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_pct=metrics.max_drawdown_pct,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            avg_trade_duration=metrics.avg_trade_duration,
            equity_curve=equity_curve,
            trades=all_trades,
            metrics=metrics
        )

    def _update_portfolio_prices(self, portfolio: Portfolio, last_prices: Dict[str, float]) -> None:
        """Update portfolio position market_values with latest prices."""
        for symbol, pos in portfolio.positions.items():
            if pos.quantity != 0 and symbol in last_prices:
                pos.market_value = pos.quantity * last_prices[symbol]
                pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

    def _create_context(self, portfolio: Portfolio, risk_engine: RiskEngine, data_provider: Any) -> Any:
        from quant.core.engine import Context

        class BacktestOrderManager:
            def __init__(self):
                self._pending_orders: List[Dict] = []

            def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
                order = {
                    "symbol": symbol,
                    "quantity": quantity,
                    "side": side,
                    "order_type": order_type,
                    "price": price,
                    "strategy": strategy_name
                }
                self._pending_orders.append(order)
                return f"bt_{len(self._pending_orders)}"

            def clear_pending(self):
                self._pending_orders.clear()

        class BacktestBroker:
            def is_connected(self):
                return True

        return Context(
            portfolio=portfolio,
            risk_engine=risk_engine,
            event_bus=self.event_bus,
            order_manager=BacktestOrderManager(),
            data_provider=data_provider,
            broker=BacktestBroker()
        )

    def _execute_order(
        self,
        order: Dict,
        portfolio: Portfolio,
        symbol: str,
        bar: Dict,
        entry_prices: Dict[str, float],
        entry_times: Dict[str, datetime],
    ) -> Optional[Trade]:
        """Execute order through Portfolio, returning a Trade on position close."""
        exec_price = bar.get('close', bar.get('price', 100))
        if not exec_price or exec_price <= 0:
            return None

        slippage = exec_price * (self.slippage_bps / 10000)
        if order['side'] == 'BUY':
            exec_price += slippage
        else:
            exec_price -= slippage

        quantity = order['quantity']
        market = self._detect_market(symbol)
        commission = self._calculate_commission(exec_price, quantity, market)
        total_cost = exec_price * quantity + commission

        if order['side'] == 'BUY':
            if portfolio.cash < total_cost:
                return None

            entry_prices[symbol] = entry_prices.get(symbol, exec_price)
            if symbol not in entry_times:
                entry_times[symbol] = bar.get('timestamp', datetime.now())

            old_qty = portfolio.get_position(symbol).quantity if portfolio.get_position(symbol) else 0
            cost = exec_price * quantity + commission
            portfolio.update_position(symbol, quantity=quantity, price=exec_price, cost=cost)
            portfolio.cash -= total_cost

            new_qty = old_qty + quantity
            if old_qty > 0 and new_qty > 0:
                entry_prices[symbol] = (entry_prices[symbol] * old_qty + exec_price * quantity) / new_qty
            elif old_qty == 0:
                entry_prices[symbol] = exec_price
                entry_times[symbol] = bar.get('timestamp', datetime.now())

            return None

        elif order['side'] == 'SELL':
            pos = portfolio.get_position(symbol)
            if not pos or pos.quantity <= 0:
                return None

            sell_qty = min(quantity, pos.quantity)
            realized = portfolio.close_position(symbol, exec_price)
            portfolio.cash += (exec_price * sell_qty) - commission

            trade = Trade(
                entry_time=entry_times.get(symbol, datetime.now()),
                exit_time=bar.get('timestamp', datetime.now()),
                symbol=symbol,
                side=order['side'],
                entry_price=entry_prices.get(symbol, exec_price),
                exit_price=exec_price,
                quantity=sell_qty,
                pnl=realized - commission
            )

            remaining = pos.quantity - sell_qty if pos else 0
            if remaining <= 0:
                entry_prices.pop(symbol, None)
                entry_times.pop(symbol, None)

            return trade

        return None

    def _detect_market(self, symbol: str) -> str:
        if symbol.isdigit() and len(symbol) >= 5:
            return "HK"
        return "US"

    def _calculate_commission(self, price: float, quantity: float, market: str) -> float:
        if market == "US":
            cfg = self.commission.US
            if cfg["type"] == "per_share":
                commission = quantity * cfg.get("per_share", 0.005)
                return max(commission, cfg.get("min_per_order", 1.0))
        elif market == "HK":
            cfg = self.commission.HK
            if cfg["type"] == "percent":
                commission = price * quantity * cfg.get("percent", 0.001)
                return max(commission, cfg.get("min_per_order", 2.0))
        return 0.0


class BacktestResultExporter:
    """Export backtest results to CSV."""

    @staticmethod
    def to_csv(result: BacktestResult, output_path: str) -> None:
        result.metrics.equity_curve.to_csv(f"{output_path}_equity.csv")

        if result.trades:
            trades_df = pd.DataFrame([
                {
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": t.pnl
                }
                for t in result.trades
            ])
            trades_df.to_csv(f"{output_path}_trades.csv", index=False)
