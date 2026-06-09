"""CN-listed sector ETF risk-adjusted momentum rotation."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


STRATEGY_NAME = "ashare_sector_etf_momentum_rotation"
DEFAULT_SECTOR_CATEGORY_SYMBOLS = {
    "brokerage": ["512880", "512000"],
    "bank": ["512800"],
    "semiconductor": ["512480", "159995"],
    "military": ["512660"],
    "consumer": ["159928"],
    "liquor": ["512690"],
    "healthcare": ["512170", "512010"],
    "new_energy": ["516160"],
    "new_energy_vehicle": ["515030", "159806"],
    "photovoltaic": ["515790"],
    "nonferrous": ["512400"],
    "coal": ["515220"],
    "real_estate": ["512200", "515060"],
    "media": ["512980"],
    "communication": ["515880"],
    "computer": ["512720"],
    "ai": ["159819"],
    "cloud": ["516510"],
    "infrastructure": ["516950", "516970"],
    "chemical": ["516020"],
    "agriculture": ["159825", "159865"],
    "tourism": ["159766"],
    "game": ["159869"],
}


@strategy(STRATEGY_NAME)
class AShareSectorEtfMomentumRotationStrategy(DailyBarStrategy):
    def __init__(
        self,
        category_symbols: Optional[Dict[str, List[str]]] = None,
        momentum_lookback: int = 60,
        momentum_skip: int = 1,
        trend_window: int = 60,
        volatility_window: int = 20,
        liquidity_window: int = 20,
        min_avg_turnover: float = 20_000_000.0,
        min_momentum: float = 0.02,
        max_positions: int = 3,
        max_category_weight: float = 0.35,
        target_exposure: float = 0.95,
        lot_size: int = 100,
        holding_days: int = 20,
        volatility_floor: float = 0.01,
    ):
        self.category_symbols = self._normalize_category_symbols(category_symbols or DEFAULT_SECTOR_CATEGORY_SYMBOLS)
        super().__init__(STRATEGY_NAME, self._flatten_category_symbols(self.category_symbols), holding_days=holding_days)
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.trend_window = max(2, int(trend_window))
        self.volatility_window = max(2, int(volatility_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_avg_turnover = float(min_avg_turnover)
        self.min_momentum = float(min_momentum)
        self.max_positions = max(1, int(max_positions))
        self.max_category_weight = min(max(float(max_category_weight), 0.0), 1.0)
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.volatility_floor = max(0.0, float(volatility_floor))
        self._risk_exited_today: set[str] = set()
        self._last_scores: Dict[str, float] = {}
        self._last_category_scores: Dict[str, float] = {}
        self._last_target_weights: Dict[str, float] = {}
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected": [],
            "last_selected_categories": [],
            "last_target_weights": {},
            "entry_rejections": {},
            "exit_triggers": {},
        }

    @property
    def _max_keep_hint(self) -> int:
        return max(
            self.momentum_lookback + self.momentum_skip,
            self.trend_window,
            self.volatility_window,
            self.liquidity_window,
        ) + 5

    def required_snapshot_symbols(self) -> List[str]:
        return []

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._risk_exited_today = self._exit_invalid_positions(trading_date)
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> bool:
        selected = self._select_targets(trading_date, excluded=self._risk_exited_today)
        selected_symbols = [symbol for _, symbol, _ in selected]
        selected_set = set(selected_symbols)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(selected_symbols)
        self._diagnostics["last_selected_categories"] = [category for category, _, _ in selected]

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        if not selected_symbols:
            self._remember_target_weights({})
            return False
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            self._remember_target_weights({})
            return False
        target_weights = self._target_weights(selected_symbols)
        for symbol, weight in target_weights.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = self._round_lot(nav * weight / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)
        return True

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

    def _select_targets(self, trading_date: date, excluded: set[str]) -> List[Tuple[str, str, float]]:
        category_winners: List[Tuple[str, str, float]] = []
        for category, symbols in self.category_symbols.items():
            category_scores = []
            for symbol in symbols:
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
                category_scores.append((score, symbol))
            if category_scores:
                score, symbol = max(category_scores, key=lambda item: (item[0], item[1]))
                category_winners.append((category, symbol, score))
        self._diagnostics["last_candidate_count"] = len(category_winners)
        self._last_scores = {symbol: score for _, symbol, score in category_winners}
        self._last_category_scores = {category: score for category, _, score in category_winners}
        selected = sorted(category_winners, key=lambda item: (-item[2], item[0], item[1]))[: self.max_positions]
        return selected

    def _candidate_rejection(self, symbol: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if self._avg_turnover(symbol) < self.min_avg_turnover:
            return "low_turnover"
        if not self._above_trend(symbol):
            return "below_trend"
        momentum = self._momentum(symbol)
        if momentum is None:
            return "missing_momentum"
        if momentum < self.min_momentum:
            return "weak_momentum"
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

    def _target_weights(self, selected_symbols: List[str]) -> Dict[str, float]:
        if not selected_symbols:
            return {}
        raw_weight = self.target_exposure / float(len(selected_symbols))
        weight = min(raw_weight, self.max_category_weight)
        weights = {symbol: weight for symbol in selected_symbols}
        self._remember_target_weights(weights)
        return weights

    def _risk_adjusted_momentum(self, symbol: str) -> Optional[float]:
        momentum = self._momentum(symbol)
        volatility = self._volatility(symbol, self.volatility_window)
        if momentum is None or volatility is None:
            return None
        volatility = max(volatility, self.volatility_floor)
        if volatility <= 0:
            return None
        score = momentum / volatility
        return score if math.isfinite(score) else None

    def _momentum(self, symbol: str) -> Optional[float]:
        closes = self._valid_adj_closes(symbol, self.momentum_lookback + self.momentum_skip + 1)
        needed = self.momentum_lookback + self.momentum_skip + 1
        if len(closes) < needed:
            return None
        end_index = -1 - self.momentum_skip if self.momentum_skip > 0 else -1
        current = closes[end_index]
        base = closes[end_index - self.momentum_lookback]
        if base <= 0 or current <= 0:
            return None
        return current / base - 1.0

    def _above_trend(self, symbol: str) -> bool:
        closes = self._valid_adj_closes(symbol, self.trend_window)
        if len(closes) < self.trend_window:
            return False
        return closes[-1] > sum(closes) / float(len(closes))

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
        values = []
        for bar in self._day_data.get(symbol, []):
            try:
                value = float(self._adj(bar, "close"))
            except (TypeError, ValueError):
                continue
            if value > 0 and math.isfinite(value):
                values.append(value)
        return values[-count:]

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _remember_target_weights(self, weights: Dict[str, float]) -> None:
        self._last_target_weights = dict(weights)
        self._diagnostics["last_target_weights"] = dict(weights)

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._diagnostics.get("exit_triggers") or {}),
            "last_scores": dict(self._last_scores),
            "last_category_scores": dict(self._last_category_scores),
            "last_target_weights": dict(self._last_target_weights),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "category_symbols": {key: list(value) for key, value in self.category_symbols.items()},
            "momentum_lookback": self.momentum_lookback,
            "momentum_skip": self.momentum_skip,
            "trend_window": self.trend_window,
            "volatility_window": self.volatility_window,
            "liquidity_window": self.liquidity_window,
            "min_avg_turnover": self.min_avg_turnover,
            "min_momentum": self.min_momentum,
            "max_positions": self.max_positions,
            "max_category_weight": self.max_category_weight,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "holding_days": self.holding_days,
            "volatility_floor": self.volatility_floor,
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
        return str(value)[:10] == str(trading_date)

    @staticmethod
    def _bar_value(bar: Any, key: str, default: Any = None) -> Any:
        if isinstance(bar, dict):
            return bar.get(key, default)
        return getattr(bar, key, default)

    @classmethod
    def _numeric_bar_value(cls, bar: Any, key: str) -> Optional[float]:
        value = cls._bar_value(bar, key)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _normalize_category_symbols(category_symbols: Dict[str, List[str]]) -> Dict[str, List[str]]:
        normalized: Dict[str, List[str]] = {}
        for category, symbols in category_symbols.items():
            values = [str(symbol) for symbol in symbols if str(symbol)]
            if values:
                normalized[str(category)] = list(dict.fromkeys(values))
        return normalized

    @staticmethod
    def _flatten_category_symbols(category_symbols: Dict[str, List[str]]) -> List[str]:
        values = []
        for symbols in category_symbols.values():
            values.extend(str(symbol) for symbol in symbols)
        return sorted(set(values))
