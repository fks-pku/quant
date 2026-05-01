"""Backtest orchestrator — daily loop, T+1 execution, portfolio management."""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Any, Optional
import pandas as pd

if TYPE_CHECKING:
    from quant.features.backtest.schemas import BacktestBar, DeferredOrder

from quant.domain.ports.event_publisher import EventPublisher
from quant.features.backtest.entities import (
    BacktestDiagnostics,
    BacktestResult,
    CommissionConfig,
    BacktestResultExporter,
    _BacktestContext,
    _BacktestOrderManager,
)
from quant.features.backtest.analytics import calculate_performance_metrics
from quant.features.backtest.market_rules import (
    select_currency,
    is_suspended,
    get_market,
    get_lot_size,
    get_earliest_lot_time,
    fifo_lot_slices,
    is_price_at_limit,
    DEFAULT_LOT_SIZE,
)
from quant.features.backtest.order_executor import execute_order
from quant.features.backtest.dividend_processor import process_dividends
from quant.features.backtest.portfolio_factory import create_portfolio_contexts, create_context
from quant.features.backtest.nav_calculator import calculate_daily_nav, extract_open_positions

from quant.features.backtest.commission import (
    HK_COMMISSION_RATE, HK_STAMP_DUTY_RATE, HK_SFC_LEVY_RATE,
    HK_CLEARING_RATE, HK_TRADING_FEE_RATE, HK_MIN_COMMISSION, HK_TRADING_SYSTEM_FEE,
    CN_COMMISSION_RATE, CN_STAMP_DUTY_RATE, CN_TRANSFER_FEE_RATE,
    CN_REGULATOR_FEE_RATE, CN_MIN_COMMISSION,
    US_SEC_FEE_RATE, US_FINRA_TAF_PER_SHARE,
    VOLUME_PARTICIPATION_LIMIT,
)
from quant.features.backtest.dividend_processor import (
    CN_DIVIDEND_TAX_SHORT_DAYS, CN_DIVIDEND_TAX_MEDIUM_DAYS,
)

logger = logging.getLogger(__name__)


