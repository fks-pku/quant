"""Shared A-share small-cap rotation logic."""

from datetime import date
import math
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.features.strategies.base import Strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


MARKET_CAP_FIELDS = (
    "market_cap",
    "total_market_cap",
    "total_mv",
    "circ_mv",
    "float_market_cap",
    "circulating_market_cap",
)


class AShareSmallCapRotationBase(Strategy):
    def __init__(
        self,
        name: str,
        symbols: Optional[List[str]] = None,
        max_positions: int = 20,
        rebalance_interval: int = 10,
        min_price: float = 5.0,
        min_adv_value: float = 20000.0,
        lot_size: int = 100,
    ):
        super().__init__(name)
        self._symbols = [str(symbol) for symbol in symbols] if symbols else []
        self.max_positions = max(1, int(max_positions))
        self.rebalance_interval = max(1, int(rebalance_interval))
        self.min_price = float(min_price)
        self.min_adv_value = float(min_adv_value)
        self.lot_size = max(1, int(lot_size))
        self._bars: Dict[str, List[Any]] = {}
        self._last_price: Dict[str, float] = {}
        self._rebalance_counter = 0

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = str(self._value(data, "symbol", "") or "")
        if not symbol or (self._symbols and symbol not in self._symbols):
            return
        price = self._price(data)
        if price > 0:
            self._last_price[symbol] = price
        self._bars.setdefault(symbol, []).append(data)
        if len(self._bars[symbol]) > 90:
            self._bars[symbol] = self._bars[symbol][-90:]

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        exited = self._exit_risk_positions()
        try_rebalance = self._rebalance_counter % self.rebalance_interval == 0
        self._rebalance_counter += 1
        if not try_rebalance:
            return

        candidates = []
        for symbol, bars in self._bars.items():
            if not bars or symbol in exited:
                continue
            bar = bars[-1]
            if self._entry_risk(symbol, bar):
                continue
            candidates.append((self._candidate_score(symbol, bar), symbol))
        if not candidates:
            return

        selected = [
            symbol
            for _, symbol in sorted(candidates, key=lambda item: (-item[0], item[1]))[: self.max_positions]
        ]
        selected_set = set(selected)
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._last_price.get(symbol, 0.0)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_value = nav / float(self.max_positions)
        for symbol in selected:
            price = self._last_price.get(symbol, 0.0)
            if price <= 0:
                continue
            target_quantity = self._round_lot(target_value / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._last_price.get(symbol, 0.0)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
        self._bars.clear()
        self._last_price.clear()
        super().on_stop(context)

    def _candidate_score(self, symbol: str, bar: Any) -> float:
        market_cap = self._market_cap(bar)
        return -market_cap

    def _exit_risk_positions(self) -> set:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bars = self._bars.get(symbol, [])
            if not bars or not self._exit_risk(symbol, bars[-1]):
                continue
            price = self._last_price.get(symbol, 0.0)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if not self._is_mainland_a_symbol(symbol):
            return True
        if self._bool_value(self._value(bar, "is_st", False), False):
            return True
        if self._bool_value(self._value(bar, "_suspended", False), False):
            return True
        if self._bool_value(self._value(bar, "status_is_suspended", False), False):
            return True
        if self._bool_value(self._value(bar, "tradable", True), True) is False:
            return True
        if self._bool_value(self._value(bar, "has_daily_bar", True), True) is False:
            return True
        if self._bool_value(self._value(bar, "is_listed", True), True) is False:
            return True
        list_status = str(self._value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            return True
        if self._price(bar) < self.min_price:
            return True
        if self._market_cap(bar) <= 0:
            return True
        return self._average_turnover(symbol) < self.min_adv_value

    def _exit_risk(self, symbol: str, bar: Any) -> bool:
        if self._entry_risk(symbol, bar):
            return True
        price = self._price(bar)
        return price > 0 and price < self.min_price

    def _average_turnover(self, symbol: str) -> float:
        bars = self._bars.get(symbol, [])[-20:]
        values = [self._bar_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if not values:
            return 0.0
        return sum(values) / float(len(values))

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._float_value(self._value(bar, "turnover", None), 0.0)
        if turnover > 0:
            return turnover
        return self._price(bar) * self._float_value(self._value(bar, "volume", 0.0), 0.0)

    def _market_cap(self, bar: Any) -> float:
        for field in MARKET_CAP_FIELDS:
            value = self._float_value(self._value(bar, field, None), 0.0)
            if value > 0:
                return value
        return 0.0

    def _return(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) <= lookback:
            return None
        current = self._adj(bars[-1], "close")
        base = self._adj(bars[-lookback - 1], "close")
        if current <= 0 or base <= 0:
            return None
        return current / base - 1.0

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parameters": {
                "max_positions": self.max_positions,
                "rebalance_interval": self.rebalance_interval,
                "min_price": self.min_price,
                "min_adv_value": self.min_adv_value,
                "lot_size": self.lot_size,
            },
        }

    @staticmethod
    def _is_mainland_a_symbol(symbol: str) -> bool:
        text = str(symbol)
        if len(text) != 6 or not text.isdigit():
            return False
        return text.startswith(("0", "2", "3", "6")) and not text.startswith("200")

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
