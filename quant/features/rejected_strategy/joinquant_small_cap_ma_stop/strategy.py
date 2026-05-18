"""JoinQuant small-cap selection with moving-average stop."""

from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


DEFAULT_SYMBOLS: List[str] = []
MARKET_CAP_FIELDS = (
    "market_cap",
    "total_market_cap",
    "total_mv",
    "circ_mv",
    "float_market_cap",
    "circulating_market_cap",
)


@strategy("joinquant_small_cap_ma_stop")
class JoinquantSmallCapMaStopStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        short_window: int = 10,
        long_window: int = 50,
        max_positions: int = 20,
        max_position_pct: float = 1.0,
        holding_days: int = 1,
        market_cap_fields: Optional[List[str]] = None,
        static_market_caps: Optional[Dict[str, float]] = None,
        exclude_st: bool = False,
        lot_size: int = 100,
    ):
        if short_window <= 0 or long_window <= 0:
            raise ValueError("short_window and long_window must be positive")
        if short_window >= long_window:
            raise ValueError("short_window must be smaller than long_window")
        if max_positions <= 0:
            raise ValueError("max_positions must be positive")
        self._symbols = symbols or DEFAULT_SYMBOLS
        self.short_window = int(short_window)
        self.long_window = int(long_window)
        self.max_positions = int(max_positions)
        self.max_position_pct = float(max_position_pct)
        self.market_cap_fields = tuple(market_cap_fields or MARKET_CAP_FIELDS)
        self.static_market_caps = dict(static_market_caps or {})
        self.exclude_st = bool(exclude_st)
        self.lot_size = max(1, int(lot_size))
        super().__init__("joinquant_small_cap_ma_stop", self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return self.long_window + 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("JoinquantSmallCapMaStopStrategy")

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        stopped = self._apply_ma_stop()
        candidates = self._rank_small_cap_candidates(stopped)
        if not candidates:
            return

        selected = candidates[: min(self.max_positions, len(candidates))]
        selected_symbols = {symbol for _, symbol, _ in selected}
        self._sell_unselected(selected_symbols, stopped)

        slots = len(selected)
        for _, symbol, price in selected:
            target_qty = self._target_quantity(context, price, slots)
            current_pos = self._positions.get(symbol, 0.0)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _apply_ma_stop(self) -> set[str]:
        stopped = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            if not self._is_crossunder(symbol):
                continue
            price = self._get_last_price(symbol)
            if price > 0:
                self.sell(symbol, int(quantity), "MARKET", price)
                stopped.add(symbol)
        return stopped

    def _rank_small_cap_candidates(self, stopped: set[str]) -> List[tuple[float, str, float]]:
        candidates: List[tuple[float, str, float]] = []
        for symbol in self._symbols:
            if symbol in stopped:
                continue
            bar = self._get_last_bar(symbol)
            if not self._is_eligible_bar(bar):
                continue
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            market_cap = self._market_cap(symbol, bar)
            if market_cap is None:
                continue
            candidates.append((market_cap, symbol, price))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def _sell_unselected(self, selected_symbols: set[str], stopped: set[str]) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0 or symbol in selected_symbols or symbol in stopped:
                continue
            price = self._get_last_price(symbol)
            if price > 0:
                self.sell(symbol, int(quantity), "MARKET", price)

    def _target_quantity(self, context: "Context", price: float, slots: int) -> int:
        portfolio = getattr(context, "portfolio", None)
        nav = float(getattr(portfolio, "nav", 0.0) or 0.0)
        if nav <= 0 or price <= 0 or slots <= 0:
            return 0
        raw_qty = int((nav * self.max_position_pct / slots) / price)
        return (raw_qty // self.lot_size) * self.lot_size

    def _is_crossunder(self, symbol: str) -> bool:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.long_window + 1:
            return False
        closes = np.asarray([self._adj(bar, "close") for bar in bars[-self.long_window - 1:]], dtype=float)
        if closes.size < self.long_window + 1 or not np.isfinite(closes).all():
            return False
        short_now = float(np.mean(closes[-self.short_window:]))
        long_now = float(np.mean(closes[-self.long_window:]))
        short_prev = float(np.mean(closes[-self.short_window - 1:-1]))
        long_prev = float(np.mean(closes[-self.long_window - 1:-1]))
        return short_now < long_now and short_prev >= long_prev

    def _is_eligible_bar(self, bar: Optional[Dict]) -> bool:
        if not bar:
            return False
        if self.exclude_st and self._truthy(self._bar_value(bar, "is_st", False)):
            return False
        if self._truthy(self._bar_value(bar, "_suspended", False)):
            return False
        tradable = self._bar_value(bar, "tradable", True)
        if self._falsey(tradable):
            return False
        has_daily_bar = self._bar_value(bar, "has_daily_bar", True)
        if self._falsey(has_daily_bar):
            return False
        price = self._bar_value(bar, "close", 0.0)
        return self._positive_finite(price)

    def _market_cap(self, symbol: str, bar: Optional[Dict]) -> Optional[float]:
        if bar:
            for field in self.market_cap_fields:
                value = self._bar_value(bar, field, None)
                if self._positive_finite(value):
                    return float(value)
        value = self.static_market_caps.get(symbol)
        if self._positive_finite(value):
            return float(value)
        return None

    @staticmethod
    def _bar_value(bar: Any, field: str, default: Any = None) -> Any:
        if isinstance(bar, dict):
            return bar.get(field, default)
        return getattr(bar, field, default)

    @staticmethod
    def _positive_finite(value: Any) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return bool(np.isfinite(number) and number > 0)

    @staticmethod
    def _truthy(value: Any) -> bool:
        try:
            return bool(value)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _falsey(value: Any) -> bool:
        try:
            return not bool(value)
        except (TypeError, ValueError):
            return False

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "max_positions": self.max_positions,
            "max_position_pct": self.max_position_pct,
            "market_cap_fields": list(self.market_cap_fields),
            "exclude_st": self.exclude_st,
            "lot_size": self.lot_size,
        }
