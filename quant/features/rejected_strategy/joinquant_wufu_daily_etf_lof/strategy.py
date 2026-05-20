"""Daily JoinQuant Wufu-style ETF/LOF momentum rotation."""

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


DEFAULT_CASH_SYMBOL = "511880"
DEFAULT_EXCLUDE_KEYWORDS = (
    "货币",
    "现金",
    "添利",
    "收益",
    "债",
    "国债",
    "政金",
    "信用",
    "可转债",
)


@strategy("joinquant_wufu_daily_etf_lof")
class JoinquantWufuDailyEtfLofStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        cash_symbol: str = DEFAULT_CASH_SYMBOL,
        score_window: int = 25,
        liquidity_window: int = 20,
        min_active_candidates: int = 5,
        min_score: float = 0.0,
        min_avg_turnover: float = 20_000_000.0,
        max_premium_rate: float = 0.03,
        target_exposure: float = 0.98,
        lot_size: int = 100,
        holding_days: int = 1,
        exclude_keywords: Optional[List[str]] = None,
    ):
        trade_symbols = [str(symbol) for symbol in (symbols or [cash_symbol])]
        if str(cash_symbol) not in trade_symbols:
            trade_symbols.append(str(cash_symbol))
        super().__init__("joinquant_wufu_daily_etf_lof", trade_symbols, holding_days=holding_days)
        self.cash_symbol = str(cash_symbol)
        self.score_window = max(5, int(score_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_active_candidates = max(1, int(min_active_candidates))
        self.min_score = float(min_score)
        self.min_avg_turnover = float(min_avg_turnover)
        self.max_premium_rate = abs(float(max_premium_rate))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.exclude_keywords = tuple(str(item) for item in (exclude_keywords or DEFAULT_EXCLUDE_KEYWORDS))

    @property
    def _max_keep_hint(self) -> int:
        return max(self.score_window, self.liquidity_window) + 5

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        target = self._select_target(trading_date)
        if not target:
            return
        target_price = self._get_last_price(target)
        if target_price <= 0:
            return

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol != target:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        if self._positions.get(target, 0) > 0:
            return

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        quantity = self._round_lot(nav * self.target_exposure / target_price)
        if quantity > 0:
            self.buy(target, quantity, "MARKET", target_price)

    def _select_target(self, trading_date: date) -> str:
        scored = []
        for symbol in self._symbols:
            if symbol == self.cash_symbol:
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
        if not bar:
            return False
        if not self._is_current_bar(bar, trading_date):
            return False
        if str(self._bar_value(bar, "fund_status", "L") or "L").upper() in {"D", "P"}:
            return False
        name = str(self._bar_value(bar, "fund_name", "") or "")
        if any(keyword and keyword in name for keyword in self.exclude_keywords):
            return False
        premium = self._numeric_bar_value(bar, "premium_rate")
        if premium is not None and abs(premium) > self.max_premium_rate:
            return False
        return self._avg_turnover(symbol) >= self.min_avg_turnover

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

    def _avg_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_window :]
        values = [self._cash_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0]
        if len(values) < self.liquidity_window:
            return 0.0
        return float(sum(values) / len(values))

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

    def _has_last_price(self, symbol: str, trading_date: date) -> bool:
        bar = self._get_last_bar(symbol)
        return bool(bar and self._is_current_bar(bar, trading_date) and self._get_last_price(symbol) > 0)

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

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
            "min_active_candidates": self.min_active_candidates,
            "min_score": self.min_score,
            "min_avg_turnover": self.min_avg_turnover,
            "max_premium_rate": self.max_premium_rate,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "holding_days": self.holding_days,
            "exclude_keywords": list(self.exclude_keywords),
        }
