"""Pure data structures for backtest results, diagnostics, and context."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd

from quant.domain.models.trade import Trade
from quant.domain.context import StrategyScopedOrderManager
from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason
from quant.features.backtest.market_rules import get_market
from quant.features.backtest.schemas import (
    DeferredOrder,
    EXECUTION_TIMING_NEXT_OPEN,
    VALID_EXECUTION_TIMINGS,
)
from quant.runtime.execution_cost import (
    bar_close_price,
    estimate_cost_protection_limit,
    has_historical_cost_model,
)


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
    exposure_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    execution_observations: List[Dict[str, Any]] = field(default_factory=list)

    def record_rejection(self, reason: OrderRejectionReason) -> None:
        key = reason.name.lower()
        self.rejection_counts[key] = self.rejection_counts.get(key, 0) + 1

    def record_exposure_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.exposure_snapshots.append(snapshot)

    def record_execution_observation(self, observation: Dict[str, Any]) -> None:
        self.execution_observations.append(observation)

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
    supports_backtest_execution_timing = True

    def __init__(
        self,
        risk_engine: Any,
        *,
        base_slippage_bps: float = 5.0,
        execution_cost_model: Optional[Dict[str, Any]] = None,
        market_impact_factor: float = 0.0,
    ) -> None:
        self._risk_engine = risk_engine
        self._base_slippage_bps = float(base_slippage_bps or 0.0)
        self._execution_cost_model = execution_cost_model
        self._market_impact_factor = float(market_impact_factor or 0.0)
        self._buffer: List[Dict] = []
        self._buy_dedup_set: set = set()
        self._current_date: Optional[date] = None
        self._last_prices: Dict[str, float] = {}
        self._current_bars: Dict[str, Any] = {}
        self._tradable_today: Optional[Dict[str, bool]] = None
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
        if self._tradable_today is None:
            return
        if symbol not in self._tradable_today:
            raise OrderRejectedError(
                OrderRejectionReason.BAR_UNAVAILABLE,
                symbol,
                f"no bar on {self._current_date}",
            )
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

    def _as_historical_limit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str,
        price: Optional[float],
        reference_price: float,
    ) -> tuple[str, Optional[float], Dict[str, float]]:
        order_type_text = (order_type or "MARKET").upper()
        if order_type_text != "MARKET":
            return order_type, price, {}
        market = get_market(symbol)
        if not has_historical_cost_model(self._execution_cost_model, symbol, market):
            return order_type, price, {}
        auto_limit = bool(
            (self._execution_cost_model or {}).get(
                "market_orders_as_limits",
                market == "CN",
            )
        )
        if not auto_limit:
            return order_type, price, {}
        signal_bar = self._current_bars.get(symbol) or {}
        bar_reference = bar_close_price(signal_bar)
        cost_reference_price = bar_reference or reference_price
        estimate = estimate_cost_protection_limit(
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=cost_reference_price,
            market=market,
            signal_bar=signal_bar,
            base_slippage_bps=self._base_slippage_bps,
            execution_cost_model=self._execution_cost_model,
            fallback_max_cost_bps=None,
            fallback_impact_factor=self._market_impact_factor,
        )
        return "LIMIT", None, {
            "execution_cost_reference_price": float(cost_reference_price),
            "execution_cost_bps": float(estimate.cost_bps),
            "execution_slippage_bps": float(estimate.slippage_bps),
            "execution_impact_bps": float(estimate.impact_bps),
        }

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str,
        price: Optional[float],
        strategy_name: str,
        execution_timing: Optional[str] = None,
    ) -> None:
        try:
            order_type_text = (order_type or "MARKET").upper()
            timing = str(execution_timing or EXECUTION_TIMING_NEXT_OPEN).upper()
            if timing not in VALID_EXECUTION_TIMINGS:
                raise OrderRejectedError(
                    OrderRejectionReason.PRICE_INVALID,
                    symbol,
                    f"unsupported execution_timing={execution_timing!r}",
                )
            if order_type_text == "LIMIT" and (
                not isinstance(price, (int, float)) or price <= 0
            ):
                raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                      f"limit price={price!r}")
            self._passes_tradability(symbol)
            effective = self._resolve_price(price, symbol)
            self._passes_dedup(symbol, side)
            self._passes_risk(symbol, quantity, effective, side)
            submitted_order_type, submitted_price, cost_metadata = self._as_historical_limit_order(
                symbol, quantity, side, order_type_text, price, effective,
            )
        except OrderRejectedError:
            self._rejected_count += 1
            return None
        order = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": submitted_order_type,
            "price": submitted_price,
            "strategy": strategy_name,
            "_risk_check_price": effective,
            "execution_timing": timing,
        }
        order.update(cost_metadata)
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
                execution_timing=item.get("execution_timing", EXECUTION_TIMING_NEXT_OPEN),
                execution_cost_reference_price=item.get("execution_cost_reference_price"),
                execution_cost_bps=item.get("execution_cost_bps"),
                execution_slippage_bps=item.get("execution_slippage_bps"),
                execution_impact_bps=item.get("execution_impact_bps"),
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
    def __init__(
        self,
        portfolio: Any,
        risk_engine: Any,
        event_bus: Any,
        data_provider: Any,
        *,
        base_slippage_bps: float = 5.0,
        execution_cost_model: Optional[Dict[str, Any]] = None,
        market_impact_factor: float = 0.0,
        strategy_name: Optional[str] = None,
    ) -> None:
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.event_bus = event_bus
        self.data_provider = data_provider
        self.strategy_name = str(strategy_name) if strategy_name else None
        self._order_manager = _BacktestOrderManager(
            risk_engine,
            base_slippage_bps=base_slippage_bps,
            execution_cost_model=execution_cost_model,
            market_impact_factor=market_impact_factor,
        )
        self.order_manager = StrategyScopedOrderManager(self._order_manager, self.strategy_name)

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str,
        price: Optional[float],
        strategy_name: str,
        execution_timing: Optional[str] = None,
    ) -> None:
        return self.order_manager.submit_order(
            symbol,
            quantity,
            side,
            order_type,
            price,
            strategy_name,
            execution_timing=execution_timing,
        )

    def prepare_for_trading_day(
        self,
        trading_date: date,
        last_prices: Dict[str, float],
        tradable_today: Optional[Dict[str, bool]] = None,
        current_bars: Optional[Dict[str, Any]] = None,
    ):
        self._order_manager._current_date = trading_date
        self._order_manager._last_prices = last_prices
        self._order_manager._tradable_today = tradable_today
        self._order_manager._current_bars = current_bars or {}

    def drain_orders(self, signal_date: Optional[date] = None):
        return self._order_manager.drain_pending(signal_date)
