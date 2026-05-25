"""A-share gold-equity ETF barbell timing strategy."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


STRATEGY_NAME = "ashare_gold_equity_barbell_timing"
DEFAULT_RISK_ETFS = ["510050", "510300", "159915", "159949", "510880"]
DEFAULT_DEFENSIVE_ETFS = ["518880"]
DEFAULT_RISK_CATEGORY_SYMBOLS = {
    "sse50": ["510050"],
    "csi300": ["510300"],
    "chinext": ["159915"],
    "chinext50": ["159949"],
    "dividend": ["510880"],
}
DEFAULT_DEFENSIVE_CATEGORY_SYMBOLS = {"gold": ["518880"]}
DEFAULT_PIT_SIZE_FIELDS = ("total_netasset", "net_asset", "fund_size", "aum", "total_net_asset", "net_assets")


@strategy(STRATEGY_NAME)
class AShareGoldEquityBarbellTimingStrategy(DailyBarStrategy):
    def __init__(
        self,
        risk_symbols: Optional[List[str]] = None,
        defensive_symbols: Optional[List[str]] = None,
        timing_symbol: str = "510300",
        momentum_lookback: int = 63,
        momentum_skip: int = 1,
        trend_window: int = 120,
        volatility_window: int = 20,
        liquidity_window: int = 20,
        min_avg_turnover: float = 20_000_000.0,
        target_exposure: float = 0.98,
        risk_leg_weight: float = 0.50,
        holding_days: int = 20,
        lot_size: int = 100,
        risk_category_symbols: Optional[Dict[str, List[str]]] = None,
        defensive_category_symbols: Optional[Dict[str, List[str]]] = None,
        pit_size_fields: Optional[List[str]] = None,
        require_pit_size: bool = False,
    ):
        explicit_risk_symbols = [str(symbol) for symbol in risk_symbols] if risk_symbols is not None else None
        explicit_defensive_symbols = [str(symbol) for symbol in defensive_symbols] if defensive_symbols is not None else None
        self.risk_category_symbols = self._normalize_category_symbols(
            risk_category_symbols,
            explicit_risk_symbols,
            DEFAULT_RISK_CATEGORY_SYMBOLS,
            "risk",
        )
        self.defensive_category_symbols = self._normalize_category_symbols(
            defensive_category_symbols,
            explicit_defensive_symbols,
            DEFAULT_DEFENSIVE_CATEGORY_SYMBOLS,
            "defensive",
        )
        self.risk_symbols = self._flatten_category_symbols(self.risk_category_symbols)
        self.defensive_symbols = self._flatten_category_symbols(self.defensive_category_symbols)
        self._risk_category_mode = risk_category_symbols is not None or explicit_risk_symbols is None
        self._defensive_category_mode = defensive_category_symbols is not None or explicit_defensive_symbols is None
        self.timing_symbol = str(timing_symbol)
        symbols = list(dict.fromkeys([*self.risk_symbols, *self.defensive_symbols, self.timing_symbol]))
        super().__init__(STRATEGY_NAME, symbols, holding_days=holding_days)
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.trend_window = max(2, int(trend_window))
        self.volatility_window = max(2, int(volatility_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_avg_turnover = float(min_avg_turnover)
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.risk_leg_weight = min(max(float(risk_leg_weight), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self.pit_size_fields = tuple(str(field) for field in (pit_size_fields or DEFAULT_PIT_SIZE_FIELDS))
        self.require_pit_size = bool(require_pit_size)
        self._last_scores: Dict[str, float] = {}
        self._last_target_weights: Dict[str, float] = {}
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "risk_on_count": 0,
            "risk_off_count": 0,
            "last_risk_on": False,
            "last_selected": [],
            "last_risk_category_candidates": {},
            "last_defensive_category_candidates": {},
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
        target_weights = self._target_weights(trading_date)
        selected_set = set(target_weights)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(target_weights)
        self._last_target_weights = dict(target_weights)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._get_last_price(symbol)
                sell_quantity = int(quantity)
                if sell_quantity > 0:
                    self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)

        if not target_weights:
            return False
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return False
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

    def _target_weights(self, trading_date: date) -> Dict[str, float]:
        defensive = self._best_defensive_symbol(trading_date)
        risk_on = self._risk_on(trading_date)
        self._diagnostics["last_risk_on"] = risk_on
        if risk_on:
            self._diagnostics["risk_on_count"] = int(self._diagnostics.get("risk_on_count") or 0) + 1
            risk_symbol = self._best_risk_symbol(trading_date)
            if risk_symbol and defensive and risk_symbol != defensive:
                return {
                    risk_symbol: self.target_exposure * self.risk_leg_weight,
                    defensive: self.target_exposure * (1.0 - self.risk_leg_weight),
                }
            if risk_symbol:
                return {risk_symbol: self.target_exposure}
        self._diagnostics["risk_off_count"] = int(self._diagnostics.get("risk_off_count") or 0) + 1
        return {defensive: self.target_exposure} if defensive else {}

    def _risk_on(self, trading_date: date) -> bool:
        bar = self._get_last_bar(self.timing_symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return False
        closes = self._valid_adj_closes(self.timing_symbol, max(self.trend_window, self.momentum_lookback + self.momentum_skip + 1))
        if len(closes) < max(self.trend_window, self.momentum_lookback + self.momentum_skip + 1):
            return False
        trend_ma = sum(closes[-self.trend_window :]) / float(self.trend_window)
        momentum = self._momentum(self.timing_symbol)
        return closes[-1] > trend_ma and momentum is not None and momentum > 0.0

    def _best_risk_symbol(self, trading_date: date) -> Optional[str]:
        scores = {}
        symbols = self._risk_candidate_symbols(trading_date)
        for symbol in symbols:
            reason = self._candidate_rejection(symbol, trading_date)
            if reason:
                self._count("entry_rejections", reason)
                continue
            score = self._risk_adjusted_momentum(symbol)
            if score is not None:
                scores[symbol] = score
        self._last_scores = dict(scores)
        if not scores:
            return None
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _best_defensive_symbol(self, trading_date: date) -> Optional[str]:
        candidates = []
        symbols = self._defensive_candidate_symbols(trading_date)
        for symbol in symbols:
            bar = self._get_last_bar(symbol)
            if not bar or not self._is_current_bar(bar, trading_date):
                self._count("entry_rejections", "defensive_stale_or_missing_bar")
                continue
            if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
                self._count("entry_rejections", "defensive_missing_pit_size")
                continue
            if self._avg_turnover(symbol) < self.min_avg_turnover:
                self._count("entry_rejections", "defensive_low_turnover")
                continue
            score = self._risk_adjusted_momentum(symbol)
            candidates.append((score if score is not None else 0.0, symbol))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1]

    def _candidate_rejection(self, symbol: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if self._avg_turnover(symbol) < self.min_avg_turnover:
            return "low_turnover"
        if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
            return "missing_pit_size"
        momentum = self._momentum(symbol)
        if momentum is None:
            return "missing_momentum"
        if momentum <= 0.0:
            return "weak_momentum"
        return ""

    def _risk_adjusted_momentum(self, symbol: str) -> Optional[float]:
        momentum = self._momentum(symbol)
        volatility = self._volatility(symbol, self.volatility_window)
        if momentum is None or volatility is None or volatility <= 0:
            return None
        score = momentum / volatility
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

    def _risk_candidate_symbols(self, trading_date: date) -> List[str]:
        if not self._risk_category_mode:
            return list(self.risk_symbols)
        candidates = self._visible_symbols_by_category(
            self.risk_category_symbols,
            trading_date,
            "risk",
        )
        self._diagnostics["last_risk_category_candidates"] = {key: list(value) for key, value in candidates.items()}
        return self._flatten_category_symbols(candidates)

    def _defensive_candidate_symbols(self, trading_date: date) -> List[str]:
        if not self._defensive_category_mode:
            return list(self.defensive_symbols)
        candidates = self._visible_symbols_by_category(
            self.defensive_category_symbols,
            trading_date,
            "defensive",
        )
        self._diagnostics["last_defensive_category_candidates"] = {key: list(value) for key, value in candidates.items()}
        return self._flatten_category_symbols(candidates)

    def _visible_symbols_by_category(
        self,
        category_symbols: Dict[str, List[str]],
        trading_date: date,
        bucket: str,
    ) -> Dict[str, List[str]]:
        selected = {}
        for category, symbols in category_symbols.items():
            visible = []
            for symbol in symbols:
                bar = self._get_last_bar(symbol)
                if not bar or not self._is_current_bar(bar, trading_date):
                    self._count("entry_rejections", f"{bucket}_category_stale_or_missing_bar")
                    continue
                if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
                    self._count("entry_rejections", f"{bucket}_category_missing_pit_size")
                    continue
                visible.append(str(symbol))
            selected[str(category)] = list(dict.fromkeys(visible))
        return selected

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

    @staticmethod
    def _normalize_category_symbols(
        category_symbols: Optional[Dict[str, List[str]]],
        flat_symbols: Optional[List[str]],
        defaults: Dict[str, List[str]],
        fallback_category: str,
    ) -> Dict[str, List[str]]:
        if category_symbols is not None:
            return {
                str(category): list(dict.fromkeys(str(symbol) for symbol in symbols))
                for category, symbols in category_symbols.items()
                if symbols
            }
        if flat_symbols is not None:
            return {fallback_category: list(dict.fromkeys(flat_symbols))}
        return {category: list(symbols) for category, symbols in defaults.items()}

    @staticmethod
    def _flatten_category_symbols(category_symbols: Dict[str, List[str]]) -> List[str]:
        values = []
        for symbols in category_symbols.values():
            values.extend(str(symbol) for symbol in symbols)
        return list(dict.fromkeys(values))

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "last_scores": dict(self._last_scores),
            "last_target_weights": dict(self._last_target_weights),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "risk_symbols": list(self.risk_symbols),
            "defensive_symbols": list(self.defensive_symbols),
            "timing_symbol": self.timing_symbol,
            "momentum_lookback": self.momentum_lookback,
            "momentum_skip": self.momentum_skip,
            "trend_window": self.trend_window,
            "volatility_window": self.volatility_window,
            "liquidity_window": self.liquidity_window,
            "min_avg_turnover": self.min_avg_turnover,
            "target_exposure": self.target_exposure,
            "risk_leg_weight": self.risk_leg_weight,
            "holding_days": self.holding_days,
            "lot_size": self.lot_size,
            "risk_category_symbols": {key: list(value) for key, value in self.risk_category_symbols.items()},
            "defensive_category_symbols": {key: list(value) for key, value in self.defensive_category_symbols.items()},
            "pit_size_fields": list(self.pit_size_fields),
            "require_pit_size": self.require_pit_size,
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
        if not math.isfinite(number):
            return None
        return number
