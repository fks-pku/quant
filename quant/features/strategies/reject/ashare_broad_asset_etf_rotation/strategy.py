"""A-share broad asset ETF rotation candidate."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


STRATEGY_NAME = "ashare_broad_asset_etf_rotation"
DEFAULT_CATEGORY_SYMBOLS = {
    "sse50": ["510050"],
    "csi300": ["510300"],
    "csi1000": ["512100"],
    "chinext": ["159915"],
    "chinext50": ["159949"],
    "dividend": ["510880"],
    "gold": ["518880"],
    "cash": ["511990"],
    "bond_rate": ["511010"],
}
DEFAULT_PIT_SIZE_FIELDS = ("total_netasset", "net_asset", "fund_size", "aum", "total_net_asset", "net_assets")


@strategy(STRATEGY_NAME)
class AShareBroadAssetEtfRotationStrategy(DailyBarStrategy):
    def __init__(
        self,
        category_symbols: Optional[Dict[str, List[str]]] = None,
        momentum_lookback: int = 126,
        momentum_skip: int = 1,
        trend_window: int = 120,
        volatility_window: int = 60,
        liquidity_window: int = 20,
        min_avg_turnover: float = 20_000_000.0,
        max_positions: int = 3,
        max_positions_per_category: int = 1,
        target_exposure: float = 0.98,
        holding_days: int = 20,
        lot_size: int = 100,
        require_pit_size: bool = True,
        volatility_floor: float = 0.01,
        pit_size_fields: Optional[List[str]] = None,
    ):
        self.category_symbols = self._normalize_category_symbols(category_symbols or DEFAULT_CATEGORY_SYMBOLS)
        symbols = self._flatten_category_symbols(self.category_symbols)
        super().__init__(STRATEGY_NAME, symbols, holding_days=holding_days)
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.trend_window = max(2, int(trend_window))
        self.volatility_window = max(2, int(volatility_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_avg_turnover = float(min_avg_turnover)
        self.max_positions = max(1, int(max_positions))
        self.max_positions_per_category = max(1, int(max_positions_per_category))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.require_pit_size = bool(require_pit_size)
        self.volatility_floor = max(0.0, float(volatility_floor))
        self.pit_size_fields = tuple(str(field) for field in (pit_size_fields or DEFAULT_PIT_SIZE_FIELDS))
        self._last_scores: Dict[str, float] = {}
        self._last_visible_by_category: Dict[str, List[str]] = {}
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected": [],
            "last_visible_by_category": {},
            "entry_rejections": {},
        }

    @property
    def _max_keep_hint(self) -> int:
        return max(
            self.momentum_lookback + self.momentum_skip,
            self.trend_window,
            self.volatility_window,
            self.liquidity_window,
        ) + (260 if self.require_pit_size else 5)

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> bool:
        selected = self._select_targets(trading_date)
        selected_set = set(selected)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(selected)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._get_last_price(symbol)
                sell_quantity = int(quantity)
                if sell_quantity > 0:
                    self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)

        if not selected:
            return False
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return False
        target_value = nav * self.target_exposure / float(len(selected))
        submitted = False
        for symbol in selected:
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = self._round_lot(target_value / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
                submitted = True
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)
                submitted = True
        return submitted or bool(selected)

    def _select_targets(self, trading_date: date) -> List[str]:
        selected = []
        category_counts: Dict[str, int] = {}
        scored: List[Tuple[float, str, str]] = []
        visible = self._visible_symbols_by_category(trading_date)
        for category, symbols in visible.items():
            for symbol in symbols:
                reason = self._candidate_rejection(symbol, trading_date)
                if reason:
                    self._count("entry_rejections", reason)
                    continue
                score = self._risk_adjusted_momentum(symbol)
                if score is None or score <= 0:
                    self._count("entry_rejections", "non_positive_score")
                    continue
                scored.append((score, category, symbol))
        self._last_scores = {symbol: score for score, _, symbol in scored}
        self._diagnostics["last_candidate_count"] = len(scored)
        for _, category, symbol in sorted(scored, key=lambda item: (-item[0], item[2])):
            if len(selected) >= self.max_positions:
                break
            if category_counts.get(category, 0) >= self.max_positions_per_category:
                self._count("entry_rejections", "category_cap")
                continue
            selected.append(symbol)
            category_counts[category] = category_counts.get(category, 0) + 1
        return selected

    def _visible_symbols_by_category(self, trading_date: date) -> Dict[str, List[str]]:
        visible_by_category: Dict[str, List[str]] = {}
        for category, symbols in self.category_symbols.items():
            visible = []
            for symbol in symbols:
                bar = self._get_last_bar(symbol)
                if not bar or not self._is_current_bar(bar, trading_date):
                    self._count("entry_rejections", "stale_or_missing_bar")
                    continue
                if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
                    self._count("entry_rejections", "missing_pit_size")
                    continue
                visible.append(symbol)
            visible_by_category[category] = list(dict.fromkeys(visible))
        self._last_visible_by_category = {key: list(value) for key, value in visible_by_category.items()}
        self._diagnostics["last_visible_by_category"] = {
            key: list(value)
            for key, value in visible_by_category.items()
        }
        return visible_by_category

    def _candidate_rejection(self, symbol: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if self._avg_turnover(symbol) < self.min_avg_turnover:
            return "low_turnover"
        momentum = self._momentum(symbol)
        if momentum is None:
            return "missing_momentum"
        if momentum <= 0.0:
            return "weak_momentum"
        if not self._above_trend(symbol):
            return "below_trend"
        if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
            return "missing_pit_size"
        return ""

    def _risk_adjusted_momentum(self, symbol: str) -> Optional[float]:
        momentum = self._momentum(symbol)
        volatility = self._volatility(symbol, self.volatility_window)
        if momentum is None:
            return None
        adjusted_volatility = max(volatility or 0.0, self.volatility_floor)
        if adjusted_volatility <= 0:
            return None
        score = momentum / adjusted_volatility
        return score if math.isfinite(score) else None

    def _momentum(self, symbol: str) -> Optional[float]:
        needed = self.momentum_lookback + self.momentum_skip + 1
        closes = self._valid_adj_closes(symbol, needed)
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
        close = self._numeric_bar_value(bar, "raw_close") or self._numeric_bar_value(bar, "close") or 0.0
        volume = self._numeric_bar_value(bar, "raw_volume") or self._numeric_bar_value(bar, "volume") or 0.0
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

    def _pit_size(self, symbol: str, trading_date: date) -> Optional[float]:
        for bar in reversed(self._day_data.get(symbol, [])):
            bar_date = self._bar_date(bar)
            if bar_date is not None and bar_date > trading_date:
                continue
            for field in self.pit_size_fields:
                value = self._numeric_bar_value(bar, field)
                if value is not None and value > 0:
                    return value
        return None

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "last_visible_by_category": {
                key: list(value)
                for key, value in self._last_visible_by_category.items()
            },
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "last_scores": dict(self._last_scores),
            "parameters": self._get_parameters(),
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
            "max_positions": self.max_positions,
            "max_positions_per_category": self.max_positions_per_category,
            "target_exposure": self.target_exposure,
            "holding_days": self.holding_days,
            "lot_size": self.lot_size,
            "require_pit_size": self.require_pit_size,
            "volatility_floor": self.volatility_floor,
            "pit_size_fields": list(self.pit_size_fields),
        }

    def _count(self, bucket: str, key: str) -> None:
        values = self._diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @classmethod
    def _is_current_bar(cls, bar: Any, trading_date: date) -> bool:
        bar_date = cls._bar_date(bar)
        return bar_date == trading_date

    @classmethod
    def _bar_date(cls, bar: Any) -> Optional[date]:
        value = cls._bar_value(bar, "timestamp")
        if value is None:
            return None
        try:
            return value.date()
        except AttributeError:
            pass
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

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

    @staticmethod
    def _normalize_category_symbols(category_symbols: Dict[str, List[str]]) -> Dict[str, List[str]]:
        return {
            str(category): list(dict.fromkeys(str(symbol) for symbol in symbols))
            for category, symbols in category_symbols.items()
            if symbols
        }

    @staticmethod
    def _flatten_category_symbols(category_symbols: Dict[str, List[str]]) -> List[str]:
        values = []
        for symbols in category_symbols.values():
            values.extend(str(symbol) for symbol in symbols)
        return list(dict.fromkeys(values))
