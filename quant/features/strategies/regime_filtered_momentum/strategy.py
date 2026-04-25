"""Regime-Filtered Momentum - Cross-sectional momentum with regime-based position sizing.

Based on arXiv:2604.18821 (Liu, Apr 2026): strategies launched after extreme factor
runs experience sharp deterioration. This strategy detects extreme market conditions
via realized volatility of benchmark and reduces momentum exposure accordingly.

Logic:
  1. Calculate 20-day momentum for each stock
  2. Detect market regime via realized volatility of benchmark:
     - LOW vol (<15% annualized) -> full momentum exposure
     - MEDIUM vol (15-25%) -> reduced exposure (50%)
     - HIGH vol (>25%) -> minimal exposure (20%) -- extreme factor environment
  3. Go long top momentum stocks with regime-adjusted position size
  4. Monthly rebalance

Hypothesis: Momentum works in normal markets but crashes during regime transitions
(volatility spikes). Reducing exposure during extreme factor environments preserves
capital. The 2026 arXiv paper empirically validates this across 1,726 strategies.

Source: arXiv:2604.18821, Chang Liu (Apr 2026)
Authors: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.features.trading.engine import Context


@strategy("RegimeFilteredMomentum")
class RegimeFilteredMomentum(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        benchmark_symbol: str = "510300",
        momentum_lookback: int = 20,
        vol_lookback: int = 20,
        vol_low_threshold: float = 0.15,
        vol_high_threshold: float = 0.25,
        holding_days: int = 21,
        top_pct: float = 0.2,
        max_position_pct: float = 0.10,
    ):
        super().__init__("RegimeFilteredMomentum")
        self._symbols = symbols or [
            "600519", "000858", "601318", "600036", "000333",
            "002415", "300750", "601012", "600900", "000651",
            "601398", "600030", "000001", "601166", "002714",
        ]
        self.benchmark_symbol = benchmark_symbol
        self.momentum_lookback = momentum_lookback
        self.vol_lookback = vol_lookback
        self.vol_low_threshold = vol_low_threshold
        self.vol_high_threshold = vol_high_threshold
        self.holding_days = holding_days
        self.top_pct = top_pct
        self.max_position_pct = max_position_pct

        self._day_data: Dict[str, List] = {}
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0
        self._long_positions: List[str] = []
        self._current_regime: str = "normal"
        self._regime_exposure: float = 1.0

    @property
    def symbols(self) -> List[str]:
        return list(set(self._symbols + [self.benchmark_symbol]))

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("RegimeFilteredMomentum")
        self.logger.info(
            f"RegimeFilteredMomentum: benchmark={self.benchmark_symbol}, "
            f"regime thresholds=({self.vol_low_threshold:.0%}, {self.vol_high_threshold:.0%})"
        )

    def _get_closes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [
            b.get("close", 0) if isinstance(b, dict) else getattr(b, "close", 0)
            for b in bars
        ]

    def _get_last_price(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        return float(closes[-1]) if closes else 0.0

    def _calculate_momentum(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.momentum_lookback + 1:
            return 0.0
        past = closes[-self.momentum_lookback - 1]
        if past <= 0:
            return 0.0
        return (closes[-1] - past) / past

    def _detect_regime(self) -> tuple:
        closes = self._get_closes(self.benchmark_symbol)
        if len(closes) < self.vol_lookback + 1:
            return "normal", 1.0

        recent = np.array(closes[-(self.vol_lookback + 1):], dtype=float)
        prev = recent[:-1]
        curr = recent[1:]
        valid = prev > 0
        if valid.sum() < 5:
            return "normal", 1.0
        rets = (curr[valid] - prev[valid]) / prev[valid]
        extreme = np.abs(rets) < 0.20
        if extreme.sum() < 5:
            return "normal", 1.0

        realized_vol = float(np.std(rets[extreme], ddof=1) * np.sqrt(252))

        if realized_vol < self.vol_low_threshold:
            return "low_vol", 1.0
        elif realized_vol > self.vol_high_threshold:
            return "high_vol", 0.20
        else:
            t = (realized_vol - self.vol_low_threshold) / max(
                self.vol_high_threshold - self.vol_low_threshold, 0.01
            )
            exposure = 1.0 - t * 0.6
            return "transition", exposure

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        regime, exposure = self._detect_regime()
        if regime != self._current_regime:
            self.logger.info(f"Regime change: {self._current_regime} -> {regime} (exposure={exposure:.0%})")
        self._current_regime = regime
        self._regime_exposure = exposure

        momentum_scores = {}
        for symbol in self._symbols:
            momentum_scores[symbol] = self._calculate_momentum(symbol)

        sorted_syms = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
        n_top = max(1, int(len(sorted_syms) * self.top_pct))
        new_long = [s[0] for s in sorted_syms[:n_top] if s[1] > 0]

        for sym in list(self._long_positions):
            if sym not in new_long:
                pos_qty = self._positions.get(sym, 0)
                if pos_qty > 0:
                    self.sell(sym, pos_qty)

        self._long_positions = new_long
        nav = context.portfolio.nav
        adjusted_pct = self.max_position_pct * exposure
        weight = adjusted_pct / len(new_long) if new_long else 0

        for symbol in new_long:
            if self._positions.get(symbol, 0) > 0:
                continue
            price = self._get_last_price(symbol)
            if price > 0:
                qty = int((nav * weight) / price)
                if qty > 0:
                    self.buy(symbol, qty)

        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
        else:
            return

        if not symbol or symbol not in self.symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []
        self._day_data[symbol].append(data)

        max_keep = max(self.momentum_lookback, self.vol_lookback) * 2 + 10
        if len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep:]

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        if self._last_rebalance_date is not None:
            self._days_since_rebalance += 1
            if self._days_since_rebalance < self.holding_days:
                return
        self._execute_rebalance(context, trading_date)

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                price = self._get_last_price(symbol)
                self.sell(symbol, quantity, "MARKET", price if price > 0 else None)
        self._day_data.clear()
        self._long_positions.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "long_positions": self._long_positions,
            "current_regime": self._current_regime,
            "regime_exposure": self._regime_exposure,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
            "parameters": {
                "benchmark_symbol": self.benchmark_symbol,
                "momentum_lookback": self.momentum_lookback,
                "vol_low_threshold": self.vol_low_threshold,
                "vol_high_threshold": self.vol_high_threshold,
                "holding_days": self.holding_days,
            },
        }
