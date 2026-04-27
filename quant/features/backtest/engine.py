"""Backtest engine with realistic execution: T+1 fills, suspension handling, lot sizes."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import pandas as pd
import numpy as np

from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.models.trade import Trade
from quant.features.backtest.analytics import calculate_performance_metrics, PerformanceMetrics

logger = logging.getLogger(__name__)

MAX_FILL_DEFER_DAYS = 5
VOLUME_PARTICIPATION_LIMIT = 0.05
DEFAULT_LOT_SIZE = 100

HK_COMMISSION_RATE = 0.0003
HK_STAMP_DUTY_RATE = 0.0013
HK_SFC_LEVY_RATE = 0.0000278
HK_CLEARING_RATE = 0.00002
HK_TRADING_FEE_RATE = 0.00005
HK_MIN_COMMISSION = 3.0
HK_TRADING_SYSTEM_FEE = 0.50

CN_COMMISSION_RATE = 0.00025
CN_STAMP_DUTY_RATE = 0.0005
CN_TRANSFER_FEE_RATE = 0.0000341
CN_REGULATOR_FEE_RATE = 0.0000541
CN_MIN_COMMISSION = 5.0

US_SEC_FEE_RATE = 0.000000278
US_FINRA_TAF_PER_SHARE = 0.000166

MARKET_CURRENCY = {"CN": "CNY", "HK": "HKD", "US": "USD"}

CN_DIVIDEND_TAX_SHORT_DAYS = 30
CN_DIVIDEND_TAX_MEDIUM_DAYS = 365


@dataclass
class BacktestDiagnostics:
    suspended_days: int = 0
    volume_limited_trades: int = 0
    lot_adjusted_trades: int = 0
    total_fill_delay_days: int = 0
    fill_count: int = 0
    total_commission: float = 0.0
    total_gross_pnl: float = 0.0
    t1_rejected_sells: int = 0
    limit_rejected_orders: int = 0

    @property
    def avg_fill_delay_days(self) -> float:
        return self.total_fill_delay_days / self.fill_count if self.fill_count > 0 else 0.0

    @property
    def cost_drag_pct(self) -> float:
        if self.total_gross_pnl == 0:
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
    metrics: PerformanceMetrics
    diagnostics: BacktestDiagnostics = field(default_factory=BacktestDiagnostics)
    open_positions: List[Dict] = field(default_factory=list)


@dataclass
class CommissionConfig:
    US: Dict[str, Any] = field(default_factory=lambda: {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0})
    HK: Dict[str, Any] = field(default_factory=lambda: {"type": "hk_realistic"})
    CN: Dict[str, Any] = field(default_factory=lambda: {"type": "cn_realistic"})


class Backtester:
    """Backtester with realistic execution."""

    IPO_NO_LIMIT_DAYS = 5

    def __init__(self, config: Dict[str, Any], event_bus: Optional[EventPublisher] = None,
                 lot_sizes: Optional[Dict[str, int]] = None,
                 ipo_dates: Optional[Dict[str, date]] = None):
        self.config = config
        self.event_bus = event_bus
        self.slippage_bps = config.get("backtest", {}).get("slippage_bps", 5)
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}

        commission_config = config.get("execution", {}).get("commission", {})
        self.commission = CommissionConfig(
            US=commission_config.get("US", {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}),
            HK=commission_config.get("HK", {"type": "hk_realistic"}),
            CN=commission_config.get("CN", {"type": "cn_realistic"})
        )

    def _get_lot_size(self, symbol: str) -> int:
        return self.lot_sizes.get(symbol, DEFAULT_LOT_SIZE)

    def _detect_currency(self, symbols: List[str]) -> str:
        if not symbols:
            return "USD"
        markets = {self._detect_market(s) for s in symbols}
        if len(markets) == 1:
            return MARKET_CURRENCY.get(markets.pop(), "USD")
        logger.warning("Mixed markets %s in backtest, using USD", markets)
        return "USD"

    def _is_cn_price_at_limit(self, symbol: str, open_price: float, prev_close: float,
                               current_date: Optional[date] = None) -> bool:
        if prev_close <= 0:
            return False
        if current_date and symbol in self.ipo_dates:
            ipo_d = self.ipo_dates[symbol]
            trading_days_since_ipo = (current_date - ipo_d).days
            if trading_days_since_ipo <= self.IPO_NO_LIMIT_DAYS:
                return False
        if symbol.startswith("8"):
            limit_pct = 0.30
        elif symbol.startswith("688") or symbol.startswith("300"):
            limit_pct = 0.20
        else:
            limit_pct = 0.10
        upper = round(prev_close * (1 + limit_pct), 2)
        lower = round(prev_close * (1 - limit_pct), 2)
        open_rounded = round(open_price, 2)
        return open_rounded >= upper or open_rounded <= lower

    @staticmethod
    def _earliest_lot_time(pos) -> Optional[datetime]:
        if not pos._lots:
            return None
        earliest = min(pos._lots.keys())
        return datetime(earliest.year, earliest.month, earliest.day)

    @staticmethod
    def _fifo_lot_slices(pos, sell_qty: float) -> List[tuple]:
        slices = []
        remaining = sell_qty
        for lot_date in sorted(pos._lots.keys()):
            if remaining <= 0:
                break
            lot = pos._lots[lot_date]
            take = min(lot.qty, remaining)
            slices.append((lot_date, take, lot.price))
            remaining -= take
        return slices

    @staticmethod
    def _is_suspended(bar: Dict, prev_bar: Optional[Dict]) -> bool:
        if bar.get("volume", 0) == 0:
            return True
        return False

    def run(
        self,
        start: datetime,
        end: datetime,
        strategies: List[Any],
        initial_cash: float = 100000,
        data_provider: Any = None,
        symbols: Optional[List[str]] = None,
    ) -> BacktestResult:
        """Run backtest with T+1 execution.

        Daily loop execution order (DO NOT REORDER — prevents look-ahead bias):
          1. Feed today's bars to strategies → strategies generate signals
          2. Fill deferred orders from YESTERDAY's signals at today's open price (T+1)
          3. Collect NEW orders generated today → defer for tomorrow
          4. Update portfolio prices, record NAV

        Because step 2 runs before step 3, orders are always filled one day
        after the signal, using the fill day's open price.
        """
        from quant.features.trading.portfolio import Portfolio
        from quant.features.trading.risk import RiskEngine

        symbols = symbols or []
        currency = self._detect_currency(symbols)
        portfolio = Portfolio(initial_cash=initial_cash, currency=currency)
        risk_engine = RiskEngine(self.config, portfolio, self.event_bus)
        diag = BacktestDiagnostics()

        equity_curve_dates: List[datetime] = []
        equity_curve_values: List[float] = []
        all_trades: List[Trade] = []

        entry_times: Dict[str, datetime] = {}
        entry_prices: Dict[str, float] = {}
        last_prices: Dict[str, float] = {}
        prev_bars: Dict[str, Dict] = {}

        deferred_orders: List[Dict] = []
        pending_orders: List[Dict] = []

        for strategy in strategies:
            strategy.context = self._create_context(portfolio, risk_engine, data_provider)
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        if data_provider and hasattr(data_provider, 'trading_dates'):
            trading_dates_set = data_provider.trading_dates
        else:
            trading_dates_set = None

        current_date = start
        while current_date <= end:
            if trading_dates_set and datetime(current_date.year, current_date.month, current_date.day) not in trading_dates_set:
                current_date += timedelta(days=1)
                continue

            today_bars: Dict[str, Dict] = {}
            any_suspended_today = False

            for strategy in strategies:
                if hasattr(strategy, "on_before_trading"):
                    strategy.on_before_trading(strategy.context, current_date.date())

            if data_provider:
                has_fast_lookup = hasattr(data_provider, 'get_bar_for_date')
                for symbol in symbols:
                    try:
                        if has_fast_lookup:
                            bar_data = data_provider.get_bar_for_date(symbol, current_date)
                            if bar_data is None:
                                continue
                            bar_data = dict(bar_data)
                            bar_data['symbol'] = symbol
                            bar_data['_suspended'] = self._is_suspended(bar_data, prev_bars.get(symbol))
                            today_bars[symbol] = bar_data

                            if bar_data['_suspended']:
                                any_suspended_today = True
                            last_prices[symbol] = bar_data.get('close', last_prices.get(symbol, 0))

                            for strategy in strategies:
                                if hasattr(strategy, "on_data"):
                                    strategy.on_data(strategy.context, bar_data)

                            prev_bars[symbol] = bar_data
                        else:
                            bars = data_provider.get_bars(
                                symbol,
                                current_date,
                                current_date + timedelta(days=1),
                                self.config.get("data", {}).get("default_timeframe", "1d")
                            )
                            if bars is not None and not bars.empty:
                                for _, bar in bars.iterrows():
                                    bar_data = bar.to_dict()
                                    bar_data['timestamp'] = bar_data.get('timestamp', bar.name if hasattr(bar, 'name') else current_date)
                                    bar_data['symbol'] = symbol
                                    bar_data['_suspended'] = self._is_suspended(bar_data, prev_bars.get(symbol))
                                    today_bars[symbol] = bar_data

                                    if bar_data['_suspended']:
                                        any_suspended_today = True
                                    last_prices[symbol] = bar_data.get('close', last_prices.get(symbol, 0))

                                    for strategy in strategies:
                                        if hasattr(strategy, "on_data"):
                                            strategy.on_data(strategy.context, bar_data)

                                    prev_bars[symbol] = bar_data
                    except Exception as e:
                        logger.warning("Error loading bar data for %s on %s: %s", symbol, current_date, e)

            if any_suspended_today:
                diag.suspended_days += 1

            self._process_dividends(data_provider, portfolio, symbols, current_date, last_prices, entry_times)

            for order in deferred_orders:
                order['_deferred_days'] = order.get('_deferred_days', 0) + 1

            fillable = []
            still_deferred = []
            for order in deferred_orders:
                sym = order['symbol']
                bar = today_bars.get(sym, {})
                if bar.get('_suspended'):
                    if order.get('_deferred_days', 0) >= MAX_FILL_DEFER_DAYS:
                        continue
                    still_deferred.append(order)
                else:
                    fillable.append(order)
            deferred_orders = still_deferred

            for order in fillable:
                sym = order['symbol']
                bar = today_bars.get(sym, {})
                if not bar:
                    if order.get('_deferred_days', 0) < MAX_FILL_DEFER_DAYS:
                        deferred_orders.append(order)
                    continue
                trades = self._execute_order(order, portfolio, sym, bar, entry_times, entry_prices, diag, prev_bars.get(sym))
                if trades is None:
                    if order.get('_deferred_days', 0) < MAX_FILL_DEFER_DAYS:
                        deferred_orders.append(order)
                    continue
                if trades:
                    all_trades.extend(trades)
                    for s in strategies:
                        if hasattr(s, "on_fill"):
                            for t in trades:
                                s.on_fill(s.context, t)

            for strategy in strategies:
                if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                    strategy.context.order_manager._current_date = current_date.date()
                if hasattr(strategy, "on_after_trading"):
                    strategy.on_after_trading(strategy.context, current_date.date())

            if data_provider:
                for strategy in strategies:
                    if hasattr(strategy, "context") and hasattr(strategy.context, "order_manager"):
                        om = strategy.context.order_manager
                        if om and hasattr(om, "_pending_orders") and om._pending_orders:
                            for order in om._pending_orders:
                                order['_signal_date'] = current_date
                                order['_deferred_days'] = 0
                                pending_orders.append(order)
                            om.clear_pending()

            for order in pending_orders:
                deferred_orders.append(order)
            pending_orders = []

            self._update_portfolio_prices(portfolio, last_prices)

            nav = portfolio.nav
            equity_curve_dates.append(current_date)
            equity_curve_values.append(nav)

            portfolio.reset_daily()
            risk_engine.reset_daily()

            current_date += timedelta(days=1)

        for strategy in strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

        equity_curve = pd.Series(equity_curve_values, index=equity_curve_dates)
        metrics = calculate_performance_metrics(equity_curve, all_trades)

        diag.total_gross_pnl = sum(t.pnl for t in all_trades) + diag.total_commission

        open_positions = []
        for sym, pos in portfolio.positions.items():
            if pos.quantity > 0:
                last_price = last_prices.get(sym, pos.avg_cost)
                open_positions.append({
                    "symbol": sym,
                    "quantity": pos.quantity,
                    "entry_price": pos.avg_cost,
                    "entry_time": self._earliest_lot_time(pos) or entry_times.get(sym),
                    "current_price": last_price,
                    "unrealized_pnl": (last_price - pos.avg_cost) * pos.quantity,
                    "market_value": pos.quantity * last_price,
                })

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

    def _process_dividends(
        self,
        data_provider: Any,
        portfolio: Any,
        symbols: List[str],
        current_date: datetime,
        last_prices: Dict[str, float],
        entry_times: Dict[str, datetime],
    ) -> None:
        if not data_provider or not hasattr(data_provider, 'get_dividend_for_date'):
            return
        for symbol in symbols:
            pos = portfolio.get_position(symbol)
            if not pos or pos.quantity <= 0:
                continue
            div = data_provider.get_dividend_for_date(symbol, current_date)
            if not div:
                continue
            market = self._detect_market(symbol)
            cash_div = float(div.get('cash_dividend', 0) or 0)
            stock_div = float(div.get('stock_dividend', 0) or 0)
            if cash_div > 0:
                payment = cash_div * pos.quantity
                tax = 0.0
                if market == "CN":
                    tax = self._calculate_cn_dividend_tax(pos, cash_div, current_date)
                portfolio.cash += payment - tax
                logger.info(f"{symbol} ex-div: cash {cash_div:.4f}/share x {pos.quantity} = {payment:.2f}, tax={tax:.2f}")
            if stock_div > 0:
                new_shares = stock_div * pos.quantity
                portfolio.update_position(
                    symbol, quantity=new_shares, price=last_prices.get(symbol, 0),
                    cost=0,
                )
                logger.info(f"{symbol} ex-div: stock {stock_div:.4f}/share x {pos.quantity} = {new_shares:.0f} new shares")

    def _calculate_cn_dividend_tax(self, pos: Any, cash_div: float, current_date: datetime) -> float:
        total_tax = 0.0
        today = current_date.date() if hasattr(current_date, 'date') else current_date
        for lot_date, lot in pos._lots.items():
            holding_days = (today - lot_date).days
            if holding_days <= CN_DIVIDEND_TAX_SHORT_DAYS:
                rate = 0.20
            elif holding_days <= CN_DIVIDEND_TAX_MEDIUM_DAYS:
                rate = 0.10
            else:
                rate = 0.0
            total_tax += cash_div * lot.qty * rate
        return total_tax

    def _update_portfolio_prices(self, portfolio: Any, last_prices: Dict[str, float]) -> None:
        for symbol, pos in portfolio.positions.items():
            if pos.quantity != 0 and symbol in last_prices:
                pos.update_market_price(last_prices[symbol])

    def _create_context(self, portfolio: Any, risk_engine: Any, data_provider: Any) -> Any:
        class BacktestContext:
            def __init__(self, portfolio, risk_engine, event_bus, data_provider):
                self.portfolio = portfolio
                self.risk_engine = risk_engine
                self.event_bus = event_bus
                self.data_provider = data_provider
                self.order_manager = BacktestOrderManager(risk_engine)
                self.broker = BacktestBroker()

        class BacktestOrderManager:
            def __init__(self, risk_engine):
                self._pending_orders: List[Dict] = []
                self._risk_engine = risk_engine
                self._current_date: Optional[date] = None

            def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
                if self._risk_engine and price and price > 0:
                    order_value = price * quantity
                    approved, _ = self._risk_engine.check_order(
                        symbol, quantity, price, order_value, side=side,
                        as_of_date=self._current_date,
                    )
                    if not approved:
                        return None
                    self._risk_engine.record_order(
                        symbol=symbol, order_value=order_value, as_of_date=self._current_date,
                    )
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

        return BacktestContext(
            portfolio=portfolio,
            risk_engine=risk_engine,
            event_bus=self.event_bus,
            data_provider=data_provider,
        )

    def _execute_order(
        self,
        order: Dict,
        portfolio: Any,
        symbol: str,
        bar: Dict,
        entry_times: Dict[str, datetime],
        entry_prices: Dict[str, float],
        diag: BacktestDiagnostics,
        prev_bar: Optional[Dict] = None,
    ) -> Optional[List[Trade]]:
        raw_open = bar.get('open')
        if not raw_open or raw_open <= 0:
            return []

        signal_date = order.get('_signal_date')
        fill_ts = bar.get('timestamp', datetime.now())
        if not isinstance(fill_ts, datetime):
            fill_ts = pd.Timestamp(fill_ts).to_pydatetime()

        market = self._detect_market(symbol)

        if market == "CN" and prev_bar:
            prev_close = prev_bar.get('close', 0)
            fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
            if self._is_cn_price_at_limit(symbol, raw_open, prev_close, fill_date_val):
                    diag.limit_rejected_orders += 1
                    return None

        fill_price = raw_open

        diag.total_fill_delay_days += order.get('_deferred_days', 0)
        diag.fill_count += 1

        slippage = fill_price * (self.slippage_bps / 10000)
        if order['side'] == 'BUY':
            fill_price += slippage
        else:
            fill_price -= slippage

        quantity = order['quantity']
        lot_size = self._get_lot_size(symbol)

        if market in ("HK", "CN"):
            if order['side'] == 'BUY':
                lot_qty = (int(quantity) // lot_size) * lot_size
                if lot_qty < lot_size:
                    return []
                if lot_qty != int(quantity):
                    diag.lot_adjusted_trades += 1
                quantity = float(lot_qty)
            else:
                if int(quantity) < 1:
                    return []

        bar_volume = bar.get('volume', 0)
        if bar_volume > 0 and quantity > bar_volume * VOLUME_PARTICIPATION_LIMIT:
            max_qty = int(bar_volume * VOLUME_PARTICIPATION_LIMIT)
            if order['side'] == 'BUY' and market in ("HK", "CN"):
                max_qty = (max_qty // lot_size) * lot_size
            if max_qty <= 0:
                return []
            quantity = float(max_qty)
            diag.volume_limited_trades += 1

        if order['side'] == 'BUY':
            cost_breakdown = self._calculate_commission_breakdown(fill_price, quantity, market, order['side'])
            commission = sum(cost_breakdown.values())

            total_cost = fill_price * quantity + commission
            if portfolio.cash < total_cost:
                return []

            diag.total_commission += commission

            entry_times[symbol] = fill_ts
            entry_prices[symbol] = fill_price

            fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
            portfolio.update_position(symbol, quantity=quantity, price=fill_price, cost=fill_price * quantity, trade_date=fill_date_val)
            portfolio.cash -= total_cost

            return [Trade(
                entry_time=fill_ts,
                exit_time=fill_ts,
                symbol=symbol,
                side=order['side'],
                entry_price=fill_price,
                exit_price=fill_price,
                quantity=quantity,
                pnl=-commission,
                realized_pnl=-commission,
                signal_date=signal_date,
                fill_date=fill_ts,
                fill_price=fill_price,
                intended_qty=order['quantity'],
                cost_breakdown=cost_breakdown,
            )]

        elif order['side'] == 'SELL':
            pos = portfolio.get_position(symbol)
            if not pos or pos.quantity <= 0:
                logger.warning(
                    "SELL rejected for %s: no position to sell (short not supported)", symbol
                )
                return []

            if market == "CN":
                fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
                settled_qty = pos.settled_quantity(fill_date_val)
                if settled_qty <= 0:
                    diag.t1_rejected_sells += 1
                    return []
                sell_qty = min(quantity, settled_qty)
            else:
                sell_qty = min(quantity, pos.quantity)

            cost_breakdown = self._calculate_commission_breakdown(fill_price, sell_qty, market, order['side'])
            commission = sum(cost_breakdown.values())

            diag.total_commission += commission

            lot_slices = self._fifo_lot_slices(pos, sell_qty)

            trades = []
            total_realized = 0.0
            for lot_date, sub_qty, lot_price in lot_slices:
                sub_ratio = sub_qty / sell_qty
                sub_commission = commission * sub_ratio
                sub_realized = (fill_price - lot_price) * sub_qty
                total_realized += sub_realized
                sub_cost_breakdown = {k: v * sub_ratio for k, v in cost_breakdown.items()}
                entry_time = datetime(lot_date.year, lot_date.month, lot_date.day)
                trades.append(Trade(
                    entry_time=entry_time,
                    exit_time=fill_ts,
                    symbol=symbol,
                    side=order['side'],
                    entry_price=lot_price,
                    exit_price=fill_price,
                    quantity=sub_qty,
                    pnl=sub_realized - sub_commission,
                    realized_pnl=sub_realized,
                    signal_date=signal_date,
                    fill_date=fill_ts,
                    fill_price=fill_price,
                    intended_qty=order['quantity'],
                    cost_breakdown=sub_cost_breakdown,
                ))

            portfolio.cash += fill_price * sell_qty - commission
            portfolio.update_position(symbol, quantity=-sell_qty, price=fill_price, cost=0, realized_pnl=total_realized)

            updated_pos = portfolio.get_position(symbol)
            if updated_pos is None or updated_pos.quantity <= 0:
                entry_times.pop(symbol, None)
                entry_prices.pop(symbol, None)

            return trades

        return []

    def _detect_market(self, symbol: str) -> str:
        if symbol.startswith("HK.") or (symbol.isdigit() and len(symbol) == 5):
            return "HK"
        if (
            symbol.isdigit()
            and len(symbol) == 6
            and symbol[0] in ("0", "3", "6", "8", "9")
        ):
            return "CN"
        return "US"

    def _calculate_commission_breakdown(self, price: float, quantity: float, market: str, side: str) -> Dict[str, float]:
        trade_value = price * quantity
        if market == "US":
            cfg = self.commission.US
            if cfg["type"] == "per_share":
                commission = quantity * cfg.get("per_share", 0.005)
                commission = max(commission, cfg.get("min_per_order", 1.0))
            else:
                commission = max(trade_value * cfg.get("percent", 0.001), cfg.get("min_per_order", 1.0))
            result: Dict[str, float] = {"commission": commission}
            if side == 'SELL':
                sec_fee = max(trade_value * US_SEC_FEE_RATE, 0.0)
                finra_taf = quantity * US_FINRA_TAF_PER_SHARE
                result["sec_fee"] = sec_fee
                result["finra_taf"] = finra_taf
            return result

        if market == "CN":
            commission = max(trade_value * CN_COMMISSION_RATE, CN_MIN_COMMISSION)
            stamp_duty = trade_value * CN_STAMP_DUTY_RATE if side == 'SELL' else 0.0
            transfer_fee = trade_value * CN_TRANSFER_FEE_RATE
            regulator_fee = trade_value * CN_REGULATOR_FEE_RATE
            return {
                "commission": commission,
                "stamp_duty": stamp_duty,
                "transfer_fee": transfer_fee,
                "regulator_fee": regulator_fee,
            }

        commission = max(trade_value * HK_COMMISSION_RATE, HK_MIN_COMMISSION)
        sfc_levy = trade_value * HK_SFC_LEVY_RATE
        clearing = trade_value * HK_CLEARING_RATE
        trading_fee = trade_value * HK_TRADING_FEE_RATE
        stamp_duty = trade_value * HK_STAMP_DUTY_RATE if side == 'SELL' else 0.0

        return {
            "commission": commission,
            "stamp_duty": stamp_duty,
            "sfc_levy": sfc_levy,
            "clearing": clearing,
            "trading_fee": trading_fee,
            "system_fee": HK_TRADING_SYSTEM_FEE,
        }


class BacktestResultExporter:
    """Export backtest results to CSV."""

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
