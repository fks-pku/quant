"""Power Assisted Trend Following

Source: arxiv (http://arxiv.org/abs/2003.09298v1)
Authors: Andreas A. Aigner
Type: momentum
Summary: A daily-bar trend following strategy that applies digital signal processing
to filter market noise from directional trends, leveraging Welles Wilder's directional
movement indicators (ADX/DMI) to measure trend power. Only follows trends when
directional power exceeds a threshold.
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("PowerAssistedTrendFollowingStrategy")
class PowerAssistedTrendFollowingStrategy(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        dm_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        max_position_pct: float = 0.95,
    ):
        self._symbols = symbols or ["SPY", "GLD", "TLT"]
        self.dm_period = dm_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.max_position_pct = max_position_pct

        self._prev_adx: Dict[str, float] = {}
        self._prev_plus_di: Dict[str, float] = {}
        self._prev_minus_di: Dict[str, float] = {}
        self._atr_values: Dict[str, float] = {}

        super().__init__(
            "PowerAssistedTrendFollowingStrategy", self._symbols, holding_days=1,
        )

    @property
    def _max_keep_hint(self) -> int:
        return max(self.dm_period, self.adx_period) * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("PowerAssistedTrendFollowing")
        self.logger.info(
            "PowerAssistedTrendFollowing starting: dm=%d adx=%d threshold=%.1f symbols=%s",
            self.dm_period, self.adx_period, self.adx_threshold, self._symbols,
        )

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        for symbol in self._symbols:
            self._process_symbol(context, symbol)

    def _wilder_smooth(self, values: np.ndarray, period: int) -> np.ndarray:
        if len(values) < period:
            return np.array([])
        k = 1.0 / period
        result = np.empty(len(values))
        result[:period] = np.nan
        result[period - 1] = np.mean(values[:period])
        for i in range(period, len(values)):
            result[i] = result[i - 1] * (1 - k) + values[i] * k
        return result

    def _compute_dmi(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
    ) -> tuple:
        n = len(highs)
        period = self.dm_period
        if n < period + 2:
            return None

        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
            minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0

        smoothed_tr = self._wilder_smooth(tr, period)
        smoothed_plus_dm = self._wilder_smooth(plus_dm, period)
        smoothed_minus_dm = self._wilder_smooth(minus_dm, period)

        valid = ~np.isnan(smoothed_tr) & (smoothed_tr > 0)
        if not np.any(valid):
            return None

        plus_di = np.where(valid, smoothed_plus_dm / smoothed_tr * 100.0, 0.0)
        minus_di = np.where(valid, smoothed_minus_dm / smoothed_tr * 100.0, 0.0)

        di_sum = plus_di + minus_di
        di_diff = np.abs(plus_di - minus_di)
        dx = np.zeros(n)
        for i in range(n):
            if valid[i] and di_sum[i] > 0:
                dx[i] = di_diff[i] / di_sum[i] * 100.0

        first_valid = period - 1
        dx_valid = dx[first_valid:]
        if len(dx_valid) < self.adx_period:
            return None

        adx_smoothed = self._wilder_smooth(dx_valid, self.adx_period)
        if len(adx_smoothed) == 0:
            return None

        adx_val = adx_smoothed[-1]
        if np.isnan(adx_val):
            return None

        atr = smoothed_tr[-1] / period

        return plus_di[-1], minus_di[-1], adx_val, atr

    def _process_symbol(self, context: "Context", symbol: str) -> None:
        bars = self._day_data.get(symbol, [])
        min_bars = self.dm_period + 2
        if len(bars) < min_bars:
            return

        highs = np.array([self._adj(b, "high") for b in bars])
        lows = np.array([self._adj(b, "low") for b in bars])
        closes = np.array([self._adj(b, "close") for b in bars])

        result = self._compute_dmi(highs, lows, closes)
        if result is None:
            return

        plus_di, minus_di, adx, atr = result

        current_pos = self._positions.get(symbol, 0)
        nav = context.portfolio.nav
        price = self._price(bars[-1])

        if price <= 0 or nav <= 0:
            return

        prev_plus_di = self._prev_plus_di.get(symbol)
        prev_minus_di = self._prev_minus_di.get(symbol)
        prev_adx = self._prev_adx.get(symbol)

        self._prev_plus_di[symbol] = plus_di
        self._prev_minus_di[symbol] = minus_di
        self._prev_adx[symbol] = adx
        self._atr_values[symbol] = atr

        bullish_cross = (
            prev_plus_di is not None
            and prev_plus_di <= prev_minus_di
            and plus_di > minus_di
        )
        bearish_cross = (
            prev_plus_di is not None
            and prev_plus_di >= prev_minus_di
            and plus_di < minus_di
        )

        trend_strong = adx > self.adx_threshold
        trend_weakening = prev_adx is not None and adx < self.adx_threshold

        if bullish_cross and trend_strong and current_pos == 0:
            if atr > 0:
                dollar_vol = atr * price
                if dollar_vol > 0:
                    qty = int(nav * self.max_position_pct * atr / (dollar_vol * 2))
                else:
                    qty = int(nav * self.max_position_pct / price)
            else:
                qty = int(nav * self.max_position_pct / price)
            qty = max(1, qty)
            self.buy(symbol, qty)
            self.logger.info(
                "LONG %s: +DI=%.1f -DI=%.1f ADX=%.1f -> BUY %d @ ~%.2f",
                symbol, plus_di, minus_di, adx, qty, price,
            )

        elif current_pos > 0 and (bearish_cross or trend_weakening):
            self.sell(symbol, int(current_pos))
            reason = "bearish cross" if bearish_cross else "ADX weakening"
            self.logger.info(
                "CLOSE %s: +DI=%.1f -DI=%.1f ADX=%.1f (%s) -> SELL %d",
                symbol, plus_di, minus_di, adx, reason, int(current_pos),
            )

    def _on_stop_cleanup(self) -> None:
        self._prev_adx.clear()
        self._prev_plus_di.clear()
        self._prev_minus_di.clear()
        self._atr_values.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "dm_period": self.dm_period,
            "adx_period": self.adx_period,
            "adx_threshold": self.adx_threshold,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "prev_adx": dict(self._prev_adx),
            "prev_plus_di": dict(self._prev_plus_di),
            "prev_minus_di": dict(self._prev_minus_di),
            "atr_values": dict(self._atr_values),
        }
