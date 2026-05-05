"""Backtest orchestrator — daily loop, T+1 execution, portfolio management."""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Dict, List, Any, Optional
import pandas as pd

if TYPE_CHECKING:
    from quant.features.backtest.schemas import BacktestBar, DeferredOrder

from quant.domain.ports.event_publisher import EventPublisher
from quant.features.backtest.entities import (
    BacktestDiagnostics,
    BacktestResult,
    CommissionConfig,
)
from quant.features.backtest.schemas import DeferredOrder
from quant.features.backtest.exceptions import OrderRejectedError
from quant.features.backtest.analytics import calculate_performance_metrics
from quant.features.backtest.market_rules import (
    select_currency,
    is_suspended,
)
from quant.features.backtest.order_executor import execute_order
from quant.features.backtest.dividend_processor import process_dividends
from quant.features.backtest.portfolio_factory import create_portfolio_contexts, create_context
from quant.features.backtest.nav_calculator import calculate_daily_nav, extract_open_positions

logger = logging.getLogger(__name__)


class Backtester:
    """Backtester with realistic execution."""

    def __init__(self, config: Dict[str, Any], event_bus: Optional[EventPublisher] = None,
                 lot_sizes: Optional[Dict[str, int]] = None,
                 ipo_dates: Optional[Dict[str, datetime]] = None,
                 portfolio_class=None, risk_engine_class=None, sub_portfolio_class=None,
                 benchmark_provider=None):
        self.config = config
        self.event_bus = event_bus
        self.slippage_bps = config.get("backtest", {}).get("slippage_bps", 5)
        self.risk_price_deviation_limit = config.get("backtest", {}).get("risk_price_deviation_limit", 0.15)
        self.market_impact_factor = config.get("backtest", {}).get("market_impact_factor", 0.0)
        self.force_close_on_stop = config.get("backtest", {}).get("force_close_on_stop", True)
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}
        self.benchmark_provider = benchmark_provider
        if portfolio_class is None or risk_engine_class is None or sub_portfolio_class is None:
            raise ValueError(
                "portfolio_class, risk_engine_class, and sub_portfolio_class are required. "
                "Use quant.features.trading.Portfolio, RiskEngine, and SubPortfolio."
            )
        self.portfolio_class = portfolio_class
        self.risk_engine_class = risk_engine_class
        self.sub_portfolio_class = sub_portfolio_class

        commission_config = config.get("execution", {}).get("commission", {})
        self.commission = CommissionConfig(
            US=commission_config.get("US", {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}),
            HK=commission_config.get("HK", {"type": "hk_realistic"}),
            CN=commission_config.get("CN", {"type": "cn_realistic"})
        )

    def run(
        self,
        start: datetime,
        end: datetime,
        strategies: List[Any],
        initial_cash: float = 100000,
        data_provider: Any = None,
        symbols: Optional[List[str]] = None,
        strategy_allocations: Optional[Dict[str, float]] = None,
    ) -> BacktestResult:
        if end < start:
            raise ValueError(f"end ({end}) must be >= start ({start})")
        if initial_cash <= 0:
            raise ValueError(f"initial_cash must be > 0, got {initial_cash}")
        if not strategies:
            raise ValueError("At least one strategy is required")
        symbols = symbols or []
        if not symbols:
            raise ValueError("At least one symbol is required")
        currency = select_currency(symbols)
        diag = BacktestDiagnostics()

        portfolio_map, risk_map, primary_portfolio, use_subs = create_portfolio_contexts(
            strategies, initial_cash, strategy_allocations, self.config, self.event_bus, currency,
            self.portfolio_class, self.risk_engine_class, self.sub_portfolio_class,
        )

        equity_curve_dates: List[datetime] = []
        equity_curve_values: List[float] = []
        all_trades: List[Any] = []

        entry_times: Dict[str, datetime] = {}
        entry_prices: Dict[str, float] = {}
        last_prices: Dict[str, float] = {}
        last_price_times: Dict[str, datetime] = {}
        prev_bars: "Dict[str, BacktestBar]" = {}

        deferred_orders: "List[DeferredOrder]" = []
        pending_orders: "List[DeferredOrder]" = []

        seen_names = set()
        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            if sname in seen_names:
                raise ValueError(
                    f"Duplicate strategy name '{sname}'. Each strategy must have a unique name "
                    f"to prevent fill cross-talk."
                )
            seen_names.add(sname)

        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            pf = portfolio_map[sname]
            re = risk_map[sname]
            strategy.context = create_context(pf, re, self.event_bus, data_provider)
            strategy.on_start(strategy.context)

        trading_dates_set = None
        if data_provider and hasattr(data_provider, 'trading_dates'):
            trading_dates_set = data_provider.trading_dates

        current_date = start
        while current_date <= end:
            if trading_dates_set is not None:
                lookup_key = current_date.date()
                if lookup_key not in trading_dates_set:
                    current_date += timedelta(days=1)
                    continue
            elif data_provider and hasattr(data_provider, 'get_bar_for_date'):
                # No trading calendar — fall back to checking data availability
                if not any(
                    data_provider.get_bar_for_date(s, current_date) is not None
                    for s in symbols
                ):
                    current_date += timedelta(days=1)
                    continue

            # --- Step 1: on_before_trading ---
            for strategy in strategies:
                strategy.on_before_trading(strategy.context, current_date.date())

            # --- Step 2: Load today's bar data ---
            prev_close_bars: "Dict[str, BacktestBar]" = dict(prev_bars)
            today_bars, any_suspended_today = self._load_daily_bars(
                data_provider, symbols, current_date, last_prices, last_price_times, prev_bars,
            )
            if any_suspended_today:
                diag.suspended_days += 1

            # --- Step 3: Process dividends ---
            all_stock_divs: List[Dict[str, Any]] = []
            if use_subs:
                for pf in portfolio_map.values():
                    divs = process_dividends(data_provider, pf, symbols, current_date, last_prices, entry_times, diag)
                    all_stock_divs.extend(divs)
            else:
                divs = process_dividends(data_provider, primary_portfolio, symbols, current_date, last_prices, entry_times, diag)
                all_stock_divs.extend(divs)

            for div_info in all_stock_divs:
                sym = div_info['symbol']
                for strategy in strategies:
                    current_pos = strategy.get_position(sym)
                    if current_pos > 0:
                        additional = current_pos * div_info['ratio']
                        strategy.on_fill(strategy.context, SimpleNamespace(
                            symbol=sym, quantity=additional, side="BUY",
                            price=0.0, fill_price=0.0, pnl=0.0, commission=0.0,
                            realized_pnl=0.0, entry_price=0.0, exit_price=0.0,
                            intended_qty=additional, cost_breakdown={},
                            entry_time=current_date, exit_time=current_date,
                            signal_date=current_date, fill_date=current_date,
                            strategy_name=getattr(strategy, 'name', strategy.__class__.__name__),
                        ))

            # --- Step 4: Execute deferred orders from T-1 ---
            try:
                for order in deferred_orders:
                    sym = order.symbol
                    bar = today_bars.get(sym, {})
                    if not bar or bar.get('_suspended'):
                        diag.discarded_orders += 1
                        continue
                    order_strategy = order.strategy
                    if order_strategy and order_strategy not in portfolio_map:
                        logger.warning("Order strategy '%s' not in portfolio_map, discarding order for %s", order_strategy, sym)
                        diag.discarded_orders += 1
                        continue
                    order_pf = portfolio_map.get(order_strategy, primary_portfolio)
                    try:
                        trades = execute_order(
                            order=order,
                            portfolio=order_pf,
                            symbol=sym,
                            bar=bar,
                            entry_times=entry_times,
                            entry_prices=entry_prices,
                            diag=diag,
                            lot_sizes=self.lot_sizes,
                            ipo_dates=self.ipo_dates,
                            slippage_bps=self.slippage_bps,
                            commission_config=self.commission,
                            prev_bar=prev_close_bars.get(sym),
                            risk_price_deviation_limit=self.risk_price_deviation_limit,
                            market_impact_factor=self.market_impact_factor,
                        )
                    except OrderRejectedError as e:
                        diag.record_rejection(e.reason)
                        diag.discarded_orders += 1
                        continue
                    all_trades.extend(trades)
                    target_sname = order.strategy
                    for s in strategies:
                        sname = getattr(s, 'name', s.__class__.__name__)
                        if target_sname is None or sname == target_sname:
                            for t in trades:
                                s.on_fill(s.context, t)
            finally:
                deferred_orders = []

            # --- Step 5 prep: Initialize order manager with today's state ---
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "prepare_for_trading_day"):
                    strategy.context.prepare_for_trading_day(current_date.date(), last_prices)

            # --- Step 5: Feed bar data to strategies ---
            for sym, bar_data in today_bars.items():
                for strategy in strategies:
                    strategy.on_data(strategy.context, bar_data)

            # --- Step 6: Update portfolio prices ---
            if use_subs:
                for pf in portfolio_map.values():
                    self._update_portfolio_prices(pf, last_prices)
            else:
                self._update_portfolio_prices(primary_portfolio, last_prices)

            # --- Step 7: Strategy signal generation ---
            for strategy in strategies:
                strategy.on_after_trading(strategy.context, current_date.date())

            # --- Step 8: Collect pending orders ---
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "drain_orders"):
                    try:
                        for order in strategy.context.drain_orders(signal_date=current_date):
                            pending_orders.append(order)
                    except (OrderRejectedError, ValueError) as e:
                        logger.warning("Invalid order from strategy %s on %s: %s",
                                       getattr(strategy, 'name', strategy.__class__.__name__),
                                       current_date, e)

            for order in pending_orders:
                deferred_orders.append(order)
            pending_orders = []

            # Collect submission-level rejections (dedup, price-unresolvable, risk)
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                    diag.submission_rejected += strategy.context.order_manager.drain_rejection_count()

            # --- Step 9: Record NAV + reset daily state ---
            nav = calculate_daily_nav(portfolio_map, primary_portfolio, use_subs)
            equity_curve_dates.append(current_date)
            equity_curve_values.append(nav)

            self._reset_daily(portfolio_map, risk_map, use_subs, diag)

            current_date += timedelta(days=1)

        if deferred_orders:
            expired_count = len(deferred_orders)
            diag.expired_orders += expired_count
            diag.discarded_orders += expired_count
            deferred_orders = []

        for strategy in strategies:
            strategy.on_stop(strategy.context)

        # Execute final close-out orders generated by on_stop when explicitly enabled.
        stop_signal_time = max(last_price_times.values()) if last_price_times else current_date
        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            ctx = strategy.context
            if not hasattr(ctx, 'drain_orders'):
                continue
            stop_orders = list(ctx.drain_orders(signal_date=stop_signal_time))
            if hasattr(ctx, 'order_manager'):
                diag.submission_rejected += ctx.order_manager.drain_rejection_count()
            if not stop_orders:
                continue
            diag.forced_closeout_orders += len(stop_orders)
            if not self.force_close_on_stop:
                diag.expired_orders += len(stop_orders)
                diag.discarded_orders += len(stop_orders)
                continue
            for order in stop_orders:
                sym = order.symbol
                close_price = last_prices.get(sym)
                if not close_price or close_price <= 0:
                    diag.expired_orders += 1
                    diag.discarded_orders += 1
                    continue
                fill_ts = last_price_times.get(sym, stop_signal_time)
                bar = {
                    'symbol': sym, 'open': close_price, 'high': close_price,
                    'low': close_price, 'close': close_price, 'volume': 10_000_000,
                    'timestamp': fill_ts,
                }
                order_pf = portfolio_map.get(order.strategy or sname, primary_portfolio)
                try:
                    trades = execute_order(
                        order=order, portfolio=order_pf, symbol=sym, bar=bar,
                        entry_times=entry_times, entry_prices=entry_prices,
                        diag=diag, lot_sizes=self.lot_sizes, ipo_dates=self.ipo_dates,
                        slippage_bps=self.slippage_bps, commission_config=self.commission,
                        prev_bar=None,
                        risk_price_deviation_limit=self.risk_price_deviation_limit,
                        market_impact_factor=self.market_impact_factor,
                    )
                except OrderRejectedError as e:
                    diag.record_rejection(e.reason)
                    diag.discarded_orders += 1
                    continue
                all_trades.extend(trades)
                diag.forced_closeout_trades += len(trades)
                for s in strategies:
                    sn = getattr(s, 'name', s.__class__.__name__)
                    if sn == sname:
                        for t in trades:
                            s.on_fill(s.context, t)

        # Record final NAV after all post-loop executions (deferred + on_stop)
        nav = calculate_daily_nav(portfolio_map, primary_portfolio, use_subs)
        equity_curve_dates.append(current_date)
        equity_curve_values.append(nav)

        equity_curve = pd.Series(equity_curve_values, index=equity_curve_dates)

        benchmark_returns = None
        if self.benchmark_provider is not None:
            try:
                benchmark_returns = self.benchmark_provider.get_benchmark_returns(start, end)
            except Exception:
                benchmark_returns = None

        metrics = calculate_performance_metrics(equity_curve, all_trades, initial_cash, benchmark_returns)

        diag.total_gross_pnl = sum(t.pnl for t in all_trades) + diag.total_commission

        open_positions = extract_open_positions(
            portfolio_map, primary_portfolio, last_prices, entry_times, use_subs,
        )

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
            metrics=metrics,
            diagnostics=diag,
            open_positions=open_positions,
        )

    def _load_daily_bars(self, data_provider, symbols, current_date, last_prices, last_price_times, prev_bars):
        today_bars: Dict[str, Dict] = {}
        any_suspended = False

        if not data_provider:
            return today_bars, any_suspended

        has_fast_lookup = hasattr(data_provider, 'get_bar_for_date')
        for symbol in symbols:
            try:
                if has_fast_lookup:
                    bar_data = data_provider.get_bar_for_date(symbol, current_date)
                    if bar_data is None:
                        continue
                    bar_data = dict(bar_data)
                    bar_data['symbol'] = symbol
                    bar_data['_suspended'] = is_suspended(bar_data)
                    today_bars[symbol] = bar_data
                    if bar_data['_suspended']:
                        any_suspended = True
                    else:
                        bar_close = bar_data.get('close', 0)
                        if bar_close > 0:
                            last_prices[symbol] = bar_close
                            last_price_times[symbol] = self._bar_timestamp(bar_data, current_date)
                        prev_bars[symbol] = bar_data
                else:
                    bars = data_provider.get_bars(
                        symbol, current_date, current_date + timedelta(days=1),
                        self.config.get("data", {}).get("default_timeframe", "1d"),
                    )
                    if bars is not None and not bars.empty:
                        for _, bar in bars.iterrows():
                            bar_data = bar.to_dict()
                            bar_data['timestamp'] = bar_data.get('timestamp', bar.name if hasattr(bar, 'name') else current_date)
                            bar_data['symbol'] = symbol
                            bar_data['_suspended'] = is_suspended(bar_data)
                            today_bars[symbol] = bar_data
                            if bar_data['_suspended']:
                                any_suspended = True
                            else:
                                bar_close = bar_data.get('close', 0)
                                if bar_close > 0:
                                    last_prices[symbol] = bar_close
                                    last_price_times[symbol] = self._bar_timestamp(bar_data, current_date)
                                prev_bars[symbol] = bar_data
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Error loading bar data for %s on %s: %s", symbol, current_date, e)

        return today_bars, any_suspended

    @staticmethod
    def _bar_timestamp(bar_data: Dict[str, Any], fallback: datetime) -> datetime:
        ts = bar_data.get('timestamp', fallback)
        if isinstance(ts, datetime):
            return ts
        return pd.Timestamp(ts).to_pydatetime()

    @staticmethod
    def _update_portfolio_prices(portfolio: Any, last_prices: Dict[str, float]) -> None:
        for symbol, pos in portfolio.positions.items():
            if pos.quantity != 0 and symbol in last_prices:
                pos.update_market_price(last_prices[symbol])

    @staticmethod
    def _reset_daily(portfolio_map, risk_map, use_subs, diag):
        if use_subs:
            for pf in portfolio_map.values():
                pf.reset_daily()
            for re in risk_map.values():
                diag.risk_skipped_orders += re._risk_rejected_count
                re.reset_daily()
        else:
            seen_pf = set()
            for pf in portfolio_map.values():
                if id(pf) not in seen_pf:
                    seen_pf.add(id(pf))
                    pf.reset_daily()
            seen_risk = set()
            for re in risk_map.values():
                if id(re) not in seen_risk:
                    seen_risk.add(id(re))
                    diag.risk_skipped_orders += re._risk_rejected_count
                    re.reset_daily()
