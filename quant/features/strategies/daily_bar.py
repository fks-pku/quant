"""DailyBarStrategy — reusable base for daily-bar strategies.

Eliminates ~800 lines of boilerplate across 12 strategies by providing:
  - on_data: symbol extraction + bar buffering + trim
  - _get_closes / _get_last_price / _get_highs / _get_lows
  - on_stop: position liquidation + cleanup hook
  - on_after_trading: rebalance gating
  - get_state: serialization skeleton
"""

from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from quant.domain.exceptions import OrderRejectedError
from quant.features.strategies.base import Strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


class DailyBarStrategy(Strategy):
    """Base for strategies that accumulate daily bars and rebalance on a schedule.

    Subclasses:
      - Set self._symbols in __init__
      - Override _execute_rebalance() for trading logic
      - Override _max_keep_hint for bar trimming (default: 126 bars)
      - Override _on_stop_cleanup() to clear custom state
      - Override _get_parameters() / _get_state_fields() for serialization
    """

    def __init__(self, name: str, symbols: List[str], holding_days: int = 1):
        super().__init__(name)
        self._symbols = symbols
        self._day_data: Dict[str, List[Any]] = {}
        self.holding_days = holding_days

        # rebalance gating
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    # ── Data buffering ──────────────────────────────────────────────

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = data.get("symbol", "") if isinstance(data, dict) else getattr(data, "symbol", "")
        if not symbol or symbol not in self._symbols:
            return
        if symbol not in self._day_data:
            self._day_data[symbol] = []
        self._day_data[symbol].append(data)
        max_keep = self._max_keep_hint
        if max_keep > 0 and len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep:]

    @property
    def _max_keep_hint(self) -> int:
        """Override to set the bar trim window. Return 0 for no trim (unbounded)."""
        return 126

    # ── Price helpers ───────────────────────────────────────────────

    def _get_closes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [self._adj(b, "close") for b in bars]

    def _get_highs(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [self._adj(b, "high") for b in bars]

    def _get_lows(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [self._adj(b, "low") for b in bars]

    def _get_last_price(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        return self._price(bars[-1]) if bars else 0.0

    def _get_last_bar(self, symbol: str) -> Optional[Dict]:
        bars = self._day_data.get(symbol, [])
        return bars[-1] if bars else None

    # ── Rebalance gating ────────────────────────────────────────────

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        if not self._check_rebalance_gate(trading_date):
            return
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def _check_rebalance_gate(self, trading_date: date) -> bool:
        if self._last_rebalance_date is not None:
            self._days_since_rebalance += 1
            if self._days_since_rebalance < self.holding_days:
                return False
        return True

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        """Override to implement strategy-specific rebalance logic."""
        pass

    # ── Lifecycle ───────────────────────────────────────────────────

    def on_stop(self, context: "Context") -> None:
        self._liquidate_positions(context)
        self._day_data.clear()
        self._on_stop_cleanup()

    def _liquidate_positions(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                try:
                    price = self._get_last_price(symbol)
                    self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
                except OrderRejectedError:
                    pass

    def _on_stop_cleanup(self) -> None:
        """Override to clear strategy-specific state collections."""
        pass

    # ── Serialization ───────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            **_get_state_extra(self),
            "parameters": self._get_parameters(),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        """Override to return {param_name: value} for serialization."""
        return {}

    def _get_state_fields(self) -> Dict[str, Any]:
        """Override to return extra state fields for serialization."""
        return {}


def _get_state_extra(strat: "DailyBarStrategy") -> Dict[str, Any]:
    """Internal: extract extra state fields without breaking get_state override."""
    return strat._get_state_fields()
