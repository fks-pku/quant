"""Xueqiu small-cap financial-filter rotation."""

from datetime import date
import math
from typing import Any, Dict, List, Optional

from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.strategies.registry import strategy


DEFAULT_EXCLUDED_BOARD_PREFIXES = ("300", "301", "688", "689")


def _symbol_has_prefix(symbol: str, prefixes: tuple[str, ...]) -> bool:
    return any(str(symbol).startswith(prefix) for prefix in prefixes)


@strategy("xueqiu_small_cap_financial_filter")
class XueqiuSmallCapFinancialFilterStrategy(AShareSmallCapRotationBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        max_positions: int = 5,
        min_positions: int = 3,
        min_price: float = 2.0,
        min_adv_value: float = 20000.0,
        lot_size: int = 100,
        target_exposure: float = 1.0,
        min_market_cap: float = 100000.0,
        min_inferred_revenue: float = 10000.0,
        require_financial_fields: bool = True,
        empty_months: Optional[List[int]] = None,
        rebalance_signal_weekday: int = 0,
        risk_index_symbol: str = "",
        index_drawdown_lookback: int = 5,
        index_drawdown_threshold: float = -0.05,
        excluded_board_prefixes: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.12,
        min_stop_loss_pct: float = 0.08,
        max_stop_loss_pct: float = 0.18,
        stop_volatility_multiplier: float = 3.0,
        take_profit_pct: float = 0.25,
        trailing_stop_pct: float = 0.10,
        trailing_volatility_multiplier: float = 2.5,
        max_trailing_stop_pct: float = 0.22,
        hard_take_profit_pct: float = 0.0,
        exit_volatility_lookback: int = 20,
        max_holding_days: int = 45,
        min_time_stop_return: float = 0.02,
    ):
        excluded_prefixes = DEFAULT_EXCLUDED_BOARD_PREFIXES if excluded_board_prefixes is None else tuple(
            str(prefix) for prefix in excluded_board_prefixes if str(prefix)
        )
        extra_symbols = [
            str(symbol)
            for symbol in list(symbols or [])
            if not _symbol_has_prefix(str(symbol), excluded_prefixes)
        ]
        if risk_index_symbol and risk_index_symbol not in extra_symbols:
            extra_symbols.append(risk_index_symbol)
        super().__init__(
            "xueqiu_small_cap_financial_filter",
            symbols=extra_symbols,
            max_positions=max_positions,
            rebalance_interval=5,
            min_price=min_price,
            min_adv_value=min_adv_value,
            lot_size=lot_size,
            target_exposure=target_exposure,
        )
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = self._bool_value(risk_exit_config.get("enabled"), bool(enable_risk_exit))
        stop_loss_pct = risk_exit_config.get("stop_loss_pct", stop_loss_pct)
        min_stop_loss_pct = risk_exit_config.get("min_stop_loss_pct", min_stop_loss_pct)
        max_stop_loss_pct = risk_exit_config.get("max_stop_loss_pct", max_stop_loss_pct)
        stop_volatility_multiplier = risk_exit_config.get("stop_volatility_multiplier", stop_volatility_multiplier)
        take_profit_pct = risk_exit_config.get("take_profit_pct", take_profit_pct)
        trailing_stop_pct = risk_exit_config.get("trailing_stop_pct", trailing_stop_pct)
        trailing_volatility_multiplier = risk_exit_config.get(
            "trailing_volatility_multiplier",
            trailing_volatility_multiplier,
        )
        max_trailing_stop_pct = risk_exit_config.get("max_trailing_stop_pct", max_trailing_stop_pct)
        hard_take_profit_pct = risk_exit_config.get("hard_take_profit_pct", hard_take_profit_pct)
        exit_volatility_lookback = risk_exit_config.get("exit_volatility_lookback", exit_volatility_lookback)
        max_holding_days = risk_exit_config.get("max_holding_days", max_holding_days)
        min_time_stop_return = risk_exit_config.get("min_time_stop_return", min_time_stop_return)
        self.min_positions = max(1, int(min_positions))
        self.min_market_cap = float(min_market_cap)
        self.min_inferred_revenue = float(min_inferred_revenue)
        self.require_financial_fields = bool(require_financial_fields)
        month_values = [1, 4] if empty_months is None else empty_months
        self.empty_months = {int(month) for month in month_values}
        self.rebalance_signal_weekday = int(rebalance_signal_weekday)
        self.risk_index_symbol = str(risk_index_symbol or "")
        self.index_drawdown_lookback = max(1, int(index_drawdown_lookback))
        self.index_drawdown_threshold = float(index_drawdown_threshold)
        self.excluded_board_prefixes = tuple(excluded_prefixes)
        self.enable_risk_exit = bool(enable_risk_exit)
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.min_stop_loss_pct = max(0.0, float(min_stop_loss_pct))
        self.max_stop_loss_pct = max(0.0, float(max_stop_loss_pct))
        self.stop_volatility_multiplier = max(0.0, float(stop_volatility_multiplier))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.trailing_volatility_multiplier = max(0.0, float(trailing_volatility_multiplier))
        self.max_trailing_stop_pct = max(0.0, float(max_trailing_stop_pct))
        self.hard_take_profit_pct = max(0.0, float(hard_take_profit_pct))
        self.exit_volatility_lookback = max(2, int(exit_volatility_lookback))
        self.max_holding_days = max(0, int(max_holding_days))
        self.min_time_stop_return = float(min_time_stop_return)
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._entry_bar_counts: Dict[str, int] = {}

    def on_after_trading(self, context: Any, trading_date: date) -> None:
        exited = self._exit_risk_positions()
        if trading_date.month in self.empty_months or self._index_risk_off():
            self._sell_unwanted(set(), exited)
            return
        if trading_date.weekday() != self.rebalance_signal_weekday:
            return
        selected = self._select_candidates(exited)
        selected_set = set(selected)
        self._sell_unwanted(selected_set, exited)
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0 or len(selected) < self.min_positions:
            return
        target_value = nav * self.target_exposure / float(max(self.max_positions, len(selected)))
        self._rebalance_small_cap_sleeve(selected, target_value)

    def _select_candidates(self, exited: set) -> List[str]:
        candidates = []
        for symbol, bars in self._bars.items():
            if not bars or symbol in exited:
                continue
            if self.risk_index_symbol and symbol == self.risk_index_symbol:
                continue
            bar = bars[-1]
            if self._entry_risk(symbol, bar):
                continue
            candidates.append((self._candidate_score(symbol, bar), symbol))
        return [
            symbol
            for _, symbol in sorted(candidates, key=lambda item: (-item[0], item[1]))[: self.max_positions]
        ]

    def _sell_unwanted(self, selected: set, exited: set) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected and symbol not in exited:
                self._sell_position(symbol, quantity, "risk_or_rebalance_exit")

    def on_fill(self, context: Any, fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if not symbol or (self.risk_index_symbol and symbol == self.risk_index_symbol):
            return
        current_quantity = float(self._positions.get(symbol, 0) or 0)
        side = str(getattr(fill, "side", "") or "").upper()
        fill_quantity = float(getattr(fill, "quantity", 0) or 0)
        fill_price = self._fill_price(fill)
        if side == "BUY" and fill_quantity > 0:
            self._record_entry_fill(symbol, previous_quantity, fill_quantity, fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._clear_position_state(symbol)

    def _exit_risk_positions(self) -> set:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bars = self._bars.get(symbol, [])
            if not bars:
                continue
            price = self._price(bars[-1])
            self._update_peak_price(symbol, price)
            reason = self._position_exit_reason(symbol, bars[-1])
            if not reason:
                continue
            if self._sell_position(symbol, quantity, reason):
                exited.add(symbol)
        return exited

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if self.risk_index_symbol and symbol == self.risk_index_symbol:
            return True
        if self._is_permission_excluded_symbol(symbol):
            self._count("entry_rejections", "excluded_permission_board")
            return True
        if super()._entry_risk(symbol, bar):
            return True
        market_cap = self._market_cap(bar)
        if market_cap < self.min_market_cap:
            self._count("entry_rejections", "small_below_1b_market_cap")
            return True
        if not self._has_positive_profit(bar):
            self._count("entry_rejections", "non_positive_profit_proxy")
            return True
        if self._inferred_revenue(bar, market_cap) < self.min_inferred_revenue:
            self._count("entry_rejections", "revenue_below_100m_proxy")
            return True
        return False

    def _exit_risk(self, symbol: str, bar: Any) -> bool:
        if self.risk_index_symbol and symbol == self.risk_index_symbol:
            return False
        return bool(self._position_exit_reason(symbol, bar))

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        status_reason = self._status_or_delisting_exit_reason(symbol, bar)
        if status_reason:
            return status_reason
        if not self.enable_risk_exit:
            return ""
        return self._profit_exit_reason(symbol, self._price(bar))

    def _status_or_delisting_risk(self, symbol: str, bar: Any) -> bool:
        return bool(self._status_or_delisting_exit_reason(symbol, bar))

    def _status_or_delisting_exit_reason(self, symbol: str, bar: Any) -> str:
        if self._is_permission_excluded_symbol(symbol):
            return "excluded_permission_board"
        if not self._is_mainland_a_symbol(symbol):
            return "not_mainland_a"
        if self._bool_value(self._value(bar, "is_st", False), False):
            return "st"
        if self._bool_value(self._value(bar, "_suspended", False), False):
            return "suspended"
        if self._bool_value(self._value(bar, "status_is_suspended", False), False):
            return "suspended"
        if self._bool_value(self._value(bar, "tradable", True), True) is False:
            return "not_tradable"
        if self._bool_value(self._value(bar, "has_daily_bar", True), True) is False:
            return "no_daily_bar"
        if self._bool_value(self._value(bar, "is_listed", True), True) is False:
            return "not_listed"
        list_status = str(self._value(bar, "list_status", "L") or "L").upper()
        if list_status not in {"", "L"}:
            return "list_status"
        if self._price(bar) < self.min_price:
            return "low_price"
        if self._market_cap(bar) <= 0:
            return "missing_market_cap"
        if self._average_turnover(symbol) < self.min_adv_value:
            return "low_turnover"
        return ""

    def _is_permission_excluded_symbol(self, symbol: str) -> bool:
        return _symbol_has_prefix(str(symbol), self.excluded_board_prefixes)

    def _has_positive_profit(self, bar: Any) -> bool:
        for field in ("pe_ttm", "pe", "eps", "netprofit_margin"):
            if self._float_value(self._value(bar, field, None), 0.0) > 0:
                return True
        return not self.require_financial_fields

    def _inferred_revenue(self, bar: Any, market_cap: float) -> float:
        for field in ("ps_ttm", "ps"):
            ps_value = self._float_value(self._value(bar, field, None), 0.0)
            if ps_value > 0 and market_cap > 0:
                return market_cap / ps_value
        return self.min_inferred_revenue if not self.require_financial_fields else 0.0

    def _index_risk_off(self) -> bool:
        if not self.risk_index_symbol:
            return False
        ret = self._return(self.risk_index_symbol, self.index_drawdown_lookback)
        return ret is not None and ret <= self.index_drawdown_threshold

    def _candidate_score(self, symbol: str, bar: Any) -> float:
        return -self._market_cap(bar)

    def _profit_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        stop_loss = self._effective_stop_loss_pct(symbol)
        if stop_loss > 0 and price <= entry_price * (1.0 - stop_loss):
            return "stop_loss"
        if self.hard_take_profit_pct > 0 and price >= entry_price * (1.0 + self.hard_take_profit_pct):
            return "take_profit"
        peak_price = max(self._peak_prices.get(symbol, price), price)
        trailing_stop = self._effective_trailing_stop_pct(symbol)
        if (
            self.take_profit_pct > 0
            and trailing_stop > 0
            and peak_price >= entry_price * (1.0 + self.take_profit_pct)
            and price <= peak_price * (1.0 - trailing_stop)
        ):
            return "trailing_take_profit"
        if self._time_stop_triggered(symbol, price, entry_price):
            return "time_stop"
        return ""

    def _effective_stop_loss_pct(self, symbol: str) -> float:
        value = self.stop_loss_pct
        volatility = self._daily_volatility(symbol, self.exit_volatility_lookback)
        if volatility is not None and self.stop_volatility_multiplier > 0:
            value = max(value, volatility * self.stop_volatility_multiplier)
        if self.min_stop_loss_pct > 0:
            value = max(value, self.min_stop_loss_pct)
        if self.max_stop_loss_pct > 0:
            value = min(value, self.max_stop_loss_pct)
        return value

    def _effective_trailing_stop_pct(self, symbol: str) -> float:
        value = self.trailing_stop_pct
        volatility = self._daily_volatility(symbol, self.exit_volatility_lookback)
        if volatility is not None and self.trailing_volatility_multiplier > 0:
            value = max(value, volatility * self.trailing_volatility_multiplier)
        if self.max_trailing_stop_pct > 0:
            value = min(value, self.max_trailing_stop_pct)
        return value

    def _daily_volatility(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) <= lookback:
            return None
        prices = [self._adj(bar, "close") for bar in bars[-lookback - 1 :]]
        prices = [price for price in prices if price > 0 and math.isfinite(price)]
        if len(prices) < 3:
            return None
        returns = [prices[index] / prices[index - 1] - 1.0 for index in range(1, len(prices))]
        mean = sum(returns) / float(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / float(max(1, len(returns) - 1))
        volatility = math.sqrt(max(variance, 0.0))
        return volatility if math.isfinite(volatility) and volatility > 0 else None

    def _time_stop_triggered(self, symbol: str, price: float, entry_price: float) -> bool:
        if self.max_holding_days <= 0:
            return False
        entry_bar_count = self._entry_bar_counts.get(symbol)
        if entry_bar_count is None:
            return False
        holding_days = len(self._bars.get(symbol, [])) - entry_bar_count
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
                number = self._float_value(value, 0.0)
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
            self._entry_bar_counts[symbol] = len(self._bars.get(symbol, []))
        if fill_price > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)

    def _update_peak_price(self, symbol: str, price: float) -> None:
        if price > 0 and self._positions.get(symbol, 0) > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)

    def _clear_position_state(self, symbol: str) -> None:
        self._entry_prices.pop(symbol, None)
        self._peak_prices.pop(symbol, None)
        self._entry_bar_counts.pop(symbol, None)

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0 and math.isfinite(number):
                return number
        return 0.0

    def get_state(self) -> dict:
        state = super().get_state()
        params = dict(state.get("parameters") or {})
        params.update(
            {
                "min_positions": self.min_positions,
                "min_market_cap": self.min_market_cap,
                "min_inferred_revenue": self.min_inferred_revenue,
                "require_financial_fields": self.require_financial_fields,
                "empty_months": sorted(self.empty_months),
                "rebalance_signal_weekday": self.rebalance_signal_weekday,
                "risk_index_symbol": self.risk_index_symbol,
                "index_drawdown_lookback": self.index_drawdown_lookback,
                "index_drawdown_threshold": self.index_drawdown_threshold,
                "excluded_board_prefixes": list(self.excluded_board_prefixes),
                "enable_risk_exit": self.enable_risk_exit,
                "stop_loss_pct": self.stop_loss_pct,
                "min_stop_loss_pct": self.min_stop_loss_pct,
                "max_stop_loss_pct": self.max_stop_loss_pct,
                "stop_volatility_multiplier": self.stop_volatility_multiplier,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "trailing_volatility_multiplier": self.trailing_volatility_multiplier,
                "max_trailing_stop_pct": self.max_trailing_stop_pct,
                "hard_take_profit_pct": self.hard_take_profit_pct,
                "exit_volatility_lookback": self.exit_volatility_lookback,
                "max_holding_days": self.max_holding_days,
                "min_time_stop_return": self.min_time_stop_return,
                "risk_exit": {
                    "enabled": self.enable_risk_exit,
                    "stop_loss_pct": self.stop_loss_pct,
                    "min_stop_loss_pct": self.min_stop_loss_pct,
                    "max_stop_loss_pct": self.max_stop_loss_pct,
                    "stop_volatility_multiplier": self.stop_volatility_multiplier,
                    "take_profit_pct": self.take_profit_pct,
                    "trailing_stop_pct": self.trailing_stop_pct,
                    "trailing_volatility_multiplier": self.trailing_volatility_multiplier,
                    "max_trailing_stop_pct": self.max_trailing_stop_pct,
                    "hard_take_profit_pct": self.hard_take_profit_pct,
                    "exit_volatility_lookback": self.exit_volatility_lookback,
                    "max_holding_days": self.max_holding_days,
                    "min_time_stop_return": self.min_time_stop_return,
                },
            }
        )
        state["parameters"] = params
        state["exit_state"] = {
            "entry_prices": dict(self._entry_prices),
            "peak_prices": dict(self._peak_prices),
            "entry_bar_counts": dict(self._entry_bar_counts),
        }
        return state

    def on_stop(self, context: Any) -> None:
        super().on_stop(context)
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._entry_bar_counts.clear()
