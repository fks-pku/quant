"""Close-to-open overnight anomaly candidate strategy."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


STRATEGY_NAME = "close_to_open_overnight_anomaly"
SAME_CLOSE = "SAME_CLOSE"


@strategy(STRATEGY_NAME)
class CloseToOpenOvernightAnomalyStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        max_positions: int = 10,
        lookback: int = 20,
        liquidity_lookback: int = 20,
        min_avg_turnover: float = 20_000_000.0,
        min_price: float = 1.0,
        target_exposure: float = 0.95,
        lot_size: int = 100,
        require_positive_score: bool = True,
    ):
        super().__init__(STRATEGY_NAME, [str(symbol) for symbol in (symbols or [])], holding_days=1)
        self.max_positions = max(1, int(max_positions))
        self.lookback = max(1, int(lookback))
        self.liquidity_lookback = max(1, int(liquidity_lookback))
        self.min_avg_turnover = max(0.0, float(min_avg_turnover))
        self.min_price = max(0.0, float(min_price))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.require_positive_score = bool(require_positive_score)
        self._pending_entry_symbols: set[str] = set()
        self._last_scores: Dict[str, float] = {}
        self._diagnostics: Dict[str, Any] = {
            "entry_rejections": {},
            "last_selected": [],
            "last_scores": {},
        }

    @property
    def _max_keep_hint(self) -> int:
        return max(self.lookback + 2, self.liquidity_lookback + 1, 10)

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._pending_entry_symbols.intersection_update(
            symbol for symbol, quantity in self._positions.items() if quantity > 0
        )
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            sell_quantity = int(quantity)
            if sell_quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0 or self.target_exposure <= 0:
            return

        candidates = []
        for symbol in self.symbols:
            if self._positions.get(symbol, 0) > 0:
                continue
            score = self._entry_score(symbol)
            if score is None:
                continue
            if self.require_positive_score and score <= 0:
                self._count("entry_rejections", "non_positive_overnight_score")
                continue
            candidates.append((score, symbol))

        selected = [
            symbol
            for score, symbol in sorted(candidates, key=lambda item: (-item[0], item[1]))[: self.max_positions]
        ]
        self._last_scores = {symbol: score for score, symbol in candidates}
        self._diagnostics["last_scores"] = dict(self._last_scores)
        self._diagnostics["last_selected"] = list(selected)
        if not selected:
            return

        slot_value = nav * self.target_exposure / float(self.max_positions)
        for symbol in selected:
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            quantity = self._round_lot(slot_value / price)
            if quantity <= 0:
                self._count("entry_rejections", "below_lot_size")
                continue
            self._pending_entry_symbols.add(symbol)
            order_id = self.buy(symbol, quantity, "MARKET", price, execution_timing=SAME_CLOSE)
            if order_id is None:
                self._pending_entry_symbols.discard(symbol)

    def on_fill(self, context: "Context", fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        super().on_fill(context, fill)
        if not symbol:
            return
        side = str(getattr(fill, "side", "") or "").upper()
        quantity = float(getattr(fill, "quantity", 0.0) or 0.0)
        if side == "BUY" and symbol in self._pending_entry_symbols and quantity > 0:
            fill_price = self._fill_price(fill)
            self.sell(symbol, quantity, "MARKET", fill_price if fill_price > 0 else None)
        elif side == "SELL" and self._positions.get(symbol, 0) <= 0:
            self._pending_entry_symbols.discard(symbol)

    def on_stop(self, context: "Context") -> None:
        self._pending_entry_symbols.clear()
        self._last_scores.clear()
        super().on_stop(context)

    def _entry_score(self, symbol: str) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.lookback + 1:
            self._count("entry_rejections", "insufficient_lookback")
            return None
        current_bar = bars[-1]
        if self._entry_risk(symbol, current_bar):
            return None
        returns = []
        start = len(bars) - self.lookback
        for idx in range(start, len(bars)):
            prev_close = self._adj(bars[idx - 1], "close")
            open_price = self._adj(bars[idx], "open")
            if prev_close <= 0 or open_price <= 0:
                continue
            returns.append(open_price / prev_close - 1.0)
        if not returns:
            self._count("entry_rejections", "missing_overnight_returns")
            return None
        return sum(returns) / float(len(returns))

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        price = self._price(bar)
        if price < self.min_price:
            self._count("entry_rejections", "low_price")
            return True
        if self._bool_bar_value(bar, "is_st", False):
            self._count("entry_rejections", "st")
            return True
        if self._bool_bar_value(bar, "_suspended", False):
            self._count("entry_rejections", "suspended")
            return True
        if self._bool_bar_value(bar, "tradable", True) is False:
            self._count("entry_rejections", "not_tradable")
            return True
        if self._bool_bar_value(bar, "has_daily_bar", True) is False:
            self._count("entry_rejections", "missing_daily_bar")
            return True
        if self._bool_bar_value(bar, "is_listed", True) is False:
            self._count("entry_rejections", "not_listed")
            return True
        list_status = str(self._bar_value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            self._count("entry_rejections", "bad_list_status")
            return True
        if self._average_turnover(symbol) < self.min_avg_turnover:
            self._count("entry_rejections", "low_turnover")
            return True
        return False

    def _average_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_lookback:]
        values = [self._cash_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if not values:
            return 0.0
        return sum(values) / float(len(values))

    def _cash_turnover(self, bar: Any) -> float:
        turnover = self._float_bar_value(bar, "turnover", 0.0)
        if turnover > 0:
            close = self._price(bar)
            volume = self._float_bar_value(bar, "volume", 0.0)
            if close > 0 and volume > 0:
                ratio = close * volume / turnover
                if 5.0 <= ratio <= 20.0 or 500.0 <= ratio <= 2000.0:
                    return turnover * 1000.0
            return turnover
        return self._price(bar) * self._float_bar_value(bar, "volume", 0.0)

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "last_selected": list(self._diagnostics.get("last_selected") or []),
            "last_scores": dict(self._diagnostics.get("last_scores") or {}),
            "parameters": self._get_parameters(),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "max_positions": self.max_positions,
            "lookback": self.lookback,
            "liquidity_lookback": self.liquidity_lookback,
            "min_avg_turnover": self.min_avg_turnover,
            "min_price": self.min_price,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "require_positive_score": self.require_positive_score,
        }

    def _count(self, bucket: str, key: str) -> None:
        values = self._diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            try:
                number = float(value or 0.0)
            except (TypeError, ValueError):
                continue
            if number > 0 and math.isfinite(number):
                return number
        return 0.0

    @staticmethod
    def _bar_value(bar: Any, field: str, default: Any = None) -> Any:
        if isinstance(bar, dict):
            return bar.get(field, default)
        return getattr(bar, field, default)

    @classmethod
    def _float_bar_value(cls, bar: Any, field: str, default: float = 0.0) -> float:
        try:
            value = float(cls._bar_value(bar, field, default) or default)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    @classmethod
    def _bool_bar_value(cls, bar: Any, field: str, default: bool) -> bool:
        value = cls._bar_value(bar, field, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)