class Backtester:
    """Backtester with realistic execution."""

    IPO_NO_LIMIT_DAYS = 9

    @staticmethod
    def _is_suspended(bar: Dict) -> bool:
        return is_suspended(bar)

    @staticmethod
    def _detect_market(self_or_none, symbol: str = None) -> str:
        return get_market(symbol if symbol is not None else self_or_none)

    @staticmethod
    def _fifo_lot_slices(pos, sell_qty: float) -> List[tuple]:
        return fifo_lot_slices(pos, sell_qty)

    @staticmethod
    def _earliest_lot_time(pos) -> Optional[datetime]:
        return get_earliest_lot_time(pos)

    def _is_cn_price_at_limit(self, symbol: str, open_price: float, prev_close: float,
                               current_date=None) -> bool:
        return is_price_at_limit(symbol, open_price, prev_close, current_date, self.ipo_dates)

    def _calculate_cn_dividend_tax(self, pos: Any, cash_div: float, current_date: Any) -> float:
        from quant.features.backtest.dividend_processor import calculate_cn_dividend_tax
        return calculate_cn_dividend_tax(pos, cash_div, current_date)

    def _get_lot_size(self, symbol: str) -> int:
        return get_lot_size(symbol, self.lot_sizes)

    def _detect_currency(self, symbols: List[str]) -> str:
        return select_currency(symbols)

    def _calculate_commission_breakdown(self, price: float, quantity: float, market: str, side: str) -> Dict[str, float]:
        from quant.features.backtest.commission import (
            _calculate_us_commission,
            _calculate_cn_commission,
            _calculate_hk_commission,
        )
        trade_value = price * quantity
        if market == "US":
            return _calculate_us_commission(quantity, trade_value, side, self.commission)
        elif market == "CN":
            return _calculate_cn_commission(trade_value, side)
        return _calculate_hk_commission(trade_value, side)

    def _execute_order(
        self,
        order: "DeferredOrder",
        portfolio: Any,
        symbol: str,
        bar: "BacktestBar",
        entry_times: Dict[str, datetime],
        entry_prices: Dict[str, float],
        diag: BacktestDiagnostics,
        prev_bar: Optional["BacktestBar"] = None,
    ) -> List:
        return execute_order(
            order=order,
            portfolio=portfolio,
            symbol=symbol,
            bar=bar,
            entry_times=entry_times,
            entry_prices=entry_prices,
            diag=diag,
            lot_sizes=self.lot_sizes,
            ipo_dates=self.ipo_dates,
            slippage_bps=self.slippage_bps,
            commission_config=self.commission,
            prev_bar=prev_bar,
            risk_price_deviation_limit=self.risk_price_deviation_limit,
        )

    def __init__(self, config: Dict[str, Any], event_bus: Optional[EventPublisher] = None,
                 lot_sizes: Optional[Dict[str, int]] = None,
                 ipo_dates: Optional[Dict[str, datetime]] = None,
                 portfolio_class=None, risk_engine_class=None, sub_portfolio_class=None):
        self.config = config
        self.event_bus = event_bus
        self.slippage_bps = config.get("backtest", {}).get("slippage_bps", 5)
        self.risk_price_deviation_limit = config.get("backtest", {}).get("risk_price_deviation_limit", 0.15)
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}
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
        symbols = symbols or []
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
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        trading_dates_set = None
        if data_provider and hasattr(data_provider, 'trading_dates'):
            trading_dates_set = data_provider.trading_dates

        current_date = start
        while current_date <= end:
            if trading_dates_set and datetime(current_date.year, current_date.month, current_date.day) not in trading_dates_set:
                current_date += timedelta(days=1)
                continue

            # --- Step 1: on_before_trading ---
            for strategy in strategies:
                if hasattr(strategy, "on_before_trading"):
                    strategy.on_before_trading(strategy.context, current_date.date())

            # --- Step 2: Load today's bar data ---
            prev_close_bars: "Dict[str, BacktestBar]" = dict(prev_bars)
            today_bars, any_suspended_today = self._load_daily_bars(
                data_provider, symbols, current_date, last_prices, prev_bars,
            )
            if any_suspended_today:
                diag.suspended_days += 1

            # --- Step 3: Process dividends ---
            if use_subs:
                for pf in portfolio_map.values():
                    process_dividends(data_provider, pf, symbols, current_date, last_prices, entry_times)
            else:
                process_dividends(data_provider, primary_portfolio, symbols, current_date, last_prices, entry_times)

            # --- Step 4: Execute deferred orders from T-1 ---
            for order in deferred_orders:
                sym = order['symbol']
                bar = today_bars.get(sym, {})
                if not bar or bar.get('_suspended'):
                    diag.discarded_orders += 1
                    continue
                order_strategy = order.get('strategy')
                if order_strategy and order_strategy not in portfolio_map:
                    logger.warning("Order strategy '%s' not in portfolio_map, discarding order for %s", order_strategy, sym)
                    diag.discarded_orders += 1
                    continue
                order_pf = portfolio_map.get(order_strategy, primary_portfolio)
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
                )
                if not trades:
                    diag.discarded_orders += 1
                    continue
                all_trades.extend(trades)
                target_sname = order.get('strategy')
                for s in strategies:
                    sname = getattr(s, 'name', s.__class__.__name__)
                    if hasattr(s, "on_fill") and (target_sname is None or sname == target_sname):
                        for t in trades:
                            s.on_fill(s.context, t)
            deferred_orders = []

            # --- Step 5: Feed bar data to strategies ---
            for sym, bar_data in today_bars.items():
                for strategy in strategies:
                    if hasattr(strategy, "on_data"):
                        strategy.on_data(strategy.context, bar_data)

            # --- Step 6: Update portfolio prices ---
            if use_subs:
                for pf in portfolio_map.values():
                    self._update_portfolio_prices(pf, last_prices)
            else:
                self._update_portfolio_prices(primary_portfolio, last_prices)

            # --- Step 7: Strategy signal generation ---
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "prepare_for_trading_day"):
                    strategy.context.prepare_for_trading_day(current_date.date(), last_prices)
                if hasattr(strategy, "on_after_trading"):
                    strategy.on_after_trading(strategy.context, current_date.date())

            # --- Step 8: Collect pending orders ---
            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "drain_orders"):
                    for order in strategy.context.drain_orders():
                        order['_signal_date'] = current_date
                        pending_orders.append(order)

            for order in pending_orders:
                deferred_orders.append(order)
            pending_orders = []

            # --- Step 9: Record NAV + reset daily state ---
            nav = calculate_daily_nav(portfolio_map, primary_portfolio, use_subs)
            equity_curve_dates.append(current_date)
            equity_curve_values.append(nav)

            self._reset_daily(portfolio_map, risk_map, use_subs, diag)

            current_date += timedelta(days=1)

        for strategy in strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

        equity_curve = pd.Series(equity_curve_values, index=equity_curve_dates)
        metrics = calculate_performance_metrics(equity_curve, all_trades, initial_cash)

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

    def _load_daily_bars(self, data_provider, symbols, current_date, last_prices, prev_bars):
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
                    bar_close = bar_data.get('close', 0)
                    if bar_close > 0:
                        last_prices[symbol] = bar_close
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
                            bar_close = bar_data.get('close', 0)
                            if bar_close > 0:
                                last_prices[symbol] = bar_close
                            prev_bars[symbol] = bar_data
            except Exception as e:
                logger.warning("Error loading bar data for %s on %s: %s", symbol, current_date, e)

        return today_bars, any_suspended

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
