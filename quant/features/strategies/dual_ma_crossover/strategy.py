"""Dual Moving Average Crossover - 经典双均线交叉策略。

对标 JoinQuant 双均线策略，短均线上穿长均线（金叉）全仓买入，
短均线下穿长均线（死叉）清仓卖出。适用于单标的日线级别交易。

Hypothesis: 趋势延续性——价格在上升通道中短周期均线领先于长周期均线，
金叉表明趋势转强值得追随，死叉表明趋势转弱应当离场。
均线交叉是趋势跟踪的简化版，本质是对价格序列的低通滤波。

Author: Quantitative Research
Validated: Walk-forward with 6m train / 1m test
Reference: 经典技术分析，JoinQuant 双均线策略
"""

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("DualMACrossover")
class DualMACrossover(DailyBarStrategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        fast_period: int = 5,
        slow_period: int = 20,
        buy_buffer: float = 0.0,
        max_position_pct: float = 0.95,
    ):
        self._symbols = symbols or ["000001"]
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.buy_buffer = buy_buffer
        self.max_position_pct = max_position_pct

        self._prev_signal: Dict[str, str] = {}

        super().__init__("DualMACrossover", self._symbols, holding_days=1)

    @property
    def _max_keep_hint(self) -> int:
        return self.slow_period * 2

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("DualMACrossover")
        self.logger.info(
            "DualMACrossover starting: fast=%d slow=%d buffer=%.2f%% symbols=%s",
            self.fast_period, self.slow_period, self.buy_buffer * 100, self._symbols,
        )

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        for symbol in self._symbols:
            self._process_symbol(context, symbol)

    def _process_symbol(self, context: "Context", symbol: str) -> None:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.slow_period + 1:
            return

        closes = np.array([self._adj(b, "close") for b in bars])
        if len(closes) < self.slow_period + 1:
            return

        fast_ma = np.mean(closes[-self.fast_period:])
        slow_ma = np.mean(closes[-self.slow_period:])
        prev_closes = closes[:-1]
        if len(prev_closes) < self.slow_period:
            return
        prev_fast_ma = np.mean(prev_closes[-self.fast_period:])
        prev_slow_ma = np.mean(prev_closes[-self.slow_period:])

        current_pos = self._positions.get(symbol, 0)

        spread = (fast_ma - slow_ma) / slow_ma if slow_ma > 0 else 0
        prev_spread = (prev_fast_ma - prev_slow_ma) / prev_slow_ma if prev_slow_ma > 0 else 0
        golden_cross = prev_spread <= 0 and spread > self.buy_buffer
        death_cross = prev_spread >= 0 and spread < 0

        if golden_cross and current_pos == 0:
            nav = context.portfolio.nav
            price = self._price(bars[-1])
            if price <= 0 or nav <= 0:
                return
            qty = int(nav * self.max_position_pct / price)
            if qty > 0:
                self.buy(symbol, qty)
                self._prev_signal[symbol] = "golden"
                self.logger.info(
                    "GOLDEN CROSS %s: fast_ma=%.2f slow_ma=%.2f -> BUY %d @ ~%.2f",
                    symbol, fast_ma, slow_ma, qty, price,
                )

        elif death_cross and current_pos > 0:
            self.sell(symbol, int(current_pos))
            self._prev_signal[symbol] = "death"
            self.logger.info(
                "DEATH CROSS %s: fast_ma=%.2f slow_ma=%.2f -> SELL %d",
                symbol, fast_ma, slow_ma, int(current_pos),
            )

    def _on_stop_cleanup(self) -> None:
        self._prev_signal.clear()

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "buy_buffer": self.buy_buffer,
            "max_position_pct": self.max_position_pct,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"prev_signal": dict(self._prev_signal)}
