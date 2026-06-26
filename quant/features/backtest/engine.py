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
from quant.features.backtest.schemas import DeferredOrder, EXECUTION_TIMING_SAME_CLOSE
from quant.features.backtest.exceptions import OrderRejectedError
from quant.features.backtest.analytics import calculate_performance_metrics
from quant.features.backtest.market_rules import (
    select_currency,
    is_suspended,
)
from quant.features.backtest.order_executor import execute_order
from quant.features.backtest.dividend_processor import (
    process_adjustment_factor_changes,
    process_dividends,
)
from quant.features.backtest.portfolio_factory import create_portfolio_contexts, create_context
from quant.features.backtest.nav_calculator import calculate_daily_nav, extract_open_positions
from quant.runtime.daily_strategy_runner import run_daily_snapshots
from quant.runtime.strategy_cycle import before_trading, start_strategy, stop_strategy

logger = logging.getLogger(__name__)


def _strategy_name(strategy) -> str:
    return getattr(strategy, 'name', strategy.__class__.__name__)


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
        self.execution_cost_model = (
            config.get("backtest", {}).get("execution_cost_model")
            or config.get("execution", {}).get("cost_model")
        )
        self.force_close_on_stop = config.get("backtest", {}).get("force_close_on_stop", True)
        self.strict_daily_snapshot = bool(config.get("backtest", {}).get(
            "strict_daily_snapshot",
            config.get("live_trading", {}).get("strict_daily_snapshot", True),
        ))
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
        initial_cash: float = 10000,
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
        symbol_lookup = set(symbols)
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
        latest_bars: "Dict[str, BacktestBar]" = {}

        deferred_orders: "List[DeferredOrder]" = []
        pending_orders: "List[DeferredOrder]" = []

        seen_names = set()
        for strategy in strategies:
            sname = _strategy_name(strategy)
            if sname in seen_names:
                raise ValueError(
                    f"Duplicate strategy name '{sname}'. Each strategy must have a unique name "
                    f"to prevent fill cross-talk."
                )
            seen_names.add(sname)

        for strategy in strategies:
            sname = _strategy_name(strategy)
            pf = portfolio_map[sname]
            re = risk_map[sname]
            strategy.context = create_context(
                pf,
                re,
                self.event_bus,
                data_provider,
                base_slippage_bps=self.slippage_bps,
                execution_cost_model=self.execution_cost_model,
                market_impact_factor=self.market_impact_factor,
                strategy_name=sname,
            )
            start_strategy(strategy)

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
                before_trading(strategy, current_date.date())

            # --- Step 2: Load today's bar data ---
            prev_close_bars: "Dict[str, BacktestBar]" = dict(prev_bars)
            today_bars, any_suspended_today = self._load_daily_bars(
                data_provider, symbols, current_date, last_prices, last_price_times, prev_bars, latest_bars,
                symbol_lookup,
            )
            if any_suspended_today:
                diag.suspended_days += 1

            # --- Step 3: Process dividends ---
            all_stock_divs: List[Dict[str, Any]] = []
            all_share_adjustments: List[Dict[str, Any]] = []
            if use_subs:
                for sname, pf in portfolio_map.items():
                    divs = process_dividends(data_provider, pf, symbols, current_date, last_prices, entry_times, diag)
                    for div_info in divs:
                        div_info['strategy'] = sname
                    all_stock_divs.extend(divs)
                    adjustments = process_adjustment_factor_changes(
                        data_provider, pf, prev_close_bars, today_bars, current_date
                    )
                    for adj_info in adjustments:
                        adj_info['strategy'] = sname
                    all_share_adjustments.extend(adjustments)
            else:
                divs = process_dividends(data_provider, primary_portfolio, symbols, current_date, last_prices, entry_times, diag)
                all_stock_divs.extend(divs)
                all_share_adjustments.extend(process_adjustment_factor_changes(
                    data_provider, primary_portfolio, prev_close_bars, today_bars, current_date
                ))

            for div_info in [*all_stock_divs, *all_share_adjustments]:
                sym = div_info['symbol']
                target_sname = div_info.get('strategy')
                for strategy in strategies:
                    if target_sname is not None and _strategy_name(strategy) != target_sname:
                        continue
                    current_pos = strategy.get_position(sym)
                    if current_pos > 0:
                        if 'quantity_delta' in div_info:
                            quantity_delta = div_info['quantity_delta']
                        elif 'additional_shares' in div_info:
                            quantity_delta = div_info['additional_shares']
                        else:
                            quantity_delta = current_pos * div_info['ratio']
                        side = "BUY" if quantity_delta >= 0 else "SELL"
                        strategy.on_fill(strategy.context, SimpleNamespace(
                            symbol=sym, quantity=abs(quantity_delta), side=side,
                            price=0.0, fill_price=0.0, pnl=0.0, commission=0.0,
                            realized_pnl=0.0, entry_price=0.0, exit_price=0.0,
                            intended_qty=abs(quantity_delta), cost_breakdown={},
                            entry_time=current_date, exit_time=current_date,
                            signal_date=current_date, fill_date=current_date,
                            strategy_name=_strategy_name(strategy),
                        ))

            # --- Step 4: Execute deferred orders from T-1 ---
            self._execute_deferred_orders(
                deferred_orders, today_bars, prev_close_bars, last_prices,
                current_date, portfolio_map, primary_portfolio, use_subs,
                strategies, all_trades, diag, self.lot_sizes, self.commission,
                self.slippage_bps, self.market_impact_factor,
                self.execution_cost_model,
                self.risk_price_deviation_limit,
                entry_times, entry_prices,
            )
            deferred_orders = []

            # --- Step 5 prep: Initialize order manager with today's state ---
            tradable_today = {}
            for sym, bar_data in today_bars.items():
                suspended = bar_data.get("_suspended")
                if suspended is None:
                    suspended = is_suspended(bar_data)
                tradable_today[sym] = not suspended
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "prepare_for_trading_day"):
                    strategy.context.prepare_for_trading_day(
                        current_date.date(),
                        last_prices,
                        tradable_today,
                        current_bars=today_bars,
                    )

            # --- Step 5-7: Shared daily snapshot signal generation ---
            self._run_strategy_daily_snapshots(
                strategies, current_date.date(), today_bars, last_prices,
                portfolio_map, primary_portfolio, use_subs,
            )

            # --- Step 8: Collect pending orders ---
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "drain_orders"):
                    try:
                        for order in strategy.context.drain_orders(signal_date=current_date):
                            pending_orders.append(order)
                    except (OrderRejectedError, ValueError) as e:
                        logger.warning("Invalid order from strategy %s on %s: %s",
                                       _strategy_name(strategy),
                                       current_date, e)

            same_close_orders = [
                order for order in pending_orders
                if order.execution_timing == EXECUTION_TIMING_SAME_CLOSE
            ]
            next_open_orders = [
                order for order in pending_orders
                if order.execution_timing != EXECUTION_TIMING_SAME_CLOSE
            ]
            if same_close_orders:
                self._execute_deferred_orders(
                    same_close_orders, today_bars, prev_close_bars, last_prices,
                    current_date, portfolio_map, primary_portfolio, use_subs,
                    strategies, all_trades, diag, self.lot_sizes, self.commission,
                    self.slippage_bps, self.market_impact_factor,
                    self.execution_cost_model,
                    self.risk_price_deviation_limit,
                    entry_times, entry_prices,
                )
                if use_subs:
                    for pf in portfolio_map.values():
                        self._update_portfolio_prices(pf, last_prices)
                else:
                    self._update_portfolio_prices(primary_portfolio, last_prices)
                for strategy in strategies:
                    if hasattr(strategy, "context") and hasattr(strategy.context, "drain_orders"):
                        try:
                            for order in strategy.context.drain_orders(signal_date=current_date):
                                if order.execution_timing == EXECUTION_TIMING_SAME_CLOSE:
                                    diag.discarded_orders += 1
                                else:
                                    next_open_orders.append(order)
                        except (OrderRejectedError, ValueError) as e:
                            logger.warning("Invalid order from strategy %s on %s: %s",
                                           _strategy_name(strategy),
                                           current_date, e)

            for order in next_open_orders:
                deferred_orders.append(order)
            pending_orders = []

            # Collect submission-level rejections (dedup, price-unresolvable, tradability, risk)
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                    diag.submission_rejected += strategy.context.order_manager.drain_rejection_count()

            # --- Step 9: Record NAV + reset daily state ---
            nav = calculate_daily_nav(portfolio_map, primary_portfolio, use_subs)
            equity_curve_dates.append(current_date)
            equity_curve_values.append(nav)
            self._record_exposure_snapshot(
                diag, current_date, nav, portfolio_map, primary_portfolio, use_subs,
            )

            self._reset_daily(portfolio_map, risk_map, use_subs, diag)

            current_date += timedelta(days=1)

        if deferred_orders:
            expired_count = len(deferred_orders)
            diag.expired_orders += expired_count
            diag.discarded_orders += expired_count
            deferred_orders = []

        self._record_final_suspended_holdings(
            diag, portfolio_map, primary_portfolio, use_subs, latest_bars, last_prices,
        )

        stop_signal_time = max(last_price_times.values()) if last_price_times else current_date
        stop_signal_date = stop_signal_time.date() if hasattr(stop_signal_time, "date") else stop_signal_time
        for strategy in strategies:
            if hasattr(strategy, "context") and hasattr(strategy.context, "prepare_for_trading_day"):
                strategy.context.prepare_for_trading_day(stop_signal_date, last_prices, None)
            stop_strategy(strategy)

        # Execute final close-out orders generated by on_stop when explicitly enabled.
        for strategy in strategies:
            sname = _strategy_name(strategy)
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
            synthetic_bars = {}
            for order in stop_orders:
                sym = order.symbol
                close_price = last_prices.get(sym)
                if not close_price or close_price <= 0:
                    diag.expired_orders += 1
                    diag.discarded_orders += 1
                    continue
                fill_ts = last_price_times.get(sym, stop_signal_time)
                synthetic_bars[sym] = {
                    'symbol': sym, 'open': close_price, 'high': close_price,
                    'low': close_price, 'close': close_price, 'volume': 10_000_000,
                    'timestamp': fill_ts,
                }
            trades_before = len(all_trades)
            self._execute_deferred_orders(
                stop_orders, synthetic_bars, {}, last_prices,
                stop_signal_time, portfolio_map, primary_portfolio, use_subs,
                strategies, all_trades, diag, self.lot_sizes, self.commission,
                self.slippage_bps, self.market_impact_factor,
                self.execution_cost_model,
                self.risk_price_deviation_limit,
                entry_times, entry_prices,
                ignore_settlement=True,
            )
            diag.forced_closeout_trades += len(all_trades) - trades_before

        # Record final NAV after all post-loop executions (deferred + on_stop)
        nav = calculate_daily_nav(portfolio_map, primary_portfolio, use_subs)
        if equity_curve_values:
            equity_curve_values[-1] = nav
        else:
            equity_curve_dates.append(end)
            equity_curve_values.append(nav)

        return self._build_backtest_result(
            equity_curve_dates, equity_curve_values, all_trades,
            primary_portfolio, use_subs, initial_cash, start, end,
            symbols, strategies, config=self.config, diag=diag,
            portfolio_map=portfolio_map, last_prices=last_prices,
            entry_times=entry_times,
        )

    def _execute_deferred_orders(
        self,
        deferred_orders: list,
        today_bars: dict,
        prev_close_bars: dict,
        last_prices: dict,
        current_date,
        portfolio_map: dict,
        primary_portfolio,
        use_subs: bool,
        strategies: list,
        all_trades: list,
        diag,
        lot_sizes: dict,
        commission_cfg,
        slippage_bps: float,
        market_impact_factor: float,
        execution_cost_model: dict | None,
        risk_price_deviation_limit: float,
        entry_times: dict,
        entry_prices: dict,
        ignore_settlement: bool = False,
    ) -> None:
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
                    lot_sizes=lot_sizes,
                    ipo_dates=self.ipo_dates,
                    slippage_bps=slippage_bps,
                    commission_config=commission_cfg,
                    prev_bar=prev_close_bars.get(sym),
                    risk_price_deviation_limit=risk_price_deviation_limit,
                    market_impact_factor=market_impact_factor,
                    execution_cost_model=execution_cost_model,
                    ignore_settlement=ignore_settlement,
                )
            except OrderRejectedError as e:
                diag.record_rejection(e.reason)
                diag.discarded_orders += 1
                continue
            all_trades.extend(trades)
            target_sname = order.strategy
            if target_sname is None:
                logger.error("Order for %s has no strategy — fill not dispatched", order.symbol)
                continue
            for s in strategies:
                sname = _strategy_name(s)
                if sname == target_sname:
                    for t in trades:
                        s.on_fill(s.context, t)

    def _run_strategy_daily_snapshots(
        self,
        strategies: list,
        trading_date,
        today_bars: dict,
        last_prices: Dict[str, float],
        portfolio_map: dict,
        primary_portfolio,
        use_subs: bool,
    ) -> None:
        if not today_bars:
            return

        def mark_portfolios(_snapshot) -> None:
            if use_subs:
                for pf in portfolio_map.values():
                    self._update_portfolio_prices(pf, last_prices)
            else:
                self._update_portfolio_prices(primary_portfolio, last_prices)

        results = run_daily_snapshots(
            strategies,
            trading_date,
            tuple(today_bars.values()),
            strict=self.strict_daily_snapshot,
            after_feed=mark_portfolios,
        )
        if results and not any(result.ran for _, result in results):
            mark_portfolios(None)
        for strategy, result in results:
            if not result.ran:
                logger.warning(
                    "Skipped daily snapshot for %s on %s: missing=%s stale=%s missing_fields=%s",
                    _strategy_name(strategy),
                    trading_date,
                    result.missing_symbols,
                    result.stale_symbols,
                    result.missing_fields,
                )

    def _record_exposure_snapshot(
        self,
        diag,
        current_date,
        nav: float,
        portfolio_map: dict,
        primary_portfolio,
        use_subs: bool,
    ) -> None:
        if nav <= 0:
            return
        if use_subs:
            portfolios = list(portfolio_map.values())
            cash = float(getattr(primary_portfolio, "cash", 0.0) or 0.0) + sum(
                float(getattr(pf, "cash", 0.0) or 0.0) for pf in portfolios
            )
        else:
            portfolios = [primary_portfolio]
            cash = float(getattr(primary_portfolio, "cash", 0.0) or 0.0)
        positions = []
        for pf in portfolios:
            positions.extend(
                pos for pos in getattr(pf, "positions", {}).values()
                if getattr(pos, "quantity", 0.0) > 0
            )
        position_values = [
            float(getattr(pos, "market_value", 0.0) or 0.0)
            for pos in positions
            if float(getattr(pos, "market_value", 0.0) or 0.0) > 0
        ]
        positions_value = sum(position_values)
        diag.record_exposure_snapshot({
            "date": current_date.date().isoformat() if hasattr(current_date, "date") else str(current_date)[:10],
            "nav": float(nav),
            "cash": cash,
            "cash_pct": cash / float(nav),
            "positions_value": positions_value,
            "gross_exposure_pct": positions_value / float(nav),
            "position_count": len(position_values),
            "max_position_weight": max((value / float(nav) for value in position_values), default=0.0),
        })

    def _build_backtest_result(
        self,
        equity_curve_dates: list,
        equity_curve_values: list,
        all_trades: list,
        primary_portfolio,
        use_subs: bool,
        initial_cash: float,
        start,
        end,
        symbols: list,
        strategies: list,
        config: dict,
        diag,
        portfolio_map: dict,
        last_prices: dict,
        entry_times: dict,
    ) -> BacktestResult:
        equity_curve = pd.Series(equity_curve_values, index=equity_curve_dates)

        benchmark_returns = None
        if self.benchmark_provider is not None:
            try:
                benchmark_returns = self.benchmark_provider.get_benchmark_returns(start, end)
            except Exception as e:
                logger.warning("Benchmark retrieval failed: %s", e)
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

    def _load_daily_bars(self, data_provider, symbols, current_date, last_prices, last_price_times, prev_bars, latest_bars, symbol_lookup=None):
        today_bars: Dict[str, Dict] = {}
        any_suspended = False

        if not data_provider:
            return today_bars, any_suspended

        if hasattr(data_provider, 'get_bars_for_date'):
            try:
                for raw_bar in data_provider.get_bars_for_date(current_date):
                    symbol = raw_bar.get('symbol') if isinstance(raw_bar, dict) else getattr(raw_bar, 'symbol', None)
                    if not symbol or (symbol_lookup is not None and symbol not in symbol_lookup):
                        continue
                    if isinstance(raw_bar, dict):
                        bar_data = raw_bar
                        if bar_data.get('symbol') != symbol:
                            bar_data = dict(raw_bar)
                            bar_data['symbol'] = symbol
                    else:
                        bar_data = dict(raw_bar)
                        bar_data['symbol'] = symbol
                    if '_suspended' not in bar_data:
                        bar_data['_suspended'] = is_suspended(bar_data)
                    today_bars[symbol] = bar_data
                    latest_bars[symbol] = bar_data
                    if bar_data['_suspended']:
                        any_suspended = True
                    else:
                        bar_close = bar_data.get('close', 0)
                        if bar_close > 0:
                            last_prices[symbol] = bar_close
                            last_price_times[symbol] = self._bar_timestamp(bar_data, current_date)
                        prev_bars[symbol] = bar_data
                return today_bars, any_suspended
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Error loading daily bar batch for %s: %s", current_date, e)

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
                    latest_bars[symbol] = bar_data
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
                        symbol, current_date, current_date,
                        self.config.get("data", {}).get("default_timeframe", "1d"),
                    )
                    if bars is not None and not bars.empty:
                        if "timestamp" in bars.columns:
                            timestamps = pd.to_datetime(bars["timestamp"], errors="coerce")
                        else:
                            timestamps = pd.to_datetime(bars.index, errors="coerce")
                        bar_dates = timestamps.dt.date if hasattr(timestamps, "dt") else timestamps.date
                        bars = bars.loc[bar_dates == current_date.date()]
                        for _, bar in bars.iterrows():
                            bar_data = bar.to_dict()
                            bar_data['timestamp'] = bar_data.get('timestamp', bar.name if hasattr(bar, 'name') else current_date)
                            bar_data['symbol'] = symbol
                            bar_data['_suspended'] = is_suspended(bar_data)
                            today_bars[symbol] = bar_data
                            latest_bars[symbol] = bar_data
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
    def _record_final_suspended_holdings(
        diag: BacktestDiagnostics,
        portfolio_map: Dict[str, Any],
        primary_portfolio: Any,
        use_subs: bool,
        prev_bars: Dict[str, Dict[str, Any]],
        last_prices: Dict[str, float],
    ) -> None:
        holdings = []
        portfolios = portfolio_map.values() if use_subs else [primary_portfolio]
        for portfolio in portfolios:
            for symbol, pos in getattr(portfolio, "positions", {}).items():
                quantity = float(getattr(pos, "quantity", 0.0) or 0.0)
                if quantity <= 0:
                    continue
                final_bar = prev_bars.get(symbol) or {}
                if not final_bar.get("_suspended"):
                    continue
                price = float(last_prices.get(symbol) or 0.0)
                if price <= 0:
                    market_value = float(getattr(pos, "market_value", 0.0) or 0.0)
                    price = (
                        market_value / quantity
                        if market_value > 0 and quantity > 0
                        else float(getattr(pos, "avg_cost", 0.0) or 0.0)
                    )
                holdings.append((symbol, quantity * max(price, 0.0)))
        diag.final_suspended_holding_nav = sum(value for _, value in holdings)
        diag.final_suspended_holding_count = len(holdings)
        diag.final_suspended_symbols = sorted({symbol for symbol, _ in holdings})

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
