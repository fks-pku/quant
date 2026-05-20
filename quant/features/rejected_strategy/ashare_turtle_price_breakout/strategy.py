"""A-share Turtle price breakout strategy."""

from datetime import date
import math
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("ashare_turtle_price_breakout")
class AShareTurtlePriceBreakoutStrategy(Strategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        entry_lookback: int = 20,
        exit_lookback: int = 10,
        atr_window: int = 20,
        atr_stop_multiplier: float = 2.0,
        max_positions: int = 20,
        min_price: float = 10.0,
        lot_size: int = 100,
        min_turnover: float = 0.0,
    ):
        super().__init__("ashare_turtle_price_breakout")
        self._symbols = [str(symbol) for symbol in symbols] if symbols else []
        self.entry_lookback = max(2, int(entry_lookback))
        self.exit_lookback = max(2, int(exit_lookback))
        self.atr_window = max(2, int(atr_window))
        self.atr_stop_multiplier = float(atr_stop_multiplier)
        self.max_positions = max(1, int(max_positions))
        self.min_price = float(min_price)
        self.lot_size = max(1, int(lot_size))
        self.min_turnover = float(min_turnover)
        self._bars: Dict[str, List[Any]] = {}
        self._last_price: Dict[str, float] = {}
        self._pending_entry_state: Dict[str, Dict[str, float]] = {}
        self._entry_state: Dict[str, Dict[str, float]] = {}
        self._candidate_scores: Dict[str, float] = {}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        self._candidate_scores.clear()

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = str(self._value(data, "symbol", "") or "")
        if not symbol or (self._symbols and symbol not in self._symbols):
            return
        close = self._price(data)
        if close <= 0:
            return
        self._last_price[symbol] = close
        if self._is_suspended(data) or not self._has_daily_bar(data):
            return
        self._bars.setdefault(symbol, []).append(data)
        max_history = max(self.entry_lookback, self.exit_lookback, self.atr_window) + 2
        if len(self._bars[symbol]) > max_history:
            self._bars[symbol] = self._bars[symbol][-max_history:]
        score = self._breakout_score(symbol) if self._eligible_for_new_position(data) else None
        if score is None:
            self._candidate_scores.pop(symbol, None)
        else:
            self._candidate_scores[symbol] = score

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        symbol = str(getattr(fill, "symbol", "") or "")
        side = str(getattr(fill, "side", "") or "").upper()
        if not symbol:
            return
        if side == "BUY":
            state = self._pending_entry_state.pop(symbol, None) or self._current_entry_state(symbol)
            if state:
                self._entry_state[symbol] = state
        elif side == "SELL" and self._positions.get(symbol, 0) <= 0:
            self._entry_state.pop(symbol, None)
            self._pending_entry_state.pop(symbol, None)

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and self._must_exit(symbol):
                price = self._last_price.get(symbol, 0.0)
                if price > 0:
                    self.sell(symbol, quantity, "MARKET", price)

        held = {symbol for symbol, quantity in self._positions.items() if quantity > 0}
        slots = max(0, self.max_positions - len(held))
        if slots <= 0:
            return
        candidates = [(score, symbol) for symbol, score in self._candidate_scores.items() if symbol not in held]
        if not candidates:
            return

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_value = nav / float(self.max_positions)
        for _, symbol in sorted(candidates, key=lambda item: (-item[0], item[1]))[:slots]:
            price = self._last_price.get(symbol, 0.0)
            quantity = self._round_lot(target_value / price) if price > 0 else 0
            if quantity <= 0:
                continue
            state = self._current_entry_state(symbol)
            if state:
                self._pending_entry_state[symbol] = state
            self.buy(symbol, quantity, "MARKET", price)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                self.sell(symbol, quantity, "MARKET", self._last_price.get(symbol))
        self._bars.clear()
        self._last_price.clear()
        self._pending_entry_state.clear()
        self._entry_state.clear()
        self._candidate_scores.clear()
        super().on_stop(context)

    def _eligible_for_new_position(self, bar: Any) -> bool:
        if self._is_suspended(bar):
            return False
        if self._bool_value(self._value(bar, "tradable", True), True) is False:
            return False
        if self._bool_value(self._value(bar, "is_st", False), False):
            return False
        if self._bool_value(self._value(bar, "is_listed", True), True) is False:
            return False
        list_status = str(self._value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            return False
        if self._price(bar) <= self.min_price:
            return False
        volume = self._float_value(self._value(bar, "volume", 0.0), 0.0)
        if volume <= 0:
            return False
        turnover = self._float_value(self._value(bar, "turnover", 0.0), 0.0)
        return turnover >= self.min_turnover

    def _must_exit(self, symbol: str) -> bool:
        bars = self._bars.get(symbol, [])
        if not bars:
            return False
        bar = bars[-1]
        if self._is_suspended(bar):
            return False
        if self._price(bar) <= self.min_price:
            return True
        if self._bool_value(self._value(bar, "is_st", False), False):
            return True
        if self._bool_value(self._value(bar, "is_listed", True), True) is False:
            return True
        list_status = str(self._value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            return True
        if len(bars) >= self.exit_lookback + 1:
            current_low = self._adj(bars[-1], "low")
            prior_low = min(self._adj(item, "low") for item in bars[-self.exit_lookback - 1:-1])
            if current_low <= prior_low:
                return True
        state = self._entry_state.get(symbol)
        if state:
            stop = state["entry_adj_close"] - self.atr_stop_multiplier * state["atr"]
            if self._adj(bars[-1], "low") <= stop:
                return True
        return False

    def _breakout_score(self, symbol: str) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) < self.entry_lookback + 1:
            return None
        current_high = self._adj(bars[-1], "high")
        prior_high = max(self._adj(item, "high") for item in bars[-self.entry_lookback - 1:-1])
        if prior_high <= 0 or current_high <= prior_high:
            return None
        atr = self._atr(symbol) or max(current_high * 0.01, 0.01)
        return (current_high - prior_high) / max(atr, 1e-9)

    def _current_entry_state(self, symbol: str) -> Dict[str, float]:
        bars = self._bars.get(symbol, [])
        if not bars:
            return {}
        atr = self._atr(symbol)
        if atr is None or atr <= 0:
            return {}
        return {"entry_adj_close": self._adj(bars[-1], "close"), "atr": atr}

    def _atr(self, symbol: str) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) < 2:
            return None
        ranges = []
        start = max(1, len(bars) - self.atr_window)
        for index in range(start, len(bars)):
            high = self._adj(bars[index], "high")
            low = self._adj(bars[index], "low")
            prev_close = self._adj(bars[index - 1], "close")
            if high <= 0 or low <= 0 or prev_close <= 0:
                continue
            ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if not ranges:
            return None
        return sum(ranges) / float(len(ranges))

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _has_daily_bar(self, data: Any) -> bool:
        return self._bool_value(self._value(data, "has_daily_bar", True), True)

    def _is_suspended(self, data: Any) -> bool:
        if self._bool_value(self._value(data, "_suspended", False), False):
            return True
        if self._bool_value(self._value(data, "status_is_suspended", False), False):
            return True
        return self._bool_value(self._value(data, "tradable", True), True) is False

    @staticmethod
    def _value(data: Any, field: str, default: Any = None) -> Any:
        if isinstance(data, dict):
            return data.get(field, default)
        return getattr(data, field, default)

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _bool_value(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"", "nan", "none", "null"}:
                return default
            if text in {"0", "false", "f", "no", "n"}:
                return False
            if text in {"1", "true", "t", "yes", "y"}:
                return True
        try:
            if value != value:
                return default
        except Exception:
            return default
        return bool(value)
