"""Pure data structures for backtest results, diagnostics, and context."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd

from quant.domain.models.trade import Trade


@dataclass
class BacktestDiagnostics:
    suspended_days: int = 0
    volume_limited_trades: int = 0
    lot_adjusted_trades: int = 0
    fill_count: int = 0
    total_commission: float = 0.0
    total_gross_pnl: float = 0.0
    t1_rejected_sells: int = 0
    limit_rejected_orders: int = 0
    discarded_orders: int = 0
    risk_skipped_orders: int = 0

    @property
    def cost_drag_pct(self) -> float:
        if abs(self.total_gross_pnl) < 1e-10:
            return 0.0
        return self.total_commission / abs(self.total_gross_pnl) * 100


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
    metrics: Any
    diagnostics: BacktestDiagnostics = field(default_factory=BacktestDiagnostics)
    open_positions: List[Dict] = field(default_factory=list)


@dataclass
class CommissionConfig:
    US: Dict[str, Any] = field(default_factory=lambda: {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0})
    HK: Dict[str, Any] = field(default_factory=lambda: {"type": "hk_realistic"})
    CN: Dict[str, Any] = field(default_factory=lambda: {"type": "cn_realistic"})


class BacktestResultExporter:

    @staticmethod
    def to_csv(result: BacktestResult, output_path: str) -> None:
        result.metrics.equity_curve.to_csv(f"{output_path}_equity.csv")

        if result.trades:
            trades_df = pd.DataFrame([
                {
                    "signal_date": t.signal_date,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "fill_date": t.fill_date,
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "fill_price": t.fill_price,
                    "intended_qty": t.intended_qty,
                    "quantity": t.quantity,
                    "pnl": t.pnl,
                    "cost_breakdown": t.cost_breakdown,
                }
                for t in result.trades
            ])
            trades_df.to_csv(f"{output_path}_trades.csv", index=False)


class _BacktestOrderManager:
    def __init__(self, risk_engine):
        self._pending_orders: List[Dict] = []
        self._risk_engine = risk_engine
        self._current_date: Optional[date] = None
        self._last_prices: Dict[str, float] = {}

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
        effective_price = price if price and price > 0 else 0
        if effective_price == 0:
            effective_price = self._last_prices.get(symbol, 0)
        if self._risk_engine:
            order_value = effective_price * quantity if effective_price > 0 else quantity
            approved, _ = self._risk_engine.check_order(
                symbol, quantity, effective_price, order_value, side=side,
                as_of_date=self._current_date,
            )
            if not approved:
                return None
            if effective_price > 0:
                self._risk_engine.record_order(
                    symbol=symbol, order_value=order_value, as_of_date=self._current_date,
                )
        order = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": order_type,
            "price": price,
            "strategy": strategy_name,
            "_risk_check_price": effective_price,
        }
        self._pending_orders.append(order)
        return f"bt_{len(self._pending_orders)}"

    def clear_pending(self):
        self._pending_orders.clear()


class _BacktestContext:
    def __init__(self, portfolio, risk_engine, event_bus, data_provider):
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.event_bus = event_bus
        self.data_provider = data_provider
        self.order_manager = _BacktestOrderManager(risk_engine)
