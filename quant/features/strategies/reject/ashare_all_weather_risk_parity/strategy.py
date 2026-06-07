"""A-share listed ETF all-weather risk-parity candidate."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


STRATEGY_NAME = "ashare_all_weather_risk_parity"
DEFAULT_CATEGORY_SYMBOLS = {
    "equity": ["510300", "510050", "510880"],
    "gold": ["518880"],
    "bond_rate": ["511010"],
    "cash": ["511990"],
}
DEFAULT_RISK_BUDGETS = {
    "equity": 0.35,
    "gold": 0.20,
    "bond_rate": 0.35,
    "cash": 0.10,
}
DEFAULT_PIT_SIZE_FIELDS = ("total_netasset", "net_asset", "fund_size", "aum", "total_net_asset", "net_assets")


@strategy(STRATEGY_NAME)
class AShareAllWeatherRiskParityStrategy(DailyBarStrategy):
    def __init__(
        self,
        category_symbols: Optional[Dict[str, List[str]]] = None,
        risk_budgets: Optional[Dict[str, float]] = None,
        momentum_lookback: int = 63,
        momentum_skip: int = 1,
        trend_window: int = 120,
        volatility_window: int = 60,
        liquidity_window: int = 20,
        min_avg_turnover: float = 20_000_000.0,
        target_exposure: float = 0.98,
        max_asset_weight: float = 0.45,
        volatility_floor: float = 0.02,
        holding_days: int = 20,
        lot_size: int = 100,
        require_pit_size: bool = True,
        pit_size_fields: Optional[List[str]] = None,
        trend_guard_enabled: bool = False,
        trend_guard_exempt_categories: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.10,
        take_profit_pct: float = 0.18,
        trailing_stop_pct: float = 0.07,
        max_holding_days: int = 90,
        min_time_stop_return: float = -0.02,
    ):
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = bool(risk_exit_config.get("enabled"))
        stop_loss_pct = risk_exit_config.get("stop_loss_pct", stop_loss_pct)
        take_profit_pct = risk_exit_config.get("take_profit_pct", take_profit_pct)
        trailing_stop_pct = risk_exit_config.get("trailing_stop_pct", trailing_stop_pct)
        max_holding_days = risk_exit_config.get("max_holding_days", max_holding_days)
        min_time_stop_return = risk_exit_config.get("min_time_stop_return", min_time_stop_return)

        self.category_symbols = self._normalize_category_symbols(category_symbols or DEFAULT_CATEGORY_SYMBOLS)
        self.risk_budgets = self._normalize_risk_budgets(risk_budgets or DEFAULT_RISK_BUDGETS, self.category_symbols)
        symbols = self._flatten_category_symbols(self.category_symbols)
        super().__init__(STRATEGY_NAME, symbols, holding_days=holding_days)
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.trend_window = max(2, int(trend_window))
        self.volatility_window = max(2, int(volatility_window))
        self.liquidity_window = max(1, int(liquidity_window))
        self.min_avg_turnover = float(min_avg_turnover)
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.max_asset_weight = min(max(float(max_asset_weight), 0.0), 1.0)
        self.volatility_floor = max(0.0, float(volatility_floor))
        self.lot_size = max(1, int(lot_size))
        self.require_pit_size = bool(require_pit_size)
        self.pit_size_fields = tuple(str(field) for field in (pit_size_fields or DEFAULT_PIT_SIZE_FIELDS))
        self.trend_guard_enabled = bool(trend_guard_enabled)
        self.trend_guard_exempt_categories = set(
            str(category)
            for category in (trend_guard_exempt_categories or ["gold", "bond_rate", "cash"])
        )
        self.enable_risk_exit = bool(enable_risk_exit)
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.max_holding_days = max(0, int(max_holding_days))
        self.min_time_stop_return = float(min_time_stop_return)
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._entry_bar_counts: Dict[str, int] = {}
        self._last_scores: Dict[str, float] = {}
        self._last_volatilities: Dict[str, float] = {}
        self._last_target_weights: Dict[str, float] = {}
        self._last_selected_by_category: Dict[str, str] = {}
        self._last_visible_by_category: Dict[str, List[str]] = {}
        self._diagnostics: Dict[str, Any] = {
            "rebalance_count": 0,
            "last_selected": [],
            "last_selected_by_category": {},
            "last_visible_by_category": {},
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
            self.max_holding_days,
        ) + (260 if self.require_pit_size else 5)

    def required_snapshot_symbols(self) -> List[str]:
        return []

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        exited = self._exit_risk_positions()
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date, exited):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date, exited: Optional[set[str]] = None) -> bool:
        exited_symbols = set(exited or set())
        target_weights = {
            symbol: weight
            for symbol, weight in self._target_weights(trading_date).items()
            if symbol not in exited_symbols
        }
        selected_set = set(target_weights)
        self._diagnostics["rebalance_count"] = int(self._diagnostics.get("rebalance_count") or 0) + 1
        self._diagnostics["last_selected"] = list(target_weights)
        self._last_target_weights = dict(target_weights)

        submitted = False
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set and symbol not in exited_symbols:
                price = self._get_last_price(symbol)
                sell_quantity = int(quantity)
                if sell_quantity > 0:
                    self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)
                    submitted = True

        if not target_weights:
            return False
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return submitted
        for symbol, weight in target_weights.items():
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = self._round_lot(nav * weight / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
                submitted = True
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)
                submitted = True
        return submitted or bool(target_weights)

    def on_fill(self, context: "Context", fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if not symbol:
            return
        current_quantity = float(self._positions.get(symbol, 0) or 0)
        side = str(getattr(fill, "side", "") or "").upper()
        fill_quantity = float(getattr(fill, "quantity", 0) or 0)
        fill_price = self._fill_price(fill)
        if side == "BUY" and fill_quantity > 0:
            self._record_entry_fill(symbol, previous_quantity, fill_quantity, fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._clear_position_state(symbol)

    def _target_weights(self, trading_date: date) -> Dict[str, float]:
        entries: List[Tuple[str, str, float, float]] = []
        selected_by_category: Dict[str, str] = {}
        self._last_scores = {}
        self._last_volatilities = {}
        visible_by_category = self._visible_symbols_by_category(trading_date)
        for category in self.category_symbols:
            symbol = self._best_symbol_for_category(category, trading_date, visible_by_category)
            if not symbol:
                self._count("entry_rejections", f"{category}_no_candidate")
                continue
            volatility = self._asset_volatility(symbol)
            if volatility is None:
                self._count("entry_rejections", "missing_volatility")
                continue
            risk_budget = self.risk_budgets.get(category, 0.0)
            if risk_budget <= 0:
                self._count("entry_rejections", "zero_risk_budget")
                continue
            selected_by_category[category] = symbol
            entries.append((category, symbol, risk_budget, volatility))
        self._last_selected_by_category = dict(selected_by_category)
        self._diagnostics["last_selected_by_category"] = dict(selected_by_category)
        if not entries:
            return {}
        raw_by_symbol: Dict[str, float] = {}
        for _, symbol, risk_budget, volatility in entries:
            raw_by_symbol[symbol] = raw_by_symbol.get(symbol, 0.0) + risk_budget / volatility
        target_weights = self._normalize_capped_weights(raw_by_symbol)
        return {
            symbol: weight
            for symbol, weight in sorted(target_weights.items())
            if weight > 0
        }

    def _best_symbol_for_category(
        self,
        category: str,
        trading_date: date,
        visible_by_category: Dict[str, List[str]],
    ) -> Optional[str]:
        candidates = []
        for symbol in visible_by_category.get(category, []):
            reason = self._candidate_rejection(symbol, category, trading_date)
            if reason:
                self._count("entry_rejections", reason)
                continue
            score = self._risk_adjusted_momentum(symbol)
            if score is None:
                self._count("entry_rejections", "missing_score")
                continue
            candidates.append((score, symbol))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1]

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

    def _candidate_rejection(self, symbol: str, category: str, trading_date: date) -> str:
        bar = self._get_last_bar(symbol)
        if not bar or not self._is_current_bar(bar, trading_date):
            return "stale_or_missing_bar"
        if self._avg_turnover(symbol) < self.min_avg_turnover:
            return "low_turnover"
        if self.require_pit_size and self._pit_size(symbol, trading_date) is None:
            return "missing_pit_size"
        if self._asset_volatility(symbol) is None:
            return "missing_volatility"
        if self.trend_guard_enabled and category not in self.trend_guard_exempt_categories and not self._above_trend(symbol):
            return "below_trend"
        return ""

    def _normalize_capped_weights(self, raw_by_symbol: Dict[str, float]) -> Dict[str, float]:
        raw = {symbol: value for symbol, value in raw_by_symbol.items() if value > 0 and math.isfinite(value)}
        if not raw:
            return {}
        if self.max_asset_weight <= 0:
            return {}
        capped: Dict[str, float] = {}
        remaining = dict(raw)
        remaining_exposure = self.target_exposure
        while remaining and remaining_exposure > 0:
            total = sum(remaining.values())
            if total <= 0:
                break
            changed = False
            for symbol, value in list(remaining.items()):
                weight = remaining_exposure * value / total
                if weight > self.max_asset_weight:
                    capped[symbol] = self.max_asset_weight
                    remaining_exposure -= self.max_asset_weight
                    remaining.pop(symbol)
                    changed = True
            if not changed:
                for symbol, value in remaining.items():
                    capped[symbol] = remaining_exposure * value / total
                break
        return capped

    def _risk_adjusted_momentum(self, symbol: str) -> Optional[float]:
        volatility = self._asset_volatility(symbol)
        if volatility is None:
            return None
        momentum = self._momentum(symbol)
        score = (momentum or 0.0) / volatility
        self._last_scores[symbol] = score
        return score if math.isfinite(score) else None

    def _asset_volatility(self, symbol: str) -> Optional[float]:
        volatility = self._volatility(symbol, self.volatility_window)
        if volatility is None:
            closes = self._valid_adj_closes(symbol, self.volatility_window + 1)
            if len(closes) < self.volatility_window + 1:
                return None
            volatility = 0.0
        adjusted = max(volatility, self.volatility_floor)
        if adjusted <= 0:
            return None
        self._last_volatilities[symbol] = adjusted
        return adjusted

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

    def _exit_risk_positions(self) -> set[str]:
        exited = set()
        if not self.enable_risk_exit:
            return exited
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            sell_quantity = int(quantity)
            if sell_quantity <= 0:
                self._count("exit_triggers", "dust_position")
                continue
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            self._update_peak_price(symbol, price)
            reason = self._position_exit_reason(symbol, price)
            if not reason:
                continue
            self.sell(symbol, sell_quantity, "MARKET", price)
            self._count("exit_triggers", reason)
            exited.add(symbol)
        return exited

    def _position_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0:
            return ""
        if self.stop_loss_pct > 0 and price <= entry_price * (1.0 - self.stop_loss_pct):
            return "stop_loss"
        peak_price = max(self._peak_prices.get(symbol, price), price)
        if (
            self.take_profit_pct > 0
            and self.trailing_stop_pct > 0
            and peak_price >= entry_price * (1.0 + self.take_profit_pct)
            and price <= peak_price * (1.0 - self.trailing_stop_pct)
        ):
            return "trailing_take_profit"
        if self._time_stop_triggered(symbol, price, entry_price):
            return "time_stop"
        return ""

    def _time_stop_triggered(self, symbol: str, price: float, entry_price: float) -> bool:
        if self.max_holding_days <= 0:
            return False
        entry_bar_count = self._entry_bar_counts.get(symbol)
        if entry_bar_count is None:
            return False
        holding_days = len(self._day_data.get(symbol, [])) - entry_bar_count
        if holding_days < self.max_holding_days:
            return False
        return price / entry_price - 1.0 < self.min_time_stop_return

    def _effective_entry_price(self, symbol: str) -> float:
        portfolio = getattr(getattr(self, "context", None), "portfolio", None)
        get_position = getattr(portfolio, "get_position", None)
        if callable(get_position):
            try:
                position = get_position(symbol)
            except Exception:
                position = None
            for field in ("avg_cost", "average_cost", "entry_price"):
                value = getattr(position, field, None)
                number = self._safe_float(value, 0.0)
                if number > 0:
                    return number
        return self._entry_prices.get(symbol, 0.0)

    def _record_entry_fill(self, symbol: str, previous_quantity: float, fill_quantity: float, fill_price: float) -> None:
        if previous_quantity > 0 and symbol in self._entry_prices:
            total_quantity = previous_quantity + fill_quantity
            if fill_price > 0:
                self._entry_prices[symbol] = (
                    self._entry_prices[symbol] * previous_quantity + fill_price * fill_quantity
                ) / total_quantity
            else:
                adjustment = previous_quantity / total_quantity
                self._entry_prices[symbol] *= adjustment
                if symbol in self._peak_prices:
                    self._peak_prices[symbol] *= adjustment
        elif fill_price > 0:
            self._entry_prices[symbol] = fill_price
            self._entry_bar_counts[symbol] = len(self._day_data.get(symbol, []))
        if fill_price > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)

    def _update_peak_price(self, symbol: str, price: float) -> None:
        if price > 0 and self._positions.get(symbol, 0) > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)

    def _clear_position_state(self, symbol: str) -> None:
        self._entry_prices.pop(symbol, None)
        self._peak_prices.pop(symbol, None)
        self._entry_bar_counts.pop(symbol, None)

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._diagnostics,
            "last_visible_by_category": {
                key: list(value)
                for key, value in self._last_visible_by_category.items()
            },
            "last_selected_by_category": dict(self._last_selected_by_category),
            "entry_rejections": dict(self._diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._diagnostics.get("exit_triggers") or {}),
            "last_scores": dict(self._last_scores),
            "last_volatilities": dict(self._last_volatilities),
            "last_target_weights": dict(self._last_target_weights),
            "parameters": self._get_parameters(),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "category_symbols": {key: list(value) for key, value in self.category_symbols.items()},
            "risk_budgets": dict(self.risk_budgets),
            "momentum_lookback": self.momentum_lookback,
            "momentum_skip": self.momentum_skip,
            "trend_window": self.trend_window,
            "volatility_window": self.volatility_window,
            "liquidity_window": self.liquidity_window,
            "min_avg_turnover": self.min_avg_turnover,
            "target_exposure": self.target_exposure,
            "max_asset_weight": self.max_asset_weight,
            "volatility_floor": self.volatility_floor,
            "holding_days": self.holding_days,
            "lot_size": self.lot_size,
            "require_pit_size": self.require_pit_size,
            "pit_size_fields": list(self.pit_size_fields),
            "trend_guard_enabled": self.trend_guard_enabled,
            "trend_guard_exempt_categories": sorted(self.trend_guard_exempt_categories),
            "enable_risk_exit": self.enable_risk_exit,
            "risk_exit": {
                "enabled": self.enable_risk_exit,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "max_holding_days": self.max_holding_days,
                "min_time_stop_return": self.min_time_stop_return,
            },
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "risk_exit_state": {
                "entry_prices": dict(self._entry_prices),
                "peak_prices": dict(self._peak_prices),
                "entry_bar_counts": dict(self._entry_bar_counts),
            }
        }

    def _on_stop_cleanup(self) -> None:
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._entry_bar_counts.clear()

    def _count(self, bucket: str, key: str) -> None:
        values = self._diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            number = AShareAllWeatherRiskParityStrategy._safe_float(value, 0.0)
            if number > 0:
                return number
        return 0.0

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

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

    @staticmethod
    def _normalize_risk_budgets(
        risk_budgets: Dict[str, float],
        category_symbols: Dict[str, List[str]],
    ) -> Dict[str, float]:
        positive = {}
        for category in category_symbols:
            try:
                value = float(risk_budgets.get(category, 0.0))
            except (TypeError, ValueError):
                value = 0.0
            if math.isfinite(value) and value > 0:
                positive[category] = value
        total = sum(positive.values())
        if total <= 0:
            equal = 1.0 / float(len(category_symbols) or 1)
            return {category: equal for category in category_symbols}
        return {category: positive.get(category, 0.0) / total for category in category_symbols}
