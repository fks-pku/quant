"""Multi Factor Score Strategy - Momentum + RSI + Volume composite ranking.

Three-factor composite scoring:
  1. Momentum (20d return) - captures trend
  2. RSI deviation (RSI - 50) - captures overbought/oversold
  3. Volume ratio (current / 20d avg) - confirms conviction

Each factor is z-score normalized, then combined with configurable weights.
Go long the top quintile, rebalance weekly.

Hypothesis: Combining independent signals improves IC over any single factor.
Momentum provides direction, RSI provides timing, volume provides confirmation.
The composite signal should have higher Information Ratio than individual factors.

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.features.trading.engine import Context


@strategy("MultiFactorScore")
class MultiFactorScore(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        momentum_lookback: int = 20,
        rsi_period: int = 14,
        volume_lookback: int = 20,
        momentum_weight: float = 0.4,
        rsi_weight: float = 0.3,
        volume_weight: float = 0.3,
        top_pct: float = 0.2,
        holding_days: int = 5,
        max_position_pct: float = 0.10,
    ):
        super().__init__("MultiFactorScore")
        self._symbols = symbols or [
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "JPM", "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK",
        ]
        self.momentum_lookback = momentum_lookback
        self.rsi_period = rsi_period
        self.volume_lookback = volume_lookback
        self.momentum_weight = momentum_weight
        self.rsi_weight = rsi_weight
        self.volume_weight = volume_weight
        self.top_pct = top_pct
        self.holding_days = holding_days
        self.max_position_pct = max_position_pct

        self._day_data: Dict[str, List] = {}
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0
        self._long_positions: List[str] = []
        self._scores: Dict[str, float] = {}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("MultiFactorScore")
        total_w = self.momentum_weight + self.rsi_weight + self.volume_weight
        self.logger.info(
            f"MultiFactorScore starting with "
            f"weights=(mom={self.momentum_weight/total_w:.1%}, "
            f"rsi={self.rsi_weight/total_w:.1%}, "
            f"vol={self.volume_weight/total_w:.1%}), "
            f"top_pct={self.top_pct}"
        )

    def _get_closes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [self._adj(b, "close") for b in bars]

    def _get_volumes(self, symbol: str) -> List[float]:
        bars = self._day_data.get(symbol, [])
        return [
            b.get("volume", 0) if isinstance(b, dict) else getattr(b, "volume", 0)
            for b in bars
        ]

    def _get_last_price(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        return float(closes[-1]) if closes else 0.0

    def _calculate_momentum(self, symbol: str) -> Optional[float]:
        closes = self._get_closes(symbol)
        if len(closes) < self.momentum_lookback + 1:
            return None
        past = closes[-self.momentum_lookback - 1]
        if past <= 0:
            return None
        return (closes[-1] - past) / past

    def _calculate_rsi_deviation(self, symbol: str) -> Optional[float]:
        closes = self._get_closes(symbol)
        if len(closes) < self.rsi_period + 1:
            return None
        deltas = np.diff(closes[-(self.rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi - 50.0

    def _calculate_volume_ratio(self, symbol: str) -> Optional[float]:
        volumes = self._get_volumes(symbol)
        if len(volumes) < self.volume_lookback + 1:
            return None
        avg_vol = np.mean(volumes[-(self.volume_lookback + 1):-1])
        if avg_vol <= 0:
            return None
        return volumes[-1] / avg_vol - 1.0

    def _calculate_composite_scores(self) -> Dict[str, float]:
        raw_factors: Dict[str, Dict[str, Optional[float]]] = {}
        for symbol in self._symbols:
            raw_factors[symbol] = {
                "momentum": self._calculate_momentum(symbol),
                "rsi_dev": self._calculate_rsi_deviation(symbol),
                "volume_ratio": self._calculate_volume_ratio(symbol),
            }

        zscored: Dict[str, Dict[str, float]] = {}
        for factor_name in ["momentum", "rsi_dev", "volume_ratio"]:
            values = []
            for symbol in self._symbols:
                v = raw_factors[symbol][factor_name]
                if v is not None:
                    values.append(v)

            if len(values) < 3:
                for symbol in self._symbols:
                    zscored.setdefault(symbol, {})[factor_name] = 0.0
                continue

            mean_v = np.mean(values)
            std_v = np.std(values, ddof=1)
            if std_v == 0:
                std_v = 1.0

            for symbol in self._symbols:
                v = raw_factors[symbol][factor_name]
                z = (v - mean_v) / std_v if v is not None else 0.0
                zscored.setdefault(symbol, {})[factor_name] = float(np.clip(z, -3, 3))

        total_w = self.momentum_weight + self.rsi_weight + self.volume_weight
        scores = {}
        for symbol in self._symbols:
            f = zscored.get(symbol, {})
            scores[symbol] = (
                f.get("momentum", 0) * self.momentum_weight
                + f.get("rsi_dev", 0) * self.rsi_weight
                + f.get("volume_ratio", 0) * self.volume_weight
            ) / total_w

        return scores

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        scores = self._calculate_composite_scores()
        self._scores = scores

        if not scores:
            self._last_rebalance_date = trading_date
            return

        sorted_symbols = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        n_top = max(1, int(len(sorted_symbols) * self.top_pct))
        new_long = [s[0] for s in sorted_symbols[:n_top]]

        for sym in list(self._long_positions):
            if sym not in new_long:
                pos_qty = self._positions.get(sym, 0)
                if pos_qty > 0:
                    self.sell(sym, pos_qty)

        self._long_positions = new_long
        nav = context.portfolio.nav
        weight = self.max_position_pct / n_long if (n_long := len(new_long)) > 0 else 0

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

        self.logger.info(
            f"MultiFactorScore rebalanced: long={self._long_positions}, "
            f"scores={{{', '.join(f'{s}:{scores[s]:.2f}' for s in new_long)}}}"
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

        max_keep = max(self.momentum_lookback, self.rsi_period, self.volume_lookback) * 2 + 10
        if len(self._day_data[symbol]) > max_keep:
            self._day_data[symbol] = self._day_data[symbol][-max_keep // 2:]

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
        self._scores.clear()

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "long_positions": self._long_positions,
            "scores": self._scores,
            "last_rebalance_date": str(self._last_rebalance_date) if self._last_rebalance_date else None,
            "parameters": {
                "momentum_lookback": self.momentum_lookback,
                "rsi_period": self.rsi_period,
                "volume_lookback": self.volume_lookback,
                "momentum_weight": self.momentum_weight,
                "rsi_weight": self.rsi_weight,
                "volume_weight": self.volume_weight,
                "top_pct": self.top_pct,
                "holding_days": self.holding_days,
            },
        }
