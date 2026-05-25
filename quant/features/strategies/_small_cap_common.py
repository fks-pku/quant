"""Shared A-share small-cap rotation logic."""

from datetime import date
import math
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.features.strategies.base import Strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


MARKET_CAP_FIELDS = (
    "market_cap",
    "total_market_cap",
    "total_mv",
    "circ_mv",
    "float_market_cap",
    "circulating_market_cap",
)


class AShareSmallCapRotationBase(Strategy):
    def __init__(
        self,
        name: str,
        symbols: Optional[List[str]] = None,
        max_positions: int = 20,
        rebalance_interval: int = 10,
        min_price: float = 5.0,
        min_adv_value: float = 20000.0,
        lot_size: int = 100,
        target_exposure: float = 1.0,
        market_timing_symbol: str = "",
        market_trend_window: int = 0,
        market_momentum_lookback: int = 0,
        market_momentum_threshold: float = 0.0,
        market_risk_off_exposure: float = 0.0,
        stock_trend_window: int = 0,
        max_pb: float = 0.0,
        max_ps_ttm: float = 0.0,
        max_pe_ttm: float = 0.0,
        max_turnover_rate_f: float = 0.0,
        max_volume_ratio: float = 0.0,
        require_positive_pe: bool = False,
        require_quality_fields: bool = False,
        broad_index_symbols: Optional[List[str]] = None,
        broad_index_exposure: float = 0.0,
        broad_momentum_lookback: int = 63,
        broad_volatility_window: int = 20,
        broad_volatility_penalty: float = 0.5,
        broad_min_momentum: float = 0.0,
        relative_strength_lookback: int = 20,
        weak_small_cap_exposure: Optional[float] = None,
    ):
        super().__init__(name)
        self.market_timing_symbol = str(market_timing_symbol or "")
        self.broad_index_symbols = [str(symbol) for symbol in broad_index_symbols] if broad_index_symbols else []
        requested_symbols = [str(symbol) for symbol in symbols] if symbols else []
        if self.market_timing_symbol and self.market_timing_symbol not in requested_symbols:
            requested_symbols.append(self.market_timing_symbol)
        for symbol in self.broad_index_symbols:
            if symbol not in requested_symbols:
                requested_symbols.append(symbol)
        self._symbols = requested_symbols
        self.max_positions = max(1, int(max_positions))
        self.rebalance_interval = max(1, int(rebalance_interval))
        self.min_price = float(min_price)
        self.min_adv_value = float(min_adv_value)
        self.lot_size = max(1, int(lot_size))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.market_trend_window = max(0, int(market_trend_window))
        self.market_momentum_lookback = max(0, int(market_momentum_lookback))
        self.market_momentum_threshold = float(market_momentum_threshold)
        self.market_risk_off_exposure = min(max(float(market_risk_off_exposure), 0.0), self.target_exposure)
        self.stock_trend_window = max(0, int(stock_trend_window))
        self.max_pb = max(0.0, float(max_pb))
        self.max_ps_ttm = max(0.0, float(max_ps_ttm))
        self.max_pe_ttm = max(0.0, float(max_pe_ttm))
        self.max_turnover_rate_f = max(0.0, float(max_turnover_rate_f))
        self.max_volume_ratio = max(0.0, float(max_volume_ratio))
        self.require_positive_pe = bool(require_positive_pe)
        self.require_quality_fields = bool(require_quality_fields)
        self.broad_index_exposure = min(max(float(broad_index_exposure), 0.0), 1.0)
        self.broad_momentum_lookback = max(1, int(broad_momentum_lookback))
        self.broad_volatility_window = max(2, int(broad_volatility_window))
        self.broad_volatility_penalty = max(0.0, float(broad_volatility_penalty))
        self.broad_min_momentum = float(broad_min_momentum)
        self.relative_strength_lookback = max(0, int(relative_strength_lookback))
        fallback_weak_exposure = self.target_exposure
        if weak_small_cap_exposure is not None:
            fallback_weak_exposure = float(weak_small_cap_exposure)
        self.weak_small_cap_exposure = min(max(fallback_weak_exposure, 0.0), self.target_exposure)
        self._max_bars = max(
            90,
            self.market_trend_window + self.market_momentum_lookback + 5,
            self.stock_trend_window + 5,
            self.broad_momentum_lookback + self.broad_volatility_window + 5,
            self.relative_strength_lookback + 5,
        )
        self._bars: Dict[str, List[Any]] = {}
        self._last_price: Dict[str, float] = {}
        self._symbol_set = set(self._symbols)
        self._rebalance_counter = 0
        self._guard_diagnostics: Dict[str, Any] = {
            "entry_rejections": {},
            "exit_triggers": {},
        }

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = str(self._value(data, "symbol", "") or "")
        if not symbol or (self._symbol_set and symbol not in self._symbol_set):
            return
        price = self._price(data)
        if price > 0:
            self._last_price[symbol] = price
        self._bars.setdefault(symbol, []).append(data)
        if len(self._bars[symbol]) > self._max_bars:
            self._bars[symbol] = self._bars[symbol][-self._max_bars:]

    def on_data_batch(self, context: "Context", data: Any) -> None:
        bars = data.values() if isinstance(data, dict) else data
        symbol_set = self._symbol_set
        last_price = self._last_price
        stored_bars = self._bars
        for bar in bars:
            symbol = str(self._value(bar, "symbol", "") or "")
            if not symbol or (symbol_set and symbol not in symbol_set):
                continue
            price = self._price(bar)
            if price > 0:
                last_price[symbol] = price
            symbol_bars = stored_bars.setdefault(symbol, [])
            symbol_bars.append(bar)
            if len(symbol_bars) > self._max_bars:
                del symbol_bars[:-self._max_bars]

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        exited = self._exit_risk_positions()
        base_small_cap_exposure = self._target_exposure()
        try_rebalance = self._rebalance_counter % self.rebalance_interval == 0
        self._rebalance_counter += 1
        if not try_rebalance and base_small_cap_exposure > 0:
            return

        candidates = []
        if base_small_cap_exposure > 0:
            for symbol, bars in self._bars.items():
                if not bars or symbol in exited:
                    continue
                bar = bars[-1]
                if self._entry_risk(symbol, bar):
                    continue
                candidates.append((self._candidate_score(symbol, bar), symbol))

        selected = [
            symbol
            for _, symbol in sorted(candidates, key=lambda item: (-item[0], item[1]))[: self.max_positions]
        ]
        broad_symbol = self._select_broad_index_symbol()
        small_cap_exposure, broad_exposure = self._blend_exposures(selected, broad_symbol, base_small_cap_exposure)
        selected_set = set(selected)
        if broad_symbol and broad_exposure > 0:
            selected_set.add(broad_symbol)
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set and symbol not in exited:
                self._sell_position(symbol, quantity, "rebalance_exit")

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        if small_cap_exposure > 0 and selected:
            self._rebalance_small_cap_sleeve(selected, nav * small_cap_exposure / float(self.max_positions))
        if broad_symbol and broad_exposure > 0:
            self._rebalance_symbol_to_value(broad_symbol, nav * broad_exposure, "broad_index_rebalance")

    def _rebalance_small_cap_sleeve(self, selected: List[str], target_value: float) -> None:
        for symbol in selected:
            self._rebalance_symbol_to_value(symbol, target_value, "small_cap_rebalance")

    def _rebalance_symbol_to_value(self, symbol: str, target_value: float, reason: str) -> None:
        price = self._last_price.get(symbol, 0.0)
        if price <= 0:
            return
        target_quantity = self._round_lot(target_value / price)
        current_quantity = int(self._positions.get(symbol, 0) or 0)
        delta = target_quantity - current_quantity
        if delta > 0:
            self.buy(symbol, delta, "MARKET", price)
        elif delta < 0:
            self.sell(symbol, abs(delta), "MARKET", price)

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                self._sell_position(symbol, quantity, "stop_exit")
        self._bars.clear()
        self._last_price.clear()
        super().on_stop(context)

    def _candidate_score(self, symbol: str, bar: Any) -> float:
        market_cap = self._market_cap(bar)
        return -market_cap

    def _exit_risk_positions(self) -> set:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bars = self._bars.get(symbol, [])
            if not bars or not self._exit_risk(symbol, bars[-1]):
                continue
            self._sell_position(symbol, quantity, "risk_exit")
            exited.add(symbol)
        return exited

    def _sell_position(self, symbol: str, quantity: float, reason: str) -> bool:
        sell_quantity = int(quantity)
        if sell_quantity <= 0:
            self._count("exit_triggers", "dust_position")
            return False
        price = self._last_price.get(symbol, 0.0)
        self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)
        self._count("exit_triggers", reason)
        return True

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if self.market_timing_symbol and symbol == self.market_timing_symbol:
            return True
        if symbol in self.broad_index_symbols:
            return True
        if not self._is_mainland_a_symbol(symbol):
            return True
        if self._bool_value(self._value(bar, "is_st", False), False):
            return True
        if self._bool_value(self._value(bar, "_suspended", False), False):
            return True
        if self._bool_value(self._value(bar, "status_is_suspended", False), False):
            return True
        if self._bool_value(self._value(bar, "tradable", True), True) is False:
            return True
        if self._bool_value(self._value(bar, "has_daily_bar", True), True) is False:
            return True
        if self._bool_value(self._value(bar, "is_listed", True), True) is False:
            return True
        list_status = str(self._value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            return True
        if self._price(bar) < self.min_price:
            return True
        if self._market_cap(bar) <= 0:
            return True
        if not self._stock_trend_ok(symbol):
            return True
        if self._quality_risk(bar):
            return True
        return self._average_turnover(symbol) < self.min_adv_value

    def _exit_risk(self, symbol: str, bar: Any) -> bool:
        if symbol in self.broad_index_symbols:
            return False
        if self._entry_risk(symbol, bar):
            return True
        price = self._price(bar)
        return price > 0 and price < self.min_price

    def _average_turnover(self, symbol: str) -> float:
        bars = self._bars.get(symbol, [])[-20:]
        values = [self._bar_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if not values:
            return 0.0
        return sum(values) / float(len(values))

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._float_value(self._value(bar, "turnover", None), 0.0)
        if turnover > 0:
            return turnover
        return self._price(bar) * self._float_value(self._value(bar, "volume", 0.0), 0.0)

    def _market_cap(self, bar: Any) -> float:
        for field in MARKET_CAP_FIELDS:
            value = self._float_value(self._value(bar, field, None), 0.0)
            if value > 0:
                return value
        return 0.0

    def _quality_risk(self, bar: Any) -> bool:
        if self._high_quality_metric(bar, ("pb",), self.max_pb, "high_pb", "missing_pb"):
            return True
        if self._high_quality_metric(bar, ("ps_ttm", "ps"), self.max_ps_ttm, "high_ps_ttm", "missing_ps_ttm"):
            return True
        pe = self._positive_field_value(bar, ("pe_ttm", "pe"))
        if self.require_positive_pe and pe <= 0:
            self._count("entry_rejections", "missing_or_negative_pe")
            return True
        if self.max_pe_ttm > 0:
            if pe <= 0:
                if self.require_quality_fields:
                    self._count("entry_rejections", "missing_pe_ttm")
                    return True
            elif pe > self.max_pe_ttm:
                self._count("entry_rejections", "high_pe_ttm")
                return True
        if self._high_quality_metric(
            bar,
            ("turnover_rate_f", "turnover_rate"),
            self.max_turnover_rate_f,
            "high_turnover_rate_f",
            "missing_turnover_rate_f",
        ):
            return True
        return self._high_quality_metric(
            bar,
            ("volume_ratio",),
            self.max_volume_ratio,
            "high_volume_ratio",
            "missing_volume_ratio",
        )

    def _high_quality_metric(
        self,
        bar: Any,
        fields: tuple[str, ...],
        ceiling: float,
        high_reason: str,
        missing_reason: str,
    ) -> bool:
        if ceiling <= 0:
            return False
        value = self._positive_field_value(bar, fields)
        if value <= 0:
            if self.require_quality_fields:
                self._count("entry_rejections", missing_reason)
                return True
            return False
        if value > ceiling:
            self._count("entry_rejections", high_reason)
            return True
        return False

    def _positive_field_value(self, bar: Any, fields: tuple[str, ...]) -> float:
        for field in fields:
            value = self._float_value(self._value(bar, field, None), 0.0)
            if value > 0:
                return value
        return 0.0

    def _return(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) <= lookback:
            return None
        current = self._adj(bars[-1], "close")
        base = self._adj(bars[-lookback - 1], "close")
        if current <= 0 or base <= 0:
            return None
        return current / base - 1.0

    def _target_exposure(self) -> float:
        if self.target_exposure <= 0:
            return 0.0
        if not self._market_timing_enabled():
            return self.target_exposure
        return self.target_exposure if self._market_risk_on() else self.market_risk_off_exposure

    def _market_timing_enabled(self) -> bool:
        return bool(self.market_timing_symbol) and (
            self.market_trend_window > 0 or self.market_momentum_lookback > 0
        )

    def _market_risk_on(self) -> bool:
        bars = self._bars.get(self.market_timing_symbol, [])
        required = max(self.market_trend_window, self.market_momentum_lookback)
        if required <= 0:
            return True
        if len(bars) <= required:
            return False
        current = self._adj(bars[-1], "close")
        if current <= 0:
            return False
        if self.market_trend_window > 0:
            prices = [self._adj(bar, "close") for bar in bars[-self.market_trend_window:]]
            prices = [price for price in prices if price > 0 and math.isfinite(price)]
            if len(prices) < self.market_trend_window:
                return False
            if current < sum(prices) / float(len(prices)):
                return False
        if self.market_momentum_lookback > 0:
            momentum = self._return(self.market_timing_symbol, self.market_momentum_lookback)
            if momentum is None or momentum < self.market_momentum_threshold:
                return False
        return True

    def _select_broad_index_symbol(self) -> Optional[str]:
        best_symbol = None
        best_score = None
        for symbol in self.broad_index_symbols:
            momentum = self._return(symbol, self.broad_momentum_lookback)
            volatility = self._volatility(symbol, self.broad_volatility_window)
            if momentum is None or volatility is None:
                continue
            if momentum < self.broad_min_momentum:
                continue
            score = momentum - self.broad_volatility_penalty * volatility
            if best_score is None or score > best_score or (score == best_score and symbol < str(best_symbol)):
                best_symbol = symbol
                best_score = score
        return best_symbol

    def _blend_exposures(
        self,
        selected: List[str],
        broad_symbol: Optional[str],
        base_small_cap_exposure: float,
    ) -> tuple[float, float]:
        broad_exposure = self.broad_index_exposure if broad_symbol else 0.0
        small_cap_exposure = base_small_cap_exposure
        if broad_symbol and self.relative_strength_lookback > 0 and selected:
            small_strength = self._selected_average_return(selected, self.relative_strength_lookback)
            broad_strength = self._return(broad_symbol, self.relative_strength_lookback)
            if small_strength is not None and broad_strength is not None and small_strength < broad_strength:
                transfer = max(0.0, base_small_cap_exposure - self.weak_small_cap_exposure)
                small_cap_exposure = self.weak_small_cap_exposure
                broad_exposure += transfer
        total_exposure = small_cap_exposure + broad_exposure
        if total_exposure > 1.0:
            scale = 1.0 / total_exposure
            small_cap_exposure *= scale
            broad_exposure *= scale
        return small_cap_exposure, broad_exposure

    def _selected_average_return(self, selected: List[str], lookback: int) -> Optional[float]:
        values = [self._return(symbol, lookback) for symbol in selected]
        values = [value for value in values if value is not None and math.isfinite(value)]
        if not values:
            return None
        return sum(values) / float(len(values))

    def _volatility(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) <= lookback:
            return None
        prices = [self._adj(bar, "close") for bar in bars[-lookback - 1:]]
        prices = [price for price in prices if price > 0 and math.isfinite(price)]
        if len(prices) < 3:
            return None
        returns = [prices[index] / prices[index - 1] - 1.0 for index in range(1, len(prices))]
        mean = sum(returns) / float(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / float(max(1, len(returns) - 1))
        return math.sqrt(max(variance, 0.0)) * math.sqrt(252.0)

    def _stock_trend_ok(self, symbol: str) -> bool:
        if self.stock_trend_window <= 0:
            return True
        bars = self._bars.get(symbol, [])
        if len(bars) < self.stock_trend_window:
            return False
        current = self._adj(bars[-1], "close")
        prices = [self._adj(bar, "close") for bar in bars[-self.stock_trend_window:]]
        prices = [price for price in prices if price > 0 and math.isfinite(price)]
        if current <= 0 or len(prices) < self.stock_trend_window:
            return False
        return current >= sum(prices) / float(len(prices))

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parameters": {
                "max_positions": self.max_positions,
                "rebalance_interval": self.rebalance_interval,
                "min_price": self.min_price,
                "min_adv_value": self.min_adv_value,
                "lot_size": self.lot_size,
                "target_exposure": self.target_exposure,
                "market_timing_symbol": self.market_timing_symbol,
                "market_trend_window": self.market_trend_window,
                "market_momentum_lookback": self.market_momentum_lookback,
                "market_momentum_threshold": self.market_momentum_threshold,
                "market_risk_off_exposure": self.market_risk_off_exposure,
                "stock_trend_window": self.stock_trend_window,
                "max_pb": self.max_pb,
                "max_ps_ttm": self.max_ps_ttm,
                "max_pe_ttm": self.max_pe_ttm,
                "max_turnover_rate_f": self.max_turnover_rate_f,
                "max_volume_ratio": self.max_volume_ratio,
                "require_positive_pe": self.require_positive_pe,
                "require_quality_fields": self.require_quality_fields,
                "broad_index_symbols": list(self.broad_index_symbols),
                "broad_index_exposure": self.broad_index_exposure,
                "broad_momentum_lookback": self.broad_momentum_lookback,
                "broad_volatility_window": self.broad_volatility_window,
                "broad_volatility_penalty": self.broad_volatility_penalty,
                "broad_min_momentum": self.broad_min_momentum,
                "relative_strength_lookback": self.relative_strength_lookback,
                "weak_small_cap_exposure": self.weak_small_cap_exposure,
            },
        }

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._guard_diagnostics,
            "entry_rejections": dict(self._guard_diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._guard_diagnostics.get("exit_triggers") or {}),
            "parameters": self.get_state().get("parameters") or {},
        }

    def _count(self, bucket: str, key: str) -> None:
        values = self._guard_diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @staticmethod
    def _is_mainland_a_symbol(symbol: str) -> bool:
        text = str(symbol)
        if len(text) != 6 or not text.isdigit():
            return False
        return text.startswith(("0", "2", "3", "6")) and not text.startswith("200")

    @staticmethod
    def _value(data: Any, field: str, default: Any = None) -> Any:
        if isinstance(data, dict):
            return data.get(field, default)
        return getattr(data, field, default)

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _bool_value(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"", "nan", "none", "null"}:
                return default
            if text in {"0", "false", "f", "no", "n"}:
                return False
            if text in {"1", "true", "t", "yes", "y"}:
                return True
        try:
            if value != value:
                return default
        except Exception:
            return default
        return bool(value)
