"""A-share PEG undervalued reversion candidate strategy."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


DEFAULT_EXCLUDED_BOARD_PREFIXES = ("300", "301", "688", "689")


def _symbol_has_prefix(symbol: str, prefixes: tuple[str, ...]) -> bool:
    return any(str(symbol).startswith(prefix) for prefix in prefixes)


@strategy("ashare_peg_undervalued_reversion")
class ASharePegUndervaluedReversionStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 60,
        max_positions: int = 10,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.15,
        cap_percentile_high: float = 0.95,
        min_price: float = 3.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        momentum_lookback: int = 126,
        momentum_skip: int = 5,
        min_pe_ttm: float = 3.0,
        max_pe_ttm: float = 80.0,
        min_profit_growth: float = 8.0,
        max_growth_for_peg: float = 120.0,
        min_roe: float = 5.0,
        min_sales_growth: float = -20.0,
        min_momentum: float = -0.25,
        max_entry_peg: float = 0.60,
        fair_peg_exit: float = 1.05,
        excluded_board_prefixes: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.22,
        min_stop_loss_pct: float = 0.18,
        max_stop_loss_pct: float = 0.32,
        stop_volatility_multiplier: float = 4.0,
        take_profit_pct: float = 0.60,
        trailing_stop_pct: float = 0.20,
        trailing_volatility_multiplier: float = 3.0,
        max_trailing_stop_pct: float = 0.35,
        hard_take_profit_pct: float = 0.0,
        exit_volatility_lookback: int = 20,
        max_holding_days: int = 252,
        min_time_stop_return: float = 0.0,
    ):
        excluded_prefixes = DEFAULT_EXCLUDED_BOARD_PREFIXES if excluded_board_prefixes is None else tuple(
            str(prefix) for prefix in excluded_board_prefixes if str(prefix)
        )
        trade_symbols = [
            str(symbol)
            for symbol in list(symbols or [])
            if not _symbol_has_prefix(str(symbol), excluded_prefixes)
        ]
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
        self.momentum_lookback = max(2, int(momentum_lookback))
        self.momentum_skip = max(0, int(momentum_skip))
        self.min_pe_ttm = float(min_pe_ttm)
        self.max_pe_ttm = float(max_pe_ttm)
        self.min_profit_growth = float(min_profit_growth)
        self.max_growth_for_peg = max(0.01, float(max_growth_for_peg))
        self.min_roe = float(min_roe)
        self.min_sales_growth = float(min_sales_growth)
        self.min_momentum = float(min_momentum)
        self.max_entry_peg = max(0.01, float(max_entry_peg))
        self.fair_peg_exit = max(self.max_entry_peg, float(fair_peg_exit))
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
        self._bar_counts: Dict[str, int] = {}
        super().__init__(
            "ashare_peg_undervalued_reversion",
            symbols=trade_symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=self.momentum_lookback + self.momentum_skip,
            target_weight_slots=max_positions,
        )

    @property
    def _max_keep_hint(self) -> int:
        return max(
            self.momentum_lookback + self.momentum_skip + 5,
            self.exit_volatility_lookback + 5,
            self.max_holding_days + 5,
        )

    @property
    def formula_key(self) -> str:
        return "ashare_peg_undervalued_reversion"

    @property
    def required_fields(self) -> List[str]:
        return [
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "roe",
            "q_roe",
            "netprofit_yoy",
            "q_netprofit_yoy",
            "or_yoy",
            "q_sales_yoy",
            "adj_close",
        ]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        return [
            ("peg_discount", 0.35, True),
            ("inverse_peg", 0.15, True),
            ("roe", 0.15, True),
            ("profit_growth", 0.15, True),
            ("earnings_yield", 0.10, True),
            ("momentum", 0.05, True),
            ("sales_growth", 0.05, True),
        ]

    def on_data(self, context: Any, data: Any) -> None:
        symbol = data.get("symbol", "") if isinstance(data, dict) else getattr(data, "symbol", "")
        if symbol and symbol in self._symbol_set:
            self._bar_counts[str(symbol)] = self._bar_counts.get(str(symbol), 0) + 1
        super().on_data(context, data)

    def on_data_batch(self, context: Any, data: Any) -> None:
        bars = list(data.values()) if isinstance(data, dict) else list(data)
        for bar in bars:
            symbol = bar.get("symbol", "") if isinstance(bar, dict) else getattr(bar, "symbol", "")
            if symbol and symbol in self._symbol_set:
                self._bar_counts[str(symbol)] = self._bar_counts.get(str(symbol), 0) + 1
        super().on_data_batch(context, bars)

    def on_fill(self, context: Any, fill: Any) -> None:
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

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        if pe_ttm <= 0:
            return {"symbol": symbol, "missing_field": "pe_ttm"}
        if pe_ttm < self.min_pe_ttm:
            return {"symbol": symbol, "rejection_reason": "pe_too_low"}
        if pe_ttm > self.max_pe_ttm:
            return {"symbol": symbol, "rejection_reason": "pe_too_high"}

        profit_growth = self._first_finite(bar, "q_netprofit_yoy", "netprofit_yoy")
        if profit_growth is None:
            return {"symbol": symbol, "missing_field": "profit_growth"}
        if profit_growth < self.min_profit_growth:
            return {"symbol": symbol, "rejection_reason": "weak_profit_growth"}

        peg = self._peg_from_values(pe_ttm, profit_growth)
        if peg is None:
            return {"symbol": symbol, "missing_field": "peg"}
        if peg > self.max_entry_peg:
            return {"symbol": symbol, "rejection_reason": "peg_too_high"}

        roe = self._first_finite(bar, "q_roe", "roe")
        if roe is None:
            return {"symbol": symbol, "missing_field": "roe"}
        if roe < self.min_roe:
            return {"symbol": symbol, "rejection_reason": "weak_roe"}

        sales_growth = self._first_finite(bar, "q_sales_yoy", "or_yoy")
        sales_growth = 0.0 if sales_growth is None else sales_growth
        if sales_growth < self.min_sales_growth:
            return {"symbol": symbol, "rejection_reason": "weak_sales_growth"}

        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if momentum < self.min_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_momentum"}

        clipped_growth = self._clip(profit_growth, -100.0, self.max_growth_for_peg)
        return {
            **base,
            "pe_ttm": pe_ttm,
            "peg": peg,
            "inverse_peg": 1.0 / peg,
            "peg_discount": max(0.0, self.fair_peg_exit - peg),
            "profit_growth": clipped_growth,
            "sales_growth": self._clip(sales_growth, -100.0, 200.0),
            "roe": self._clip(roe, -50.0, 80.0),
            "earnings_yield": 1.0 / pe_ttm,
            "momentum": momentum,
            "missing_field": "",
        }

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        if self._is_permission_excluded_symbol(symbol):
            return "excluded_permission_board"
        reason = AShareMidCapCompositeBase._position_exit_reason(self, symbol, bar)
        if reason:
            return reason
        if self._average_turnover(symbol) < self.min_turnover:
            return "low_turnover"
        return ""

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if self._is_permission_excluded_symbol(symbol):
            return "excluded_permission_board"
        status_reason = AShareMidCapCompositeBase._position_exit_reason(self, symbol, bar)
        if status_reason:
            return status_reason
        if self._positions.get(symbol, 0) > 0:
            valuation_reason = self._valuation_exit_reason(bar)
            if valuation_reason:
                return valuation_reason
        if not self.enable_risk_exit:
            return ""
        return self._profit_exit_reason(symbol, self._price(bar))

    def _exit_risk_positions(self) -> set[str]:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            price = self._get_last_price(symbol)
            self._update_peak_price(symbol, price)
            reason = self._position_exit_reason(symbol, bar)
            if not reason:
                continue
            sell_quantity = int(quantity)
            if sell_quantity <= 0:
                self._count("exit_triggers", "dust_position")
                continue
            order_id = self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)
            if order_id:
                self._count("exit_triggers", reason)
                exited.add(symbol)
        return exited

    def _valuation_exit_reason(self, bar: Any) -> str:
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        if pe_ttm <= 0:
            return ""
        profit_growth = self._first_finite(bar, "q_netprofit_yoy", "netprofit_yoy")
        if profit_growth is None:
            return ""
        if profit_growth < self.min_profit_growth:
            return "growth_broken"
        peg = self._peg_from_values(pe_ttm, profit_growth)
        if peg is not None and peg >= self.fair_peg_exit:
            return "peg_reversion"
        return ""

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
        bars = self._day_data.get(symbol, [])
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
        holding_days = self._bar_counts.get(symbol, 0) - entry_bar_count
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
                number = self._float_or_zero(value)
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
            self._entry_bar_counts[symbol] = self._bar_counts.get(symbol, 0)
        if fill_price > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)

    def _update_peak_price(self, symbol: str, price: float) -> None:
        if price > 0 and self._positions.get(symbol, 0) > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)

    def _clear_position_state(self, symbol: str) -> None:
        self._entry_prices.pop(symbol, None)
        self._peak_prices.pop(symbol, None)
        self._entry_bar_counts.pop(symbol, None)

    def _is_permission_excluded_symbol(self, symbol: str) -> bool:
        return _symbol_has_prefix(str(symbol), self.excluded_board_prefixes)

    def _peg_from_values(self, pe_ttm: float, profit_growth: float) -> Optional[float]:
        growth = min(float(profit_growth), self.max_growth_for_peg)
        if growth <= 0 or pe_ttm <= 0:
            return None
        peg = float(pe_ttm) / growth
        return peg if math.isfinite(peg) and peg > 0 else None

    @staticmethod
    def _first_finite(data: Any, *fields: str) -> Optional[float]:
        for field in fields:
            value = AShareMidCapCompositeBase._value(data, field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                return number
        return None

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return min(max(float(value), float(low)), float(high))

    @staticmethod
    def _float_or_zero(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

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

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
            {
                "momentum_lookback": self.momentum_lookback,
                "momentum_skip": self.momentum_skip,
                "min_pe_ttm": self.min_pe_ttm,
                "max_pe_ttm": self.max_pe_ttm,
                "min_profit_growth": self.min_profit_growth,
                "max_growth_for_peg": self.max_growth_for_peg,
                "min_roe": self.min_roe,
                "min_sales_growth": self.min_sales_growth,
                "min_momentum": self.min_momentum,
                "max_entry_peg": self.max_entry_peg,
                "fair_peg_exit": self.fair_peg_exit,
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
        return params

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "exit_state": {
                "entry_prices": dict(self._entry_prices),
                "peak_prices": dict(self._peak_prices),
                "entry_bar_counts": dict(self._entry_bar_counts),
                "bar_counts": dict(self._bar_counts),
            }
        }

    def _on_stop_cleanup(self) -> None:
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._entry_bar_counts.clear()
        self._bar_counts.clear()
