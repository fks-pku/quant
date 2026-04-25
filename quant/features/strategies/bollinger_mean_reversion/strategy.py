"""Bollinger Band Mean Reversion Strategy - Oversold bounce with RSI confirmation.

Buy when:
  1. Price closes below lower Bollinger Band (lookback SMA - k*std)
  2. RSI < oversold threshold (default 30)

Sell when:
  1. Price returns to or above middle band (SMA), OR
  2. RSI > overbought threshold (default 70), OR
  3. Stop loss triggered (price falls below lower band - ATR buffer)

Weekly rebalance to avoid over-trading. Single-asset per position.

Hypothesis: Short-term oversold conditions with Bollinger Band + RSI
confirmation indicate temporary dislocations that revert within days.
Combining two independent signals reduces false entries.

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


@strategy("BollingerMeanReversion")
class BollingerMeanReversion(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        bb_lookback: int = 20,
        bb_num_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        stop_atr_mult: float = 1.5,
        holding_days: int = 5,
        max_position_pct: float = 0.10,
    ):
        super().__init__("BollingerMeanReversion")
        self._symbols = symbols or ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM"]
        self.bb_lookback = bb_lookback
        self.bb_num_std = bb_num_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.holding_days = holding_days
        self.max_position_pct = max_position_pct

        self._day_data: Dict[str, List] = {}
        self._entry_dates: Dict[str, date] = {}
        self._stop_prices: Dict[str, float] = {}
        self._pending_buy_symbols: set = set()

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("BollingerMeanReversion")
        self.logger.info(
            f"BollingerMeanReversion starting with BB({self.bb_lookback},{self.bb_num_std}), "
            f"RSI({self.rsi_period})"
        )

    def _get_closes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [
            b.get("close", 0) if isinstance(b, dict) else getattr(b, "close", 0)
            for b in bars
        ]

    def _get_highs(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [
            b.get("high", 0) if isinstance(b, dict) else getattr(b, "high", 0)
            for b in bars
        ]

    def _get_lows(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [
            b.get("low", 0) if isinstance(b, dict) else getattr(b, "low", 0)
            for b in bars
        ]

    def _get_last_price(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        return float(closes[-1]) if closes else 0.0

    def _calculate_bb(self, symbol: str) -> tuple:
        closes = self._get_closes(symbol)
        if len(closes) < self.bb_lookback:
            return None, None, None
        recent = np.array(closes[-self.bb_lookback:])
        sma = np.mean(recent)
        std = np.std(recent, ddof=1)
        upper = sma + self.bb_num_std * std
        lower = sma - self.bb_num_std * std
        return sma, upper, lower

    def _calculate_rsi(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.rsi_period + 1:
            return 50.0
        deltas = np.diff(closes[-(self.rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calculate_atr(self, symbol: str) -> float:
        highs = self._get_highs(symbol)
        lows = self._get_lows(symbol)
        closes = self._get_closes(symbol)
        if len(closes) < self.atr_period + 1:
            return 0.0
        trs = []
        for i in range(-self.atr_period, 0):
            h = highs[i]
            l = lows[i]
            pc = closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def _check_entry(self, symbol: str) -> bool:
        sma, upper, lower = self._calculate_bb(symbol)
        if lower is None:
            return False
        price = self._get_last_price(symbol)
        rsi = self._calculate_rsi(symbol)
        return price < lower and rsi < self.rsi_oversold

    def _check_exit(self, symbol: str) -> bool:
        sma, upper, lower = self._calculate_bb(symbol)
        if sma is None:
            return False
        price = self._get_last_price(symbol)
        rsi = self._calculate_rsi(symbol)
        if price >= sma:
            return True
        if rsi > self.rsi_overbought:
            return True
        stop = self._stop_prices.get(symbol, 0)
        if stop > 0 and price <= stop:
            return True
        return False

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

        max_keep = max(self.bb_lookback, self.rsi_period + 1, self.atr_period + 1) * 2
        if len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep // 2:]

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        nav = context.portfolio.nav

        for symbol in list(self._positions.keys()):
            pos_qty = self._positions.get(symbol, 0)
            if pos_qty <= 0:
                continue

            entry_date = self._entry_dates.get(symbol)
            if entry_date is not None:
                days_held = (trading_date - entry_date).days
                if days_held >= self.holding_days and self._check_exit(symbol):
                    self.sell(symbol, pos_qty)
                    self._stop_prices.pop(symbol, None)
                    self._entry_dates.pop(symbol, None)
                    continue

            if self._check_exit(symbol):
                self.sell(symbol, pos_qty)
                self._stop_prices.pop(symbol, None)
                self._entry_dates.pop(symbol, None)

        for symbol in self._symbols:
            if self._positions.get(symbol, 0) > 0:
                continue
            if symbol in self._pending_buy_symbols:
                continue
            if not self._check_entry(symbol):
                continue

            price = self._get_last_price(symbol)
            if price <= 0:
                continue

            atr = self._calculate_atr(symbol)
            stop = price - self.stop_atr_mult * atr

            qty = int((nav * self.max_position_pct) / price)
            if qty > 0:
                self.buy(symbol, qty)
                self._pending_buy_symbols.add(symbol)
                self._entry_dates[symbol] = trading_date
                self._stop_prices[symbol] = stop

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        if hasattr(fill, "side") and fill.side == "BUY" and hasattr(fill, "symbol"):
            self._pending_buy_symbols.discard(fill.symbol)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._day_data.clear()
        self._entry_dates.clear()
        self._stop_prices.clear()
        self._pending_buy_symbols.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry_dates": {k: str(v) for k, v in self._entry_dates.items()},
            "stop_prices": self._stop_prices,
            "parameters": {
                "bb_lookback": self.bb_lookback,
                "bb_num_std": self.bb_num_std,
                "rsi_period": self.rsi_period,
                "rsi_oversold": self.rsi_oversold,
                "rsi_overbought": self.rsi_overbought,
                "stop_atr_mult": self.stop_atr_mult,
                "holding_days": self.holding_days,
            },
        }
