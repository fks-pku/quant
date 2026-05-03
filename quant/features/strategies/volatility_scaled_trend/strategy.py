"""Volatility-Scaled Trend Following - Multi-asset trend with inverse-vol weighting.

For each asset:
  1. Trend signal: close > SMA(lookback) -> bullish, else bearish
  2. Volatility scaling: weight = target_vol / realized_vol, capped at max_weight
  3. Go long when bullish with volatility-scaled weight, flat when bearish

Monthly rebalance (21 trading days) to minimize turnover.

Hypothesis: Trend following captures behavioral biases (anchoring, disposition effect).
Volatility scaling equalizes risk contribution across assets, improving Sharpe.
A-Shares have strong trends driven by retail dominance and policy cycles.

Source: Alpha Architect DIY Trend-Following (2025), Quantpedia Tactical Allocation
Authors: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("VolatilityScaledTrend")
class VolatilityScaledTrend(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        sma_lookback: int = 50,
        vol_lookback: int = 20,
        target_vol: float = 0.15,
        max_weight: float = 0.25,
        holding_days: int = 21,
    ):
        _syms = symbols or [
            "510300", "510500", "159915", "512880", "512010",
            "510050", "512100", "518880",
        ]
        super().__init__("VolatilityScaledTrend", _syms, holding_days)
        self.sma_lookback = sma_lookback
        self.vol_lookback = vol_lookback
        self.target_vol = target_vol
        self.max_weight = max_weight

    @property
    def _max_keep_hint(self) -> int:
        return max(self.sma_lookback, self.vol_lookback) * 2 + 10

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("VolatilityScaledTrend")
        self.logger.info(
            f"VolatilityScaledTrend starting with SMA({self.sma_lookback}), "
            f"vol_lookback={self.vol_lookback}, target_vol={self.target_vol}"
        )

    def _calculate_sma(self, symbol: str) -> Optional[float]:
        closes = self._get_closes(symbol)
        if len(closes) < self.sma_lookback:
            return None
        return float(np.mean(closes[-self.sma_lookback:]))

    def _calculate_realized_vol(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.vol_lookback + 1:
            return 0.30
        recent = np.array(closes[-(self.vol_lookback + 1):], dtype=float)
        prev = recent[:-1]
        curr = recent[1:]
        valid = prev > 0
        if valid.sum() < 5:
            return 0.30
        rets = (curr[valid] - prev[valid]) / prev[valid]
        extreme = np.abs(rets) < 0.20
        if extreme.sum() < 5:
            return 0.30
        return float(np.std(rets[extreme], ddof=1) * np.sqrt(252))

    def _calculate_weights(self) -> Dict[str, float]:
        weights = {}
        for symbol in self._symbols:
            sma = self._calculate_sma(symbol)
            if sma is None:
                continue
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            if price < sma:
                continue
            realized_vol = self._calculate_realized_vol(symbol)
            if realized_vol <= 0.01:
                realized_vol = 0.01
            raw_weight = self.target_vol / realized_vol
            weights[symbol] = min(raw_weight, self.max_weight)
        total = sum(weights.values())
        if total > 1.0:
            weights = {s: w / total for s, w in weights.items()}
        return weights

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        target_weights = self._calculate_weights()
        nav = context.portfolio.nav

        for symbol in list(self._positions.keys()):
            if symbol not in target_weights:
                pos_qty = self._positions.get(symbol, 0)
                if pos_qty > 0:
                    self.sell(symbol, pos_qty)

        for symbol, weight in target_weights.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_qty = int((nav * weight) / price)
            current_qty = int(self._positions.get(symbol, 0))
            if target_qty > current_qty:
                self.buy(symbol, target_qty - current_qty)
            elif target_qty < current_qty:
                self.sell(symbol, current_qty - target_qty)

        self.logger.info(
            f"VolatilityScaledTrend rebalanced: "
            + ", ".join(f"{s}={w:.1%}" for s, w in target_weights.items())
        )

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "sma_lookback": self.sma_lookback,
            "vol_lookback": self.vol_lookback,
            "target_vol": self.target_vol,
            "max_weight": self.max_weight,
            "holding_days": self.holding_days,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
        }
