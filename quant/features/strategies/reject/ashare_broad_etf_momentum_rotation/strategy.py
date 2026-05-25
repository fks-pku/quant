"""CN-listed broad ETF risk-adjusted momentum rotation."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


DEFAULT_BROAD_ETF_SYMBOLS = [
    "510050",
    "510300",
    "510500",
    "512100",
    "159915",
    "159949",
    "510880",
    "518880",
    "513100",
    "159920",
]


@strategy("ashare_broad_etf_momentum_rotation")
class AShareBroadEtfMomentumRotationStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        momentum_lookback: int = 180,
        trend_window: int = 120,
        volatility_window: int = 60,
        liquidity_window: int = 20,
        min_avg_turnover: float = 50_000_000.0,
        max_positions: int = 2,
        target_exposure: float = 0.70,
        lot_size: int = 100,
        holding_days: int = 60,
    ):
        trade_symbols = [str(symbol) for symbol in (symbols or DEFAULT_BROAD_ETF_SYMBOLS)]
        super().__init__(
            "ashare_broad_etf_momentum_rotation",
            list(dict.fromkeys(trade_symbols)),
            holding_days=holding_days,
        )
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.trend_window = max(2, int(trend_window))
        self.volatility_window = max(2, int(volatility_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_avg_turnover = float(min_avg_turnover)
        self.max_positions = max(1, int(max_positions))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self._risk_exited_today: set[str] = set()
        self._last_scores: Dict[str, float] = {}
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected": [],
            "entry_rejections": {},
            "exit_triggers": {},
        }

    @property
    def _max_keep_hint(self) -> int:
        return max(self.momentum_lookback, self.trend_window, self.volatility_window, self.liquidity_window) + 5

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._risk_exited_today = self._exit_invalid_positions(trading_date)
        if not self._check_rebalance_gate(trading_date):
            return
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        selected = self._select_targets(trading_date, excluded=self._risk_exited_today)
        selected_set = set(selected)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(selected)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        if not selected:
            return
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_value = nav * self.target_exposure / float(len(selected))
        for symbol in selected:
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = self._round_lot(target_value / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)

    def _exit_invalid_positions(self, trading_date: date) -> set[str]:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            reason = self._position_exit_reason(symbol, trading_date)
            if not reason:
                continue
            self._count("exit_triggers", reason)
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _select_targets(self, trading_date: date, excluded: set[str]) -> List[str]:
        scored: List[Tuple[float, str]] = []
        for symbol in self._symbols:
            if symbol in excluded:
                continue
            reason = self._candidate_rejection(symbol, trading_date)
            if reason:
                self._count("entry_rejections", reason)
                continue
            score = self._risk_adjusted_momentum(symbol)
            if score is None or score <= 0:
                self._count("entry_rejections", "non_positive_score")
                continue
            scored.append((score, symbol))
        self._diagnostics["last_candidate_count"] = len(scored)
        self._last_scores = {symbol: score for score, symbol in scored}
        return [symbol for _, symbol in sorted(scored, key=lambda item: (-item[0], item[1]))[: self.max_positions]]

    def _candidate_rejection(self, symbol: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if self._avg_turnover(symbol) < self.min_avg_turnover:
            return "low_turnover"
        if not self._above_trend(symbol):
            return "below_trend"
        return ""

    def _position_exit_reason(self, symbol: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if not self._above_trend(symbol):
            return "below_trend"
        score = self._risk_adjusted_momentum(symbol)
        if score is None or score <= 0:
            return "non_positive_score"
        return ""

    def _risk_adjusted_momentum(self, symbol: str) -> Optional[float]:
        momentum = self._return(symbol, self.momentum_lookback)
        volatility = self._volatility(symbol, self.volatility_window)
        if momentum is None or volatility is None or volatility <= 0:
            return None
        return momentum / volatility

    def _above_trend(self, symbol: str) -> bool:
        closes = self._valid_adj_closes(symbol, self.trend_window)
        if len(closes) < self.trend_window:
            return False
        return closes[-1] > sum(closes) / float(len(closes))

    def _return(self, symbol: str, lookback: int) -> Optional[float]:
        closes = self._valid_adj_closes(symbol, lookback + 1)
        if len(closes) < lookback + 1 or closes[0] <= 0:
            return None
        return closes[-1] / closes[0] - 1.0

    def _volatility(self, symbol: str, lookback: int) -> Optional[float]:
        closes = self._valid_adj_closes(symbol, lookback + 1)
        if len(closes) < lookback + 1:
            return None
        returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
        if len(returns) < 2:
            return None
        mean = sum(returns) / float(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / float(len(returns) - 1)
        volatility = math.sqrt(max(variance, 0.0)) * math.sqrt(252.0)
        return volatility if math.isfinite(volatility) and volatility > 0 else None

    def _avg_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_window :]
        values = [self._cash_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if len(values) < self.liquidity_window:
            return 0.0
        return sum(values) / float(len(values))

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

    def _valid_adj_closes(self, symbol: str, count: int) -> List[float]:
        bars = self._day_data.get(symbol, [])
        values = []
        for bar in bars:
            try:
                value = float(self._adj(bar, "close"))
            except (TypeError, ValueError):
                continue
            if value > 0 and math.isfinite(value):
                values.append(value)
        return values[-count:]

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._diagnostics.get("exit_triggers") or {}),
            "last_scores": dict(self._last_scores),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbols": list(self._symbols),
            "momentum_lookback": self.momentum_lookback,
            "trend_window": self.trend_window,
            "volatility_window": self.volatility_window,
            "liquidity_window": self.liquidity_window,
            "min_avg_turnover": self.min_avg_turnover,
            "max_positions": self.max_positions,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "holding_days": self.holding_days,
        }

    def _count(self, bucket: str, key: str) -> None:
        values = self._diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @classmethod
    def _is_current_bar(cls, bar: Any, trading_date: date) -> bool:
        value = cls._bar_value(bar, "timestamp")
        if value is None:
            return False
        if hasattr(value, "date"):
            return value.date() == trading_date
        return str(value)[:10] == trading_date.isoformat()

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
        return number if math.isfinite(number) else None
