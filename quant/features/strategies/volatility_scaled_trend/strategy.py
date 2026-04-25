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

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.features.trading.engine import Context


@strategy("VolatilityScaledTrend")
class VolatilityScaledTrend(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        sma_lookback: int = 50,
        vol_lookback: int = 20,
        target_vol: float = 0.15,
        max_weight: float = 0.25,
        holding_days: int = 21,
    ):
        super().__init__("VolatilityScaledTrend")
        self._symbols = symbols or [
            "510300", "510500", "159915", "512880", "512010",
            "510050", "512100", "518880",
        ]
        self.sma_lookback = sma_lookback
        self.vol_lookback = vol_lookback
        self.target_vol = target_vol
        self.max_weight = max_weight
        self.holding_days = holding_days

        self._day_data: Dict[str, List] = {}
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("VolatilityScaledTrend")
        self.logger.info(
            f"VolatilityScaledTrend starting with SMA({self.sma_lookback}), "
            f"vol_lookback={self.vol_lookback}, target_vol={self.target_vol}"
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

        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0
        self.logger.info(
            f"VolatilityScaledTrend rebalanced: "
            + ", ".join(f"{s}={w:.1%}" for s, w in target_weights.items())
        )

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

        max_keep = max(self.sma_lookback, self.vol_lookback) * 2 + 10
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

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
            "parameters": {
                "sma_lookback": self.sma_lookback,
                "vol_lookback": self.vol_lookback,
                "target_vol": self.target_vol,
                "max_weight": self.max_weight,
                "holding_days": self.holding_days,
            },
        }
