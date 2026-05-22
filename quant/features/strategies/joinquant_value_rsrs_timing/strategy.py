"""JoinQuant-inspired A-share value selection with RSRS timing."""

from __future__ import annotations

from datetime import date
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("joinquant_value_rsrs_timing")
class JoinquantValueRsrsTimingStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timing_symbol: str = "000300",
        holding_days: int = 20,
        max_positions: int = 20,
        max_position_pct: float = 0.98,
        min_price: float = 5.0,
        min_turnover: float = 50000.0,
        rsrs_window: int = 18,
        rsrs_zscore_window: int = 120,
        rsrs_entry: float = 0.7,
        rsrs_exit: float = -0.7,
        stop_loss_pct: float = 0.10,
        take_profit_pct: float = 0.20,
        trailing_stop_pct: float = 0.08,
        lot_size: int = 100,
    ):
        self.timing_symbol = str(timing_symbol)
        base_symbols = [str(symbol) for symbol in (symbols or [])]
        if self.timing_symbol not in base_symbols:
            base_symbols.append(self.timing_symbol)
        self.trade_symbols = [symbol for symbol in base_symbols if symbol != self.timing_symbol]
        self.max_positions = max(1, int(max_positions))
        self.max_position_pct = min(max(float(max_position_pct), 0.01), 1.0)
        self.min_price = float(min_price)
        self.min_turnover = float(min_turnover)
        self.rsrs_window = max(3, int(rsrs_window))
        self.rsrs_zscore_window = max(5, int(rsrs_zscore_window))
        self.rsrs_entry = float(rsrs_entry)
        self.rsrs_exit = float(rsrs_exit)
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.lot_size = max(1, int(lot_size))
        self._risk_on = False
        self._last_rsrs_score: Optional[float] = None
        self._last_rsrs_beta: Optional[float] = None
        self._last_rsrs_r2: Optional[float] = None
        self._last_scores: Dict[str, float] = {}
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._guard_diagnostics: Dict[str, Any] = {
            "enabled": True,
            "parameters": {
                "min_price": self.min_price,
                "min_turnover": self.min_turnover,
                "timing_symbol": self.timing_symbol,
                "rsrs_window": self.rsrs_window,
                "rsrs_zscore_window": self.rsrs_zscore_window,
                "rsrs_entry": self.rsrs_entry,
                "rsrs_exit": self.rsrs_exit,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
            },
            "entry_rejections": {},
            "exit_triggers": {},
            "field_missing": {},
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected_count": 0,
        }
        super().__init__("joinquant_value_rsrs_timing", list(dict.fromkeys(base_symbols)), holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return max(self.rsrs_window + self.rsrs_zscore_window + 5, 90)

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        was_risk_on = self._risk_on
        exited = self._exit_risk_positions()
        risk_on = self._update_rsrs_state()
        if not risk_on:
            self._liquidate_trade_positions(exclude=exited)
            self._reset_rebalance_gate()
            return
        force_rebalance = not was_risk_on
        if not force_rebalance and not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date, pending_exit_symbols=exited):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(
        self,
        context: "Context",
        trading_date: date,
        pending_exit_symbols: Optional[set[str]] = None,
    ) -> bool:
        pending_exit_symbols = pending_exit_symbols or set()
        snapshots = []
        for symbol in self.trade_symbols:
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            reason = self._candidate_rejection(symbol, bar)
            if reason:
                self._count("entry_rejections", reason)
                continue
            snapshot = self._candidate_snapshot(symbol, bar)
            missing = str(snapshot.pop("missing_field", "") or "")
            if missing:
                self._count("field_missing", missing)
                self._count("entry_rejections", f"missing_{missing}")
                continue
            snapshots.append(snapshot)

        self._guard_diagnostics["rebalance_count"] += 1
        self._guard_diagnostics["last_candidate_count"] = len(snapshots)
        if not snapshots:
            self._guard_diagnostics["last_selected_count"] = 0
            return False

        scores = self._score_snapshots(snapshots)
        selected = [symbol for symbol, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.max_positions]]
        selected_set = set(selected)
        self._last_scores = scores
        self._guard_diagnostics["last_selected_count"] = len(selected)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in pending_exit_symbols and symbol not in selected_set:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0 or not selected:
            return bool(selected)
        target_value = nav * self.max_position_pct / float(len(selected))
        for symbol in selected:
            if symbol in pending_exit_symbols:
                continue
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
        return bool(selected)

    def on_fill(self, context: "Context", fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if not symbol or symbol == self.timing_symbol:
            return
        current_quantity = float(self._positions.get(symbol, 0) or 0)
        side = str(getattr(fill, "side", "") or "").upper()
        fill_quantity = float(getattr(fill, "quantity", 0) or 0)
        fill_price = self._fill_price(fill)
        if side == "BUY" and fill_quantity > 0:
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
            if fill_price > 0:
                self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

    def _update_rsrs_state(self) -> bool:
        computed = self._compute_rsrs_score()
        if computed is None:
            return self._risk_on
        score, beta, r2 = computed
        self._last_rsrs_score = score
        self._last_rsrs_beta = beta
        self._last_rsrs_r2 = r2
        if score >= self.rsrs_entry:
            self._risk_on = True
        elif score <= self.rsrs_exit:
            self._risk_on = False
        return self._risk_on

    def _compute_rsrs_score(self) -> Optional[Tuple[float, float, float]]:
        bars = self._day_data.get(self.timing_symbol, [])
        needed = self.rsrs_window + self.rsrs_zscore_window - 1
        if len(bars) < needed:
            return None
        beta_r2 = []
        recent = bars[-needed:]
        for end in range(self.rsrs_window, len(recent) + 1):
            item = self._ols_high_on_low(recent[end - self.rsrs_window : end])
            if item is not None:
                beta_r2.append(item)
        if len(beta_r2) < self.rsrs_zscore_window:
            return None
        betas = [item[0] for item in beta_r2[-self.rsrs_zscore_window :]]
        last_beta, last_r2 = beta_r2[-1]
        mean_beta = sum(betas) / float(len(betas))
        variance = sum((value - mean_beta) ** 2 for value in betas) / float(len(betas))
        std_beta = math.sqrt(variance)
        if std_beta <= 1e-12:
            return None
        zscore = (last_beta - mean_beta) / std_beta
        score = zscore * max(0.0, min(last_r2, 1.0))
        if not all(math.isfinite(value) for value in (score, last_beta, last_r2)):
            return None
        return score, last_beta, last_r2

    def _ols_high_on_low(self, bars: List[Any]) -> Optional[Tuple[float, float]]:
        lows = [self._signal_price(bar, "low") for bar in bars]
        highs = [self._signal_price(bar, "high") for bar in bars]
        pairs = [(low, high) for low, high in zip(lows, highs) if low > 0 and high > 0]
        if len(pairs) < self.rsrs_window:
            return None
        x_values = [item[0] for item in pairs]
        y_values = [item[1] for item in pairs]
        x_mean = sum(x_values) / float(len(x_values))
        y_mean = sum(y_values) / float(len(y_values))
        var_x = sum((value - x_mean) ** 2 for value in x_values)
        if var_x <= 1e-12:
            return None
        cov_xy = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
        beta = cov_xy / var_x
        alpha = y_mean - beta * x_mean
        ss_tot = sum((value - y_mean) ** 2 for value in y_values)
        if ss_tot <= 1e-12:
            r2 = 0.0
        else:
            ss_res = sum((y - (alpha + beta * x)) ** 2 for x, y in pairs)
            r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
        return beta, r2

    def _exit_risk_positions(self) -> set[str]:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            price = self._get_last_price(symbol)
            if price > 0:
                self._update_peak_price(symbol, price)
            reason = self._position_exit_reason(symbol, bar)
            if not reason:
                continue
            self._count("exit_triggers", reason)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _liquidate_trade_positions(self, exclude: Optional[set[str]] = None) -> None:
        excluded = exclude or set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0 or symbol == self.timing_symbol or symbol in excluded:
                continue
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def _reset_rebalance_gate(self) -> None:
        self._last_rebalance_date = None
        self._days_since_rebalance = 0

    def _candidate_snapshot(self, symbol: str, bar: Any) -> Dict[str, float | str]:
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        if pe_ttm <= 0:
            pe_ttm = self._positive_float(self._value(bar, "pe"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        if ps_ttm <= 0:
            ps_ttm = self._positive_float(self._value(bar, "ps"))
        fields = {
            "pe_ttm": pe_ttm,
            "pb": self._positive_float(self._value(bar, "pb")),
            "ps_ttm": ps_ttm,
            "dv_ttm": self._nonnegative_float(self._value(bar, "dv_ttm")),
            "total_mv": self._positive_float(self._value(bar, "total_mv")),
            "circ_mv": self._positive_float(self._value(bar, "circ_mv")),
        }
        for field, value in fields.items():
            if field == "dv_ttm":
                continue
            if value <= 0:
                return {"symbol": symbol, "missing_field": field}
        return {"symbol": symbol, **fields, "missing_field": ""}

    def _score_snapshots(self, snapshots: List[Dict[str, float | str]]) -> Dict[str, float]:
        scores = {str(item["symbol"]): 0.0 for item in snapshots}
        weights = {
            "pb": (0.30, False),
            "pe_ttm": (0.25, False),
            "ps_ttm": (0.20, False),
            "dv_ttm": (0.15, True),
            "circ_mv": (0.10, True),
        }
        for field, (weight, higher_is_better) in weights.items():
            ranks = self._percentile_rank(snapshots, field, higher_is_better)
            for symbol, rank in ranks.items():
                scores[symbol] += weight * rank
        return scores

    @staticmethod
    def _percentile_rank(snapshots: List[Dict[str, float | str]], field: str, higher_is_better: bool) -> Dict[str, float]:
        ordered = sorted(
            ((str(item["symbol"]), float(item[field])) for item in snapshots),
            key=lambda item: (item[1], item[0]),
            reverse=higher_is_better,
        )
        if len(ordered) == 1:
            return {ordered[0][0]: 1.0}
        denominator = float(len(ordered) - 1)
        return {symbol: 1.0 - index / denominator for index, (symbol, _) in enumerate(ordered)}

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        reason = self._security_status_rejection(symbol, bar)
        if reason:
            return reason
        if self._price(bar) < self.min_price:
            return "low_price"
        if self._bar_turnover(bar) < self.min_turnover:
            return "low_turnover"
        return ""

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        reason = self._security_status_rejection(symbol, bar)
        if reason:
            return reason
        profit_reason = self._profit_exit_reason(symbol, self._price(bar))
        if profit_reason:
            return profit_reason
        if self._price(bar) < self.min_price:
            return "low_price"
        if self._bar_turnover(bar) < self.min_turnover:
            return "low_turnover"
        return ""

    def _security_status_rejection(self, symbol: str, bar: Any) -> str:
        if symbol == self.timing_symbol:
            return "timing_symbol"
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
        return ""

    def _profit_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0 or self._positions.get(symbol, 0) <= 0:
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
        return ""

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
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if number > 0 and math.isfinite(number):
                    return number
        return self._entry_prices.get(symbol, 0.0)

    def _update_peak_price(self, symbol: str, price: float) -> None:
        if price > 0 and self._positions.get(symbol, 0) > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)

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

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._positive_float(self._value(bar, "turnover"))
        if turnover > 0:
            return turnover
        return self._price(bar) * self._positive_float(self._value(bar, "volume"))

    def _signal_price(self, bar: Any, field: str) -> float:
        adjusted = self._positive_float(self._value(bar, f"adj_{field}"))
        if adjusted > 0:
            return adjusted
        return self._positive_float(self._value(bar, field))

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._guard_diagnostics,
            "entry_rejections": dict(self._guard_diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._guard_diagnostics.get("exit_triggers") or {}),
            "field_missing": dict(self._guard_diagnostics.get("field_missing") or {}),
            "rsrs": {
                "risk_on": self._risk_on,
                "score": self._last_rsrs_score,
                "beta": self._last_rsrs_beta,
                "r_squared": self._last_rsrs_r2,
            },
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbols": list(self._symbols),
            "trade_symbols": list(self.trade_symbols),
            "timing_symbol": self.timing_symbol,
            "holding_days": self.holding_days,
            "max_positions": self.max_positions,
            "max_position_pct": self.max_position_pct,
            "min_price": self.min_price,
            "min_turnover": self.min_turnover,
            "rsrs_window": self.rsrs_window,
            "rsrs_zscore_window": self.rsrs_zscore_window,
            "rsrs_entry": self.rsrs_entry,
            "rsrs_exit": self.rsrs_exit,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
            "lot_size": self.lot_size,
            "formula_key": "joinquant_value_rsrs_timing",
            "required_fields": ["high", "low", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv"],
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "risk_on": self._risk_on,
            "last_rsrs_score": self._last_rsrs_score,
            "last_scores": dict(self._last_scores),
            "entry_prices": dict(self._entry_prices),
            "peak_prices": dict(self._peak_prices),
        }

    def _on_stop_cleanup(self) -> None:
        self._entry_prices.clear()
        self._peak_prices.clear()

    def _count(self, bucket: str, key: str) -> None:
        values = self._guard_diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

    @staticmethod
    def _value(data: Any, field: str, default: Any = None) -> Any:
        if isinstance(data, dict):
            return data.get(field, default)
        return getattr(data, field, default)

    @staticmethod
    def _positive_float(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number > 0 else 0.0

    @staticmethod
    def _nonnegative_float(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number >= 0 else 0.0

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

    @staticmethod
    def _is_mainland_a_symbol(symbol: str) -> bool:
        text = str(symbol)
        if len(text) != 6 or not text.isdigit():
            return False
        return text.startswith(("0", "2", "3", "6")) and not text.startswith("200")
