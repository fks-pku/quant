"""JoinQuant Qixing daily ETF/LOF momentum rotation."""

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


DEFAULT_CASH_SYMBOL = "511880"
DEFAULT_RISK_SYMBOLS = [
    "510300",
    "510500",
    "512100",
    "159915",
    "159949",
    "510050",
    "510880",
    "512880",
    "512000",
    "512480",
    "512690",
    "512800",
    "512660",
    "518880",
    "513100",
    "513050",
    "513030",
    "159920",
]
DEFAULT_EXCLUDE_SYMBOLS = ["511010", "511990"]


@strategy("joinquant_qixing_daily_etf_rotation")
class JoinquantQixingDailyEtfRotationStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        cash_symbol: str = DEFAULT_CASH_SYMBOL,
        score_window: int = 24,
        liquidity_window: int = 20,
        volume_window: int = 20,
        min_active_candidates: int = 5,
        min_score: float = 0.0,
        min_avg_turnover: float = 20_000_000.0,
        max_volume_multiple: float = 2.5,
        recent_drawdown_window: int = 3,
        recent_drawdown_stop: float = 0.05,
        fixed_stop_loss: float = 0.08,
        target_exposure: float = 0.98,
        lot_size: int = 100,
        holding_days: int = 1,
        exclude_symbols: Optional[List[str]] = None,
    ):
        base_symbols = symbols or [*DEFAULT_RISK_SYMBOLS, cash_symbol]
        trade_symbols = [str(symbol) for symbol in base_symbols]
        if str(cash_symbol) not in trade_symbols:
            trade_symbols.append(str(cash_symbol))
        super().__init__("joinquant_qixing_daily_etf_rotation", list(dict.fromkeys(trade_symbols)), holding_days=holding_days)
        self.cash_symbol = str(cash_symbol)
        self.score_window = max(5, int(score_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.volume_window = max(1, int(volume_window))
        self.min_active_candidates = max(1, int(min_active_candidates))
        self.min_score = float(min_score)
        self.min_avg_turnover = float(min_avg_turnover)
        self.max_volume_multiple = max(1.0, float(max_volume_multiple))
        self.recent_drawdown_window = max(1, int(recent_drawdown_window))
        self.recent_drawdown_stop = abs(float(recent_drawdown_stop))
        self.fixed_stop_loss = abs(float(fixed_stop_loss))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.exclude_symbols = set(str(symbol) for symbol in (exclude_symbols or DEFAULT_EXCLUDE_SYMBOLS))
        self._entry_price_by_symbol: Dict[str, float] = {}

    @property
    def _max_keep_hint(self) -> int:
        return max(
            self.score_window,
            self.liquidity_window,
            self.volume_window + 1,
            self.recent_drawdown_window + 1,
        ) + 5

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        symbol = str(getattr(fill, "symbol", "") or "")
        side = str(getattr(fill, "side", "") or "").upper()
        if not symbol:
            return
        if side == "BUY":
            price = self._fill_price(fill)
            if price > 0:
                self._entry_price_by_symbol[symbol] = price
        elif side == "SELL" and self._positions.get(symbol, 0) <= 0:
            self._entry_price_by_symbol.pop(symbol, None)

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        target = self._select_target(trading_date)
        if target is None:
            return

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol != target:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        if not target:
            return
        target_price = self._get_last_price(target)
        if target_price <= 0 or self._positions.get(target, 0) > 0:
            return

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        quantity = self._round_lot(nav * self.target_exposure / target_price)
        if quantity > 0:
            self.buy(target, quantity, "MARKET", target_price)

    def _select_target(self, trading_date: date) -> Optional[str]:
        current_risk = self._current_risk_position()
        if current_risk and self._stop_triggered(current_risk):
            return self.cash_symbol if self._has_last_price(self.cash_symbol, trading_date) else ""

        scored: List[Tuple[float, str]] = []
        for symbol in self._symbols:
            if symbol == self.cash_symbol or symbol in self.exclude_symbols:
                continue
            if not self._passes_filters(symbol, trading_date):
                continue
            score = self._momentum_score(symbol)
            if score is None:
                continue
            scored.append((score, symbol))

        if len(scored) < self.min_active_candidates:
            return self.cash_symbol if self._has_last_price(self.cash_symbol, trading_date) else ""

        score, symbol = max(scored, key=lambda item: (item[0], item[1]))
        if score <= self.min_score:
            return self.cash_symbol if self._has_last_price(self.cash_symbol, trading_date) else ""
        return symbol

    def _passes_filters(self, symbol: str, trading_date: date) -> bool:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return False
        if str(self._bar_value(bar, "fund_status", "L") or "L").upper() in {"D", "P"}:
            return False
        if self._stop_triggered(symbol):
            return False
        return self._avg_turnover(symbol) >= self.min_avg_turnover and self._passes_volume_filter(symbol)

    def _momentum_score(self, symbol: str) -> Optional[float]:
        closes = self._valid_closes(symbol, self.score_window)
        if len(closes) < self.score_window:
            return None
        y = np.log(np.asarray(closes, dtype=float))
        x = np.arange(len(y), dtype=float)
        weights = np.linspace(1.0, 2.0, len(y))
        try:
            slope, intercept = np.polyfit(x, y, 1, w=weights)
        except Exception:
            return None
        fitted = slope * x + intercept
        ss_res = float(np.sum((y - fitted) ** 2))
        ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
        r_squared = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
        annualized = math.exp(float(slope) * 250.0) - 1.0
        if not math.isfinite(annualized):
            return None
        return annualized * r_squared

    def _stop_triggered(self, symbol: str) -> bool:
        if symbol == self.cash_symbol:
            return False
        if self._recent_drawdown(symbol) <= -self.recent_drawdown_stop:
            return True
        entry_price = self._entry_price_by_symbol.get(symbol, 0.0)
        last_price = self._get_last_price(symbol)
        if entry_price > 0 and last_price > 0 and last_price / entry_price - 1.0 <= -self.fixed_stop_loss:
            return True
        return False

    def _recent_drawdown(self, symbol: str) -> float:
        closes = self._valid_closes(symbol, self.recent_drawdown_window + 1)
        if len(closes) < self.recent_drawdown_window + 1 or closes[0] <= 0:
            return 0.0
        return closes[-1] / closes[0] - 1.0

    def _avg_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_window :]
        values = [self._cash_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0]
        if len(values) < self.liquidity_window:
            return 0.0
        return float(sum(values) / len(values))

    def _passes_volume_filter(self, symbol: str) -> bool:
        bars = self._day_data.get(symbol, [])[-(self.volume_window + 1) :]
        if len(bars) < self.volume_window + 1:
            return False
        current = self._numeric_bar_value(bars[-1], "volume") or 0.0
        previous = [self._numeric_bar_value(bar, "volume") or 0.0 for bar in bars[:-1]]
        previous = [value for value in previous if value > 0]
        if current <= 0 or len(previous) < self.volume_window:
            return False
        avg_volume = float(sum(previous) / len(previous))
        return current <= avg_volume * self.max_volume_multiple

    def _cash_turnover(self, bar: Any) -> float:
        close = self._numeric_bar_value(bar, "close") or 0.0
        volume = self._numeric_bar_value(bar, "volume") or 0.0
        turnover = self._numeric_bar_value(bar, "turnover")
        if turnover is None or turnover <= 0:
            return close * volume if close > 0 and volume > 0 else 0.0
        if close > 0 and volume > 0:
            ratio = close * volume / turnover
            if 5.0 <= ratio <= 20.0 or 500.0 <= ratio <= 2000.0:
                return turnover * 1000.0
        return turnover

    def _valid_closes(self, symbol: str, count: int) -> List[float]:
        closes = self._get_closes(symbol)
        values = [float(value) for value in closes if value is not None and value > 0 and math.isfinite(float(value))]
        return values[-count:]

    def _current_risk_position(self) -> str:
        for symbol, quantity in self._positions.items():
            if quantity > 0 and symbol != self.cash_symbol:
                return symbol
        return ""

    def _has_last_price(self, symbol: str, trading_date: date) -> bool:
        bar = self._get_last_bar(symbol)
        return bool(bar and self._is_current_bar(bar, trading_date) and self._get_last_price(symbol) > 0)

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "entry_price", "price"):
            try:
                value = float(getattr(fill, field, 0.0) or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0 and math.isfinite(value):
                return value
        return 0.0

    @classmethod
    def _is_current_bar(cls, bar: Any, trading_date: date) -> bool:
        value = cls._bar_value(bar, "timestamp")
        if value is None:
            return True
        try:
            return value.date() == trading_date
        except AttributeError:
            return str(value)[:10] == str(trading_date)

    @staticmethod
    def _bar_value(bar: Any, key: str, default: Any = None) -> Any:
        if isinstance(bar, dict):
            return bar.get(key, default)
        return getattr(bar, key, default)

    @classmethod
    def _numeric_bar_value(cls, bar: Any, key: str) -> Optional[float]:
        value = cls._bar_value(bar, key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbols": list(self._symbols),
            "cash_symbol": self.cash_symbol,
            "score_window": self.score_window,
            "liquidity_window": self.liquidity_window,
            "volume_window": self.volume_window,
            "min_active_candidates": self.min_active_candidates,
            "min_score": self.min_score,
            "min_avg_turnover": self.min_avg_turnover,
            "max_volume_multiple": self.max_volume_multiple,
            "recent_drawdown_window": self.recent_drawdown_window,
            "recent_drawdown_stop": self.recent_drawdown_stop,
            "fixed_stop_loss": self.fixed_stop_loss,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "holding_days": self.holding_days,
            "exclude_symbols": sorted(self.exclude_symbols),
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"entry_prices": dict(self._entry_price_by_symbol)}
