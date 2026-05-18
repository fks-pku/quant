"""JoinQuant Small Cap MA Stop.

Source: https://www.joinquant.com/view/community/detail/cc21565a660487b31666dc40a6aa9ecd?type=1
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy

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


@strategy("joinquant_small_cap_ma_stop")
class JoinquantSmallCapMaStopStrategy(Strategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        short_window: int = 10,
        long_window: int = 50,
        max_positions: int = 20,
        lot_size: int = 100,
        market_cap_fields: Optional[List[str]] = None,
    ):
        super().__init__("joinquant_small_cap_ma_stop")
        self._symbols = [str(symbol) for symbol in symbols] if symbols else []
        self.short_window = int(short_window)
        self.long_window = int(long_window)
        self.max_positions = int(max_positions)
        self.lot_size = int(lot_size)
        self.market_cap_fields = tuple(market_cap_fields or MARKET_CAP_FIELDS)
        self._bars: Dict[str, List[Any]] = {}
        self._last_price: Dict[str, float] = {}
        self._last_market_cap: Dict[str, float] = {}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = self._value(data, "symbol", "")
        if not symbol or (self._symbols and symbol not in self._symbols):
            return
        close = self._price(data)
        if close <= 0:
            return
        self._last_price[symbol] = close
        market_cap = self._market_cap(data)
        if market_cap is not None and market_cap > 0:
            self._last_market_cap[symbol] = market_cap
        self._bars.setdefault(symbol, []).append(data)
        max_history = max(self.long_window + 2, 8)
        if len(self._bars[symbol]) > max_history:
            self._bars[symbol] = self._bars[symbol][-max_history:]

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and self._ma_crossunder(symbol):
                self.sell(symbol, quantity, "MARKET", self._last_price.get(symbol))

        held = {symbol for symbol, quantity in self._positions.items() if quantity > 0}
        slots = max(0, self.max_positions - len(held))
        if slots <= 0:
            return
        candidates = [
            (symbol, market_cap)
            for symbol, market_cap in self._last_market_cap.items()
            if symbol not in held and self._last_price.get(symbol, 0.0) > 0
        ]
        if not candidates:
            return
        selected = [symbol for symbol, _ in sorted(candidates, key=lambda item: (item[1], item[0]))[:slots]]
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_value = nav / float(max(1, self.max_positions))
        for symbol in selected:
            price = self._last_price.get(symbol, 0.0)
            quantity = self._round_lot(target_value / price) if price > 0 else 0
            if quantity > 0:
                self.buy(symbol, quantity, "MARKET", price)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                self.sell(symbol, quantity, "MARKET", self._last_price.get(symbol))
        self._bars.clear()
        self._last_price.clear()
        self._last_market_cap.clear()
        super().on_stop(context)

    def _ma_crossunder(self, symbol: str) -> bool:
        bars = self._bars.get(symbol, [])
        if len(bars) < self.long_window + 1:
            return False
        closes = [self._adj(bar, "close") for bar in bars]
        prev_short = sum(closes[-self.short_window - 1:-1]) / float(self.short_window)
        prev_long = sum(closes[-self.long_window - 1:-1]) / float(self.long_window)
        curr_short = sum(closes[-self.short_window:]) / float(self.short_window)
        curr_long = sum(closes[-self.long_window:]) / float(self.long_window)
        return prev_short >= prev_long and curr_short < curr_long

    def _market_cap(self, data: Any) -> Optional[float]:
        for field in self.market_cap_fields:
            value = self._value(data, field, None)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric == numeric and numeric > 0:
                return numeric
        return None

    def _round_lot(self, quantity: float) -> int:
        lot = max(1, self.lot_size)
        return int(quantity // lot) * lot

    @staticmethod
    def _value(data: Any, field: str, default: Any = None) -> Any:
        if isinstance(data, dict):
            return data.get(field, default)
        return getattr(data, field, default)
