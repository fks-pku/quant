STRATEGY_TEMPLATE = '''from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("{strategy_name}")
class {class_name}(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        {param_declarations}
    ):
        super().__init__("{strategy_name}")
        self._symbols = symbols or {default_symbols}
        {param_assignments}
        self._day_data: Dict[str, List] = {{}}
        self._positions: Dict[str, float] = {{}}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("{strategy_name}")

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            close = data.get("close")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
            close = getattr(data, "close", None)
        else:
            return

        if not symbol or not close:
            return

        if symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []

        self._day_data[symbol].append(data)
        {data_accumulation}

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        {trading_logic}

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                self.sell(symbol, quantity)
        self._day_data.clear()
'''

CONFIG_TEMPLATE = '''strategy:
  name: {strategy_name}
  enabled: true
  priority: 3

parameters:
  symbols: {symbols}
{param_lines}
'''
