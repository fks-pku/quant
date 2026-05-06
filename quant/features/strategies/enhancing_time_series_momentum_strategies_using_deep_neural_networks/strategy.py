"""Enhancing Time Series Momentum Strategies Using Deep Neural Networks

Source: arxiv (http://arxiv.org/abs/1904.04912v3)
Authors: Bryan Lim
Type: momentum
Summary: Deep Momentum Networks use LSTM-based deep learning to simultaneously learn trend estimation and position sizing within a volatility-scaling framework, achieving significant improvements over traditional time series momentum on 88 futures contracts, but require substantial deep learning expertise and carry meaningful overfitting risk.
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("DeepMomentumNetworksStrategy")
class DeepMomentumNetworksStrategy(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        short_period: int = 10,
        medium_period: int = 50,
        long_period: int = 200,
        vol_lookback: int = 21,
        target_vol: float = 0.15,
        max_position_pct: float = 0.95,
    ):
        self._symbols = symbols or ["SPY", "QQQ", "GLD"]
        self.short_period = short_period
        self.medium_period = medium_period
        self.long_period = long_period
        self.vol_lookback = vol_lookback
        self.target_vol = target_vol
        self.max_position_pct = max_position_pct

        self._conviction: Dict[str, float] = {}
        self._realized_vol: Dict[str, float] = {}

        super().__init__("DeepMomentumNetworksStrategy", self._symbols, holding_days=1)

    @property
    def _max_keep_hint(self) -> int:
        return self.long_period * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DeepMomentumNetworksStrategy")
        self.logger.info(
            "DeepMomentumNetworks starting: short=%d medium=%d long=%d vol_lookback=%d target_vol=%.2f%% symbols=%s",
            self.short_period, self.medium_period, self.long_period,
            self.vol_lookback, self.target_vol * 100, self._symbols,
        )

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        for symbol in self._symbols:
            self._process_symbol(context, symbol)

    def _process_symbol(self, context: "Context", symbol: str) -> None:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.long_period + 1:
            return

        closes = np.array([self._adj(b, "close") for b in bars])
        if len(closes) < self.long_period + 1:
            return

        short_ma = np.mean(closes[-self.short_period:])
        medium_ma = np.mean(closes[-self.medium_period:])
        long_ma = np.mean(closes[-self.long_period:])
        current_price = closes[-1]

        conviction = 0.0
        if short_ma > medium_ma:
            conviction += 0.33
        if medium_ma > long_ma:
            conviction += 0.33
        if current_price > short_ma:
            conviction += 0.33

        self._conviction[symbol] = conviction

        daily_returns = np.diff(closes[-(self.vol_lookback + 1):]) / closes[-(self.vol_lookback + 1):-1]
        realized_vol = float(np.std(daily_returns) * np.sqrt(252))
        self._realized_vol[symbol] = realized_vol

        if conviction <= 0.33 or realized_vol <= 0:
            current_pos = self._positions.get(symbol, 0)
            if current_pos > 0:
                self.sell(symbol, int(current_pos))
                self.logger.info(
                    "FLAT %s: conviction=%.2f vol=%.2f%% -> SELL %d",
                    symbol, conviction, realized_vol * 100, int(current_pos),
                )
            return

        vol_scale = min(self.target_vol / realized_vol, 2.0)
        position_fraction = conviction * vol_scale * self.max_position_pct

        nav = context.portfolio.nav
        price = self._price(bars[-1])
        if price <= 0 or nav <= 0:
            return

        target_value = nav * position_fraction
        target_qty = int(target_value / price)
        current_qty = int(self._positions.get(symbol, 0))

        delta = target_qty - current_qty
        if delta > 0:
            self.buy(symbol, delta)
            self.logger.info(
                "LONG %s: conviction=%.2f vol=%.2f%% scale=%.2f -> BUY %d (total %d) @ ~%.2f",
                symbol, conviction, realized_vol * 100, vol_scale, delta, target_qty, price,
            )
        elif delta < 0:
            self.sell(symbol, abs(delta))
            self.logger.info(
                "REDUCE %s: conviction=%.2f vol=%.2f%% scale=%.2f -> SELL %d (target %d) @ ~%.2f",
                symbol, conviction, realized_vol * 100, vol_scale, abs(delta), target_qty, price,
            )

    def _on_stop_cleanup(self) -> None:
        self._conviction.clear()
        self._realized_vol.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "short_period": self.short_period,
            "medium_period": self.medium_period,
            "long_period": self.long_period,
            "vol_lookback": self.vol_lookback,
            "target_vol": self.target_vol,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "conviction": dict(self._conviction),
            "realized_vol": dict(self._realized_vol),
        }
