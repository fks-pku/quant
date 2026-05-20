"""JoinQuant Wufu ETF momentum rotation."""

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


DEFAULT_CORE_ETFS = ["159915", "513100", "511010", "518880", "159980"]
DEFAULT_FILL_ETF = "511880"


@strategy("joinquant_wufu_etf_momentum")
class JoinquantWufuEtfMomentumStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        core_symbols: Optional[List[str]] = None,
        fill_symbol: str = DEFAULT_FILL_ETF,
        score_window: int = 13,
        min_active_candidates: int = 5,
        min_score: float = 0.0,
        boll_window: int = 20,
        boll_std: float = 2.0,
        extreme_days: int = 3,
        target_exposure: float = 0.98,
        lot_size: int = 100,
        holding_days: int = 1,
    ):
        core = [str(symbol) for symbol in (core_symbols or DEFAULT_CORE_ETFS)]
        fill = str(fill_symbol)
        trade_symbols = [str(symbol) for symbol in (symbols or [*core, fill])]
        super().__init__("joinquant_wufu_etf_momentum", trade_symbols, holding_days=holding_days)
        self.core_symbols = [symbol for symbol in core if symbol in self._symbol_set]
        self.fill_symbol = fill
        self.score_window = max(3, int(score_window))
        self.min_active_candidates = max(1, int(min_active_candidates))
        self.min_score = float(min_score)
        self.boll_window = max(5, int(boll_window))
        self.boll_std = float(boll_std)
        self.extreme_days = max(1, int(extreme_days))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))

    @property
    def _max_keep_hint(self) -> int:
        return max(self.score_window, self.boll_window + self.extreme_days) + 5

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        target = self._select_target()
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

    def _select_target(self) -> str:
        scored = []
        for symbol in self.core_symbols:
            score = self._momentum_score(symbol)
            if score is None:
                continue
            if self._extreme_down(symbol):
                continue
            scored.append((score, symbol))
        if len(scored) < self.min_active_candidates:
            return ""
        score, symbol = max(scored, key=lambda item: (item[0], item[1]))
        if score <= self.min_score:
            return self.fill_symbol if self._has_last_price(self.fill_symbol) else ""
        return symbol

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

    def _extreme_down(self, symbol: str) -> bool:
        closes = self._valid_closes(symbol, self.boll_window + self.extreme_days)
        if len(closes) < self.boll_window + self.extreme_days:
            return False
        for offset in range(self.extreme_days, 0, -1):
            window = closes[-self.boll_window - offset + 1 : -offset + 1 if offset > 1 else None]
            if len(window) < self.boll_window:
                return False
            current = closes[-offset]
            mean = float(np.mean(window))
            std = float(np.std(window, ddof=0))
            if current >= mean - self.boll_std * std:
                return False
        return True

    def _valid_closes(self, symbol: str, count: int) -> List[float]:
        closes = self._get_closes(symbol)
        values = [float(value) for value in closes if value is not None and value > 0 and math.isfinite(float(value))]
        return values[-count:]

    def _has_last_price(self, symbol: str) -> bool:
        return self._get_last_price(symbol) > 0

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "core_symbols": list(self.core_symbols),
            "fill_symbol": self.fill_symbol,
            "score_window": self.score_window,
            "min_active_candidates": self.min_active_candidates,
            "min_score": self.min_score,
            "boll_window": self.boll_window,
            "boll_std": self.boll_std,
            "extreme_days": self.extreme_days,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "holding_days": self.holding_days,
        }
