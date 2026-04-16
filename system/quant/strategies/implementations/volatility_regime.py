"""Volatility Regime Strategy - Regime-based strategy switching.

This strategy detects market volatility regimes using VIX levels and switches
between momentum and mean reversion sub-strategies accordingly.

Regime Definitions:
- BULL (Low Vol): VIX SMA < 15 -> Momentum strategy
- CHOP (Medium Vol): VIX SMA 15-25 -> Mean reversion strategy
- BEAR (High Vol): VIX SMA > 25 -> Reduce exposure / defensive

Hypothesis: Different volatility regimes favor different strategies.
Low vol trending markets favor momentum. High vol range-bound markets
favor mean reversion. This regime-aware approach reduces drawdowns
and improves risk-adjusted returns.

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd
import numpy as np

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("VolatilityRegime")
class VolatilityRegime(Strategy):
    """
    Volatility regime-based strategy switching.

    Detects market regime using VIX (or VIX proxy) and allocates to:
    - Momentum sub-strategy in low volatility (bull) regime
    - Mean reversion sub-strategy in medium volatility (chop) regime
    - Reduced exposure / defensive in high volatility (bear) regime
    """

    REGIME_BULL = "bull"
    REGIME_CHOP = "chop"
    REGIME_BEAR = "bear"

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        vix_symbol: str = "^VIX",
        vix_lookback: int = 20,
        vix_bull_threshold: float = 15.0,
        vix_bear_threshold: float = 25.0,
        momentum_lookback: int = 20,
        momentum_top_n: int = 5,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        max_position_pct: float = 0.05,
        reduce_exposure_bear: float = 0.3,
    ):
        super().__init__("VolatilityRegime")
        self._symbols = symbols or ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META"]
        self.vix_symbol = vix_symbol
        self.vix_lookback = vix_lookback
        self.vix_bull_threshold = vix_bull_threshold
        self.vix_bear_threshold = vix_bear_threshold
        self.momentum_lookback = momentum_lookback
        self.momentum_top_n = momentum_top_n
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.max_position_pct = max_position_pct
        self.reduce_exposure_bear = reduce_exposure_bear

        self._current_regime = self.REGIME_CHOP
        self._vix_history: List[float] = []
        self._regime_history: List[str] = []
        self._momentum_scores: Dict[str, float] = {}
        self._rsi_values: Dict[str, float] = {}
        self._day_data: Dict[str, List] = {}
        self._positions_opened = False

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("VolatilityRegime")
        self.logger.info(
            f"VolatilityRegime starting with VIX lookback={self.vix_lookback}, "
            f"bull_thresh={self.vix_bull_threshold}, bear_thresh={self.vix_bear_threshold}"
        )

    def _load_data(self) -> None:
        pass

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        self._update_regime(context, trading_date)
        self._calculate_momentum_scores(context, trading_date)
        self._calculate_rsi_values(context, trading_date)

    def _update_regime(self, context: "Context", trading_date: date) -> None:
        if len(self._vix_history) < self.vix_lookback:
            self._current_regime = self.REGIME_CHOP
            return

        vix_sma = np.mean(self._vix_history[-self.vix_lookback:])

        if vix_sma < self.vix_bull_threshold:
            new_regime = self.REGIME_BULL
        elif vix_sma > self.vix_bear_threshold:
            new_regime = self.REGIME_BEAR
        else:
            new_regime = self.REGIME_CHOP

        if new_regime != self._current_regime:
            self.logger.info(
                f"Regime transition: {self._current_regime} -> {new_regime} "
                f"(VIX SMA: {vix_sma:.2f})"
            )
            self._current_regime = new_regime

        self._regime_history.append(self._current_regime)

    def _calculate_momentum_scores(self, context: "Context", trading_date: date) -> None:
        if self.context and hasattr(self.context, "data_provider"):
            end = datetime.combine(trading_date, datetime.max.time())
            start = end - pd.Timedelta(days=self.momentum_lookback + 30)
            try:
                for symbol in self._symbols:
                    data = self.context.data_provider.get_bars(
                        symbol, start, end, "1d"
                    )
                    if data is not None and len(data) >= self.momentum_lookback:
                        returns = data["close"].pct_change(self.momentum_lookback)
                        score = returns.iloc[-1]
                        self._momentum_scores[symbol] = score
                    else:
                        self._momentum_scores[symbol] = 0.0
            except Exception as e:
                self.logger.warning(f"Could not calculate momentum scores: {e}")

    def _calculate_rsi_values(self, context: "Context", trading_date: date) -> None:
        if self.context and hasattr(self.context, "data_provider"):
            end = datetime.combine(trading_date, datetime.max.time())
            start = end - pd.Timedelta(days=self.rsi_period * 3 + 30)
            try:
                for symbol in self._symbols:
                    data = self.context.data_provider.get_bars(
                        symbol, start, end, "1d"
                    )
                    if data is not None and len(data) >= self.rsi_period:
                        delta = data["close"].diff()
                        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
                        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
                        rs = gain / loss.replace(0, np.nan)
                        rsi = 100 - (100 / (1 + rs))
                        self._rsi_values[symbol] = rsi.iloc[-1]
                    else:
                        self._rsi_values[symbol] = 50.0
            except Exception as e:
                self.logger.warning(f"Could not calculate RSI: {e}")

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            close = data.get("close")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
            close = getattr(data, "close", None)
        else:
            return

        if not symbol or not close:
            return

        if symbol == self.vix_symbol:
            self._vix_history.append(close)
            return

        if symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []
        self._day_data[symbol].append(data)

    def execute(self, context: "Context") -> None:
        if self._positions_opened:
            return

        nav = context.portfolio.nav
        positions = {}

        if self._current_regime == self.REGIME_BULL:
            positions = self._execute_momentum(nav)
        elif self._current_regime == self.REGIME_CHOP:
            positions = self._execute_mean_reversion(nav)
        else:
            positions = self._execute_defensive(nav)

        for symbol, (direction, quantity) in positions.items():
            if quantity > 0:
                if direction == "BUY":
                    self.buy(symbol, quantity)
                else:
                    self.sell(symbol, quantity)

        self._positions_opened = True

    def _execute_momentum(self, nav: float) -> Dict[str, tuple]:
        sorted_by_momentum = sorted(
            self._momentum_scores.items(), key=lambda x: x[1], reverse=True
        )
        top_symbols = [s[0] for s in sorted_by_momentum[: self.momentum_top_n]]

        positions = {}
        weight = self.max_position_pct / len(top_symbols) if top_symbols else 0

        for symbol in top_symbols:
            price = self._get_last_price(symbol)
            if price > 0:
                quantity = int((nav * weight) / price)
                if quantity > 0:
                    positions[symbol] = ("BUY", quantity)

        return positions

    def _execute_mean_reversion(self, nav: float) -> Dict[str, tuple]:
        positions = {}

        for symbol, rsi in self._rsi_values.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue

            if rsi < self.rsi_oversold:
                weight = self.max_position_pct * 0.5
                quantity = int((nav * weight) / price)
                if quantity > 0:
                    positions[symbol] = ("BUY", quantity)
            elif rsi > self.rsi_overbought:
                weight = self.max_position_pct * 0.5
                quantity = int((nav * weight) / price)
                if quantity > 0:
                    positions[symbol] = ("SELL", quantity)

        return positions

    def _execute_defensive(self, nav: float) -> Dict[str, tuple]:
        sorted_by_momentum = sorted(
            self._momentum_scores.items(), key=lambda x: x[1], reverse=True
        )
        top_symbols = [s[0] for s in sorted_by_momentum[:3]]

        positions = {}
        reduced_weight = (self.max_position_pct * self.reduce_exposure_bear) / len(top_symbols) if top_symbols else 0

        for symbol in top_symbols:
            price = self._get_last_price(symbol)
            if price > 0:
                quantity = int((nav * reduced_weight) / price)
                if quantity > 0:
                    positions[symbol] = ("BUY", quantity)

        return positions

    def _get_last_price(self, symbol: str) -> float:
        if symbol in self._day_data and len(self._day_data[symbol]) > 0:
            last_bar = self._day_data[symbol][-1]
            if isinstance(last_bar, dict):
                return float(last_bar.get("close", 0))
            if hasattr(last_bar, "close"):
                return float(last_bar.close)
        return 0.0

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)
        self.logger.info(
            f"VolatilityRegime filled: {fill.side} {fill.quantity} {fill.symbol}"
        )

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self.execute(context)
        self._positions_opened = False

    def on_stop(self, context: "Context") -> None:
        self._vix_history.clear()
        self._regime_history.clear()
        self._momentum_scores.clear()
        self._rsi_values.clear()
        self._day_data.clear()
        self._positions_opened = False

    def get_current_regime(self) -> str:
        return self._current_regime

    def get_regime_distribution(self) -> Dict[str, float]:
        if not self._regime_history:
            return {"bull": 0.33, "chop": 0.34, "bear": 0.33}
        total = len(self._regime_history)
        return {
            "bull": self._regime_history.count(self.REGIME_BULL) / total,
            "chop": self._regime_history.count(self.REGIME_CHOP) / total,
            "bear": self._regime_history.count(self.REGIME_BEAR) / total,
        }

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "current_regime": self._current_regime,
            "regime_distribution": self.get_regime_distribution(),
            "momentum_scores": self._momentum_scores,
            "rsi_values": self._rsi_values,
            "parameters": {
                "vix_lookback": self.vix_lookback,
                "vix_bull_threshold": self.vix_bull_threshold,
                "vix_bear_threshold": self.vix_bear_threshold,
                "momentum_lookback": self.momentum_lookback,
                "momentum_top_n": self.momentum_top_n,
                "rsi_period": self.rsi_period,
                "rsi_oversold": self.rsi_oversold,
                "rsi_overbought": self.rsi_overbought,
            },
        }
