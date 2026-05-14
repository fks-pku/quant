"""Pure data structures for backtest results, diagnostics, and context."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd

from quant.domain.models.trade import Trade
from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason
from quant.features.backtest.schemas import DeferredOrder


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
    expired_orders: int = 0
    risk_skipped_orders: int = 0
    truncated_sells: int = 0
    submission_rejected: int = 0
    total_cash_dividends: float = 0.0
    total_dividend_tax: float = 0.0
    total_net_dividends: float = 0.0
    forced_closeout_orders: int = 0
    forced_closeout_trades: int = 0
    final_suspended_holding_nav: float = 0.0
    final_suspended_holding_count: int = 0
    final_suspended_symbols: List[str] = field(default_factory=list)
    rejection_counts: Dict[str, int] = field(default_factory=dict)

    def record_rejection(self, reason: OrderRejectionReason) -> None:
        key = reason.name.lower()
        self.rejection_counts[key] = self.rejection_counts.get(key, 0) + 1

    @property
    def cost_drag_pct(self) -> float:
        gpnl = self.total_gross_pnl or 0.0
        if abs(gpnl) < 1e-10 or gpnl != gpnl:
            return 0.0
        return self.total_commission / abs(gpnl) * 100


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
        _log = logging.getLogger(__name__)
        if result.metrics is None or result.metrics.equity_curve.empty:
            _log.warning("No equity curve to export for %s", output_path)
            return
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
        else:
            _log.info("No trades to export for %s", output_path)


class _BacktestOrderManager:
    def __init__(self, risk_engine: Any) -> None:
        self._risk_engine = risk_engine
        self._buffer: List[Dict] = []
        self._buy_dedup_set: set = set()
        self._current_date: Optional[date] = None
        self._last_prices: Dict[str, float] = {}
        self._tradable_today: Dict[str, bool] = {}
        self._rejected_count: int = 0

    def _resolve_price(self, price: Optional[float], symbol: str) -> float:
        if isinstance(price, (int, float)) and price > 0:
            return float(price)
        effective = float(self._last_prices.get(symbol, 0) or 0)
        if effective <= 0:
            raise OrderRejectedError(OrderRejectionReason.PRICE_UNRESOLVABLE, symbol,
                                     f"no valid price for {symbol}")
        return effective

    def _passes_dedup(self, symbol: str, side: str) -> None:
        if side == 'BUY' and symbol in self._buy_dedup_set:
            raise OrderRejectedError(OrderRejectionReason.DUPLICATE_BUY, symbol)

    def _passes_tradability(self, symbol: str) -> None:
        if self._tradable_today.get(symbol) is False:
            raise OrderRejectedError(
                OrderRejectionReason.BAR_UNAVAILABLE,
                symbol,
                f"not tradable on {self._current_date}",
            )

    def _passes_risk(self, symbol: str, quantity: float, price: float, side: str) -> None:
        if self._risk_engine is None:
            return
        value = price * quantity
        approved, _ = self._risk_engine.check_order(
            symbol, quantity, price, value, side=side, as_of_date=self._current_date,
        )
        if not approved:
            raise OrderRejectedError(OrderRejectionReason.RISK_REJECTED, symbol)
        self._risk_engine.record_order(symbol=symbol, order_value=value, as_of_date=self._current_date)

    def submit_order(self, symbol: str, quantity: float, side: str, order_type: str, price: Optional[float], strategy_name: str) -> None:
        try:
            if (order_type or "MARKET").upper() == "LIMIT" and (
                not isinstance(price, (int, float)) or price <= 0
            ):
                raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                      f"limit price={price!r}")
            self._passes_tradability(symbol)
            effective = self._resolve_price(price, symbol)
            self._passes_dedup(symbol, side)
            self._passes_risk(symbol, quantity, effective, side)
        except OrderRejectedError:
            self._rejected_count += 1
            return None
        order = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": order_type,
            "price": price,
            "strategy": strategy_name,
            "_risk_check_price": effective,
        }
        self._buffer.append(order)
        if side == 'BUY':
            self._buy_dedup_set.add(symbol)
        return f"bt_{len(self._buffer)}"

    def drain_pending(self, signal_date: date) -> list:
        orders = [
            DeferredOrder(
                symbol=item["symbol"],
                quantity=item["quantity"],
                side=item["side"],
                order_type=item["order_type"],
                price=item["price"],
                strategy=item["strategy"],
                signal_date=signal_date,
                risk_check_price=item.get("_risk_check_price", 0.0),
            )
            for item in self._buffer
        ]
        self._buffer.clear()
        self._buy_dedup_set.clear()
        return orders

    def drain_rejection_count(self) -> int:
        c = self._rejected_count
        self._rejected_count = 0
        return c


class _BacktestContext:
    def __init__(self, portfolio: Any, risk_engine: Any, event_bus: Any, data_provider: Any) -> None:
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.event_bus = event_bus
        self.data_provider = data_provider
        self.order_manager = _BacktestOrderManager(risk_engine)

    def submit_order(self, symbol: str, quantity: float, side: str, order_type: str, price: Optional[float], strategy_name: str) -> None:
        return self.order_manager.submit_order(symbol, quantity, side, order_type, price, strategy_name)

    def prepare_for_trading_day(
        self,
        trading_date: date,
        last_prices: Dict[str, float],
        tradable_today: Optional[Dict[str, bool]] = None,
    ):
        self.order_manager._current_date = trading_date
        self.order_manager._last_prices = last_prices
        self.order_manager._tradable_today = tradable_today or {}

    def drain_orders(self, signal_date: Optional[date] = None):
        return self.order_manager.drain_pending(signal_date)
