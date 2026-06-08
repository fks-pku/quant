"""A-share broad asset ETF rotation strategy."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
        max_positions: Optional[int] = None,
        max_positions_per_category: Optional[int] = None,
        target_exposure: float = 1.0,
        holding_days: int = 20,
        lot_size: int = 100,
        require_pit_size: bool = True,
        volatility_floor: float = 0.01,
        pit_size_fields: Optional[List[str]] = None,
        weight_mode: str = "continuous_branch_tilt",
        tilt_strength: float = 0.70,
        temperature: float = 0.75,
        min_branch_weight: float = 0.02,
        max_branch_weight: float = 0.30,
        rebalance_threshold: float = 0.02,
        trend_penalty: float = 1.0,
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
        self.max_positions = None if max_positions is None else max(1, int(max_positions))
        self.max_positions_per_category = (
            None if max_positions_per_category is None else max(1, int(max_positions_per_category))
        )
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.require_pit_size = bool(require_pit_size)
        self.volatility_floor = max(0.0, float(volatility_floor))
        self.pit_size_fields = tuple(str(field) for field in (pit_size_fields or DEFAULT_PIT_SIZE_FIELDS))
        self.weight_mode = str(weight_mode)
        self.tilt_strength = min(max(float(tilt_strength), 0.0), 1.0)
        self.temperature = max(float(temperature), 1e-6)
        self.min_branch_weight = max(0.0, float(min_branch_weight))
        self.max_branch_weight = min(max(float(max_branch_weight), 0.0), 1.0)
        self.rebalance_threshold = max(0.0, float(rebalance_threshold))
        self.trend_penalty = max(0.0, float(trend_penalty))
        self._last_scores: Dict[str, float] = {}
        self._last_branch_scores: Dict[str, float] = {}
        self._last_target_weights: Dict[str, float] = {}
        self._last_branch_weights: Dict[str, float] = {}
        self._last_visible_by_category: Dict[str, List[str]] = {}
        self._last_actual_cash_weight = 1.0
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected": [],
            "last_target_weights": {},
            "last_branch_weights": {},
            "last_actual_cash_weight": 1.0,
            "last_weight_sum": 1.0,
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

    def required_snapshot_symbols(self) -> List[str]:
        return []

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> bool:
        target_weights = self._target_symbol_weights(trading_date)
        selected_set = set(target_weights)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(target_weights)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return False
        submitted = False

        target_quantities: Dict[str, int] = {}
        for symbol, weight in target_weights.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantities[symbol] = self._round_lot(nav * weight / price)

        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = target_quantities.get(symbol, 0)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            current_weight = current_quantity * price / nav
            target_weight = target_weights.get(symbol, 0.0)
            if delta < 0 and (target_quantity == 0 or abs(target_weight - current_weight) >= self.rebalance_threshold):
                self.sell(symbol, abs(delta), "MARKET", price)
                submitted = True

        for symbol, target_quantity in target_quantities.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            current_weight = current_quantity * price / nav
            target_weight = target_weights.get(symbol, 0.0)
            if delta > 0 and abs(target_weight - current_weight) >= self.rebalance_threshold:
                self.buy(symbol, delta, "MARKET", price)
                submitted = True
        return submitted or bool(target_weights)

    def _target_symbol_weights(self, trading_date: date) -> Dict[str, float]:
        scored: List[Dict[str, Any]] = []
        visible = self._visible_symbols_by_category(trading_date)
        for category, symbols in visible.items():
            category_candidates = []
            for symbol in symbols:
                reason = self._candidate_rejection(symbol, trading_date)
                if reason:
                    self._count("entry_rejections", reason)
                    continue
                score = self._branch_score(symbol)
                if score is None:
                    self._count("entry_rejections", "missing_score")
                    continue
                category_candidates.append({"category": category, "symbol": symbol, "score": score})
            if category_candidates:
                scored.append(max(category_candidates, key=lambda item: (item["score"], item["symbol"])))
        self._last_scores = {str(item["symbol"]): float(item["score"]) for item in scored}
        self._last_branch_scores = {str(item["category"]): float(item["score"]) for item in scored}
        self._diagnostics["last_candidate_count"] = len(scored)
        if not scored:
            self._remember_target_weights({}, {})
            return {}
        branch_weights = self._branch_weights(scored)
        symbol_weights = {
            str(item["symbol"]): branch_weights[str(item["category"])] * self.target_exposure
            for item in scored
            if branch_weights.get(str(item["category"]), 0.0) > 0
        }
        self._remember_target_weights(symbol_weights, branch_weights)
        return symbol_weights

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
        if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
            return "missing_pit_size"
        return ""

    def _branch_score(self, symbol: str) -> Optional[float]:
        score = self._risk_adjusted_momentum(symbol)
        if score is None:
            return None
        if not self._above_trend(symbol):
            score -= self.trend_penalty
        return score if math.isfinite(score) else None

    def _branch_weights(self, scored: List[Dict[str, Any]]) -> Dict[str, float]:
        categories = [str(item["category"]) for item in scored]
        scores = [float(item["score"]) for item in scored]
        if not scores:
            return {}
        mean = sum(scores) / float(len(scores))
        variance = sum((score - mean) ** 2 for score in scores) / float(len(scores))
        std = math.sqrt(max(variance, 0.0))
        if std <= 1e-12:
            raw = {category: 1.0 / float(len(categories)) for category in categories}
        else:
            exps = []
            for score in scores:
                z_score = min(max((score - mean) / std, -2.0), 2.0)
                exps.append(math.exp(z_score / self.temperature))
            total_exp = sum(exps)
            tilted = {
                category: exps[index] / total_exp
                for index, category in enumerate(categories)
            }
            base = 1.0 / float(len(categories))
            raw = {
                category: (1.0 - self.tilt_strength) * base + self.tilt_strength * tilted[category]
                for category in categories
            }
        return self._normalize_with_bounds(raw)

    def _normalize_with_bounds(self, raw_weights: Dict[str, float]) -> Dict[str, float]:
        keys = list(raw_weights)
        if not keys:
            return {}
        count = len(keys)
        min_weight = min(self.min_branch_weight, 1.0 / float(count))
        max_weight = max(self.max_branch_weight, 1.0 / float(count), min_weight)
        weights = {key: max(0.0, float(raw_weights.get(key) or 0.0)) for key in keys}
        fixed: Dict[str, float] = {}
        free = set(keys)
        remaining = 1.0
        for _ in range(count + 1):
            if not free:
                break
            total = sum(weights[key] for key in free)
            if total <= 0:
                scaled = {key: remaining / float(len(free)) for key in free}
            else:
                scaled = {key: weights[key] / total * remaining for key in free}
            lows = {key for key, value in scaled.items() if value < min_weight}
            highs = {key for key, value in scaled.items() if value > max_weight}
            if not lows and not highs:
                fixed.update(scaled)
                free.clear()
                break
            for key in sorted(lows):
                fixed[key] = min_weight
                free.remove(key)
            for key in sorted(highs):
                if key in free:
                    fixed[key] = max_weight
                    free.remove(key)
            remaining = max(0.0, 1.0 - sum(fixed.values()))
        if free:
            equal = remaining / float(len(free)) if free else 0.0
            fixed.update({key: equal for key in free})
        total_weight = sum(fixed.values())
        if total_weight <= 0:
            return {key: 1.0 / float(count) for key in keys}
        return {key: fixed.get(key, 0.0) / total_weight for key in keys}

    def _remember_target_weights(self, symbol_weights: Dict[str, float], branch_weights: Dict[str, float]) -> None:
        self._last_target_weights = {key: float(value) for key, value in symbol_weights.items() if value > 0}
        self._last_branch_weights = {key: float(value) for key, value in branch_weights.items() if value > 0}
        self._last_actual_cash_weight = max(0.0, 1.0 - sum(self._last_target_weights.values()))
        self._diagnostics["last_target_weights"] = dict(self._last_target_weights)
        self._diagnostics["last_branch_weights"] = dict(self._last_branch_weights)
        self._diagnostics["last_actual_cash_weight"] = self._last_actual_cash_weight
        self._diagnostics["last_weight_sum"] = sum(self._last_target_weights.values()) + self._last_actual_cash_weight

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
            "last_branch_scores": dict(self._last_branch_scores),
            "last_target_weights": dict(self._last_target_weights),
            "last_branch_weights": dict(self._last_branch_weights),
            "last_actual_cash_weight": self._last_actual_cash_weight,
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
            "weight_mode": self.weight_mode,
            "tilt_strength": self.tilt_strength,
            "temperature": self.temperature,
            "min_branch_weight": self.min_branch_weight,
            "max_branch_weight": self.max_branch_weight,
            "rebalance_threshold": self.rebalance_threshold,
            "trend_penalty": self.trend_penalty,
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
