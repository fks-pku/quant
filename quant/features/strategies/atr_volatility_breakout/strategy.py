"""ATR Volatility Breakout Strategy - Range expansion breakout with trailing stop.

Entry: Buy when today's close - today's open > ATR * threshold (bullish breakout).
Exit: Trailing stop at entry - N * ATR, or max holding period.

The strategy captures momentum from volatility expansion. When price moves
significantly more than recent average range, it suggests a new trend starting.

Weekly check cycle to reduce noise from daily fluctuations.

Hypothesis: Volatility breakouts precede trend continuation. A large
intraday move relative to ATR signals institutional flow or news catalyst
that tends to persist over the next several days.

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.features.trading.engine import Context


@strategy("ATRVolatilityBreakout")
class ATRVolatilityBreakout(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        atr_period: int = 14,
        breakout_mult: float = 1.5,
        stop_atr_mult: float = 2.0,
        max_holding_days: int = 10,
        check_interval_days: int = 5,
        max_position_pct: float = 0.08,
    ):
        super().__init__("ATRVolatilityBreakout")
        self._symbols = symbols or ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "SPY", "QQQ"]
        self.atr_period = atr_period
        self.breakout_mult = breakout_mult
        self.stop_atr_mult = stop_atr_mult
        self.max_holding_days = max_holding_days
        self.check_interval_days = check_interval_days
        self.max_position_pct = max_position_pct

        self._day_data: Dict[str, List] = {}
        self._entry_dates: Dict[str, date] = {}
        self._stop_prices: Dict[str, float] = {}
        self._last_check_date: Optional[date] = None
        self._days_since_check: int = 0

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("ATRVolatilityBreakout")
        self.logger.info(
            f"ATRVolatilityBreakout starting with ATR({self.atr_period}), "
            f"breakout_mult={self.breakout_mult}"
        )

    def _get_bars(self, symbol: str) -> List[dict]:
        return self._day_data.get(symbol, [])

    def _get_last_price(self, symbol: str) -> float:
        bars = self._get_bars(symbol)
        if not bars:
            return 0.0
        last = bars[-1]
        if isinstance(last, dict):
            return float(last.get("close", 0))
        return float(getattr(last, "close", 0))

    def _get_bar_fields(self, bar: Any, field: str) -> float:
        if isinstance(bar, dict):
            return float(bar.get(field, 0))
        return float(getattr(bar, field, 0))

    def _calculate_atr(self, symbol: str) -> float:
        bars = self._get_bars(symbol)
        if len(bars) < self.atr_period + 1:
            return 0.0
        trs = []
        for i in range(-self.atr_period, 0):
            h = self._get_bar_fields(bars[i], "high")
            l = self._get_bar_fields(bars[i], "low")
            pc = self._get_bar_fields(bars[i - 1], "close")
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def _check_breakout(self, symbol: str) -> bool:
        bars = self._get_bars(symbol)
        if len(bars) < self.atr_period + 1:
            return False

        last_bar = bars[-1]
        open_price = self._get_bar_fields(last_bar, "open")
        close_price = self._get_bar_fields(last_bar, "close")

        if open_price <= 0:
            return False

        atr = self._calculate_atr(symbol)
        if atr <= 0:
            return False

        intraday_move = close_price - open_price
        return intraday_move > atr * self.breakout_mult

    def _update_trailing_stop(self, symbol: str) -> None:
        price = self._get_last_price(symbol)
        atr = self._calculate_atr(symbol)
        if price <= 0 or atr <= 0:
            return

        new_stop = price - self.stop_atr_mult * atr
        current_stop = self._stop_prices.get(symbol, 0)
        self._stop_prices[symbol] = max(current_stop, new_stop)

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
        else:
            return

        if not symbol or symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []
        self._day_data[symbol].append(data)

        max_keep = (self.atr_period + 1) * 3
        if len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep:]

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        nav = context.portfolio.nav

        for symbol in list(self._positions.keys()):
            pos_qty = self._positions.get(symbol, 0)
            if pos_qty <= 0:
                continue

            self._update_trailing_stop(symbol)

            price = self._get_last_price(symbol)
            stop = self._stop_prices.get(symbol, 0)
            entry_date = self._entry_dates.get(symbol)
            days_held = (trading_date - entry_date).days if entry_date else 0

            should_exit = False
            exit_reason = ""

            if stop > 0 and price <= stop:
                should_exit = True
                exit_reason = f"stop hit at {stop:.2f}"
            elif days_held >= self.max_holding_days:
                should_exit = True
                exit_reason = f"max holding {days_held}d"

            if should_exit:
                self.sell(symbol, pos_qty)
                self._entry_dates.pop(symbol, None)
                self._stop_prices.pop(symbol, None)
                self.logger.info(f"ATRVolatilityBreakout exit {symbol}: {exit_reason}")

        self._days_since_check += 1
        if self._days_since_check < self.check_interval_days:
            return
        self._days_since_check = 0

        for symbol in self._symbols:
            if self._positions.get(symbol, 0) > 0:
                continue
            if not self._check_breakout(symbol):
                continue

            price = self._get_last_price(symbol)
            if price <= 0:
                continue

            atr = self._calculate_atr(symbol)
            stop = price - self.stop_atr_mult * atr

            qty = int((nav * self.max_position_pct) / price)
            if qty > 0:
                self.buy(symbol, qty)
                self._entry_dates[symbol] = trading_date
                self._stop_prices[symbol] = stop
                self.logger.info(
                    f"ATRVolatilityBreakout entry {symbol} at ~{price:.2f}, stop={stop:.2f}"
                )

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._day_data.clear()
        self._entry_dates.clear()
        self._stop_prices.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry_dates": {k: str(v) for k, v in self._entry_dates.items()},
            "stop_prices": self._stop_prices,
            "parameters": {
                "atr_period": self.atr_period,
                "breakout_mult": self.breakout_mult,
                "stop_atr_mult": self.stop_atr_mult,
                "max_holding_days": self.max_holding_days,
                "check_interval_days": self.check_interval_days,
            },
        }
