"""Enhanced backtest engine with event replay, slippage, and commission models."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
import pandas as pd
import numpy as np

from quant.core.events import EventBus, Event, EventType
from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.core.analytics import calculate_performance_metrics, PerformanceMetrics, Trade


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
    US: Dict[str, float] = field(default_factory=lambda: {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0})
    HK: Dict[str, float] = field(default_factory=lambda: {"type": "percent", "percent": 0.001, "min_per_order": 2.0})


class Backtester:
    """Enhanced backtester with event replay and slippage/commission models."""

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None
    ):
        self.config = config
        self.event_bus = event_bus or EventBus()
        self.slippage_bps = config.get("backtest", {}).get("slippage_bps", 5)
        self.commission_model = config.get("backtest", {}).get("commission_model", "us_equity")
        
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
        symbols: List[str] = None,
    ) -> BacktestResult:
        """Run backtest with full event replay."""
        portfolio = Portfolio(initial_cash=initial_cash, currency="USD")
        risk_engine = RiskEngine(self.config, portfolio, self.event_bus)
        
        symbols = symbols or []
        
        equity_curve_dates = []
        equity_curve_values = []
        all_trades: List[Trade] = []
        positions: Dict[str, Dict[str, Any]] = {}
        
        current_date = start
        speed_multiplier = self._get_speed_multiplier(
            self.config.get("backtest", {}).get("speed", "1x")
        )
        
        for strategy in strategies:
            strategy.context = self._create_context(portfolio, risk_engine, data_provider)
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)
        
        while current_date <= end:
            for strategy in strategies:
                if hasattr(strategy, "on_before_trading"):
                    strategy.on_before_trading(strategy.context, current_date.date())
            
            if data_provider:
                for symbol in symbols:
                    try:
                        bars = data_provider.get_bars(
                            symbol,
                            current_date,
                            current_date + timedelta(days=1),
                            self.config.get("data", {}).get("default_timeframe", "5m")
                        )
                        for _, bar in bars.iterrows():
                            bar_data = bar.to_dict()
                            bar_data['timestamp'] = bar.name if hasattr(bar, 'name') else current_date
                            bar_data['symbol'] = symbol
                            
                            for strategy in strategies:
                                if hasattr(strategy, "on_data"):
                                    strategy.on_data(strategy.context, bar_data)
                        
                        for strategy in strategies:
                            if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                                om = strategy.context.order_manager
                                if om and hasattr(om, "_pending_orders"):
                                    for order in list(om._pending_orders):
                                        fill = self._execute_order(order, portfolio, symbol, bar_data if not bars.empty else {}, positions)
                                        if fill:
                                            all_trades.append(fill)
                                            for s in strategies:
                                                if hasattr(s, "on_fill"):
                                                    s.on_fill(s.context, fill)
                                    om.clear_pending()
                                    
                    except Exception:
                        continue
            
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

    def _get_speed_multiplier(self, speed: str) -> int:
        """Get speed multiplier from config."""
        return {"1x": 1, "10x": 10, "100x": 100, "end_of_day": float("inf")}.get(speed, 1)

    def _create_context(self, portfolio: Portfolio, risk_engine: RiskEngine, data_provider: Any) -> Any:
        """Create strategy context for backtest."""
        from quant.core.engine import Context
        
        class BacktestOrderManager:
            def __init__(self, backtester):
                self.backtester = backtester
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
                return f"backtest_order_{len(self._pending_orders)}"
            
            def clear_pending(self):
                self._pending_orders.clear()
        
        class BacktestBroker:
            def __init__(self):
                pass
                
            def is_connected(self):
                return True
        
        return Context(
            portfolio=portfolio,
            risk_engine=risk_engine,
            event_bus=self.event_bus,
            order_manager=BacktestOrderManager(self),
            data_provider=data_provider,
            broker=BacktestBroker()
        )

    def _execute_order(
        self,
        order: Dict,
        portfolio: Portfolio,
        symbol: str,
        bar: Dict,
        positions: Dict[str, Dict[str, Any]]
    ) -> Optional[Trade]:
        """Execute order with slippage and commission."""
        execution_price = bar.get('close', bar.get('price', 100))
        slippage = execution_price * (self.slippage_bps / 10000)
        
        if order['side'] == 'BUY':
            exec_price = execution_price + slippage
        else:
            exec_price = execution_price - slippage
        
        quantity = order['quantity']
        market = self._detect_market(symbol)
        commission = self._calculate_commission(exec_price, quantity, market)
        
        total_cost = exec_price * quantity + commission
        
        if order['side'] == 'BUY':
            if portfolio.cash < total_cost:
                return None
            portfolio.cash -= total_cost
        else:
            portfolio.cash += (exec_price * quantity) - commission
        
        if symbol not in positions:
            positions[symbol] = {"quantity": 0, "entry_price": 0, "entry_time": bar.get('timestamp', datetime.now())}
        
        old_qty = positions[symbol]["quantity"]
        if order['side'] == 'BUY':
            new_qty = old_qty + quantity
        else:
            new_qty = old_qty - quantity
        
        positions[symbol]["quantity"] = new_qty
        
        if new_qty == 0:
            entry_price = positions[symbol]["entry_price"]
            if order['side'] == 'SELL':
                pnl = (exec_price - entry_price) * quantity
            else:
                pnl = (entry_price - exec_price) * quantity

            trade = Trade(
                entry_time=positions[symbol]["entry_time"],
                exit_time=bar.get('timestamp', datetime.now()),
                symbol=symbol,
                side=order['side'],
                entry_price=entry_price,
                exit_price=exec_price,
                quantity=quantity,
                pnl=pnl - commission
            )
            positions[symbol]["entry_price"] = 0
            return trade
        
        if order['side'] == 'BUY':
            avg_entry = (positions[symbol]["entry_price"] * old_qty + exec_price * quantity) / new_qty
            positions[symbol]["entry_price"] = avg_entry
        
        return None

    def _detect_market(self, symbol: str) -> str:
        """Detect market from symbol."""
        if symbol.isdigit() and len(symbol) >= 5:
            return "HK"
        return "US"

    def _calculate_commission(self, price: float, quantity: float, market: str) -> float:
        """Calculate commission for a trade."""
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
        """Export equity curve and trades to CSV."""
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
