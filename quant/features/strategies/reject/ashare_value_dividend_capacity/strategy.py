"""A-share value dividend capacity strategy."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("ashare_value_dividend_capacity")
class AShareValueDividendCapacityStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 50,
        max_position_pct: float = 1.0,
        min_price: float = 5.0,
        min_total_mv: float = 300000.0,
        min_circ_mv: float = 200000.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
    ):
        self._symbols = [str(symbol) for symbol in symbols] if symbols else []
        self.max_positions = max(1, int(max_positions))
        self.max_position_pct = min(max(float(max_position_pct), 0.01), 1.0)
        self.min_price = float(min_price)
        self.min_total_mv = float(min_total_mv)
        self.min_circ_mv = float(min_circ_mv)
        self.min_turnover = float(min_turnover)
        self.lot_size = max(1, int(lot_size))
        self._last_scores: Dict[str, float] = {}
        self._guard_diagnostics = {
            "enabled": True,
            "parameters": {
                "min_price": self.min_price,
                "min_total_mv": self.min_total_mv,
                "min_circ_mv": self.min_circ_mv,
                "min_turnover": self.min_turnover,
            },
            "entry_rejections": {},
            "exit_triggers": {},
            "field_missing": {},
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected_count": 0,
        }
        super().__init__("ashare_value_dividend_capacity", self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return 25

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._exit_risk_positions()
        if not self._check_rebalance_gate(trading_date):
            return
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        snapshots = []
        for symbol in self._symbols:
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            reason = self._candidate_rejection(symbol, bar)
            if reason:
                self._count("entry_rejections", reason)
                continue
            snapshot = self._candidate_snapshot(symbol, bar)
            missing = snapshot.pop("missing_field", "")
            if missing:
                self._count("field_missing", missing)
                self._count("entry_rejections", f"missing_{missing}")
                continue
            snapshots.append(snapshot)
        self._guard_diagnostics["rebalance_count"] += 1
        self._guard_diagnostics["last_candidate_count"] = len(snapshots)
        if not snapshots:
            self._guard_diagnostics["last_selected_count"] = 0
            return

        scores = self._score_snapshots(snapshots)
        selected = [symbol for symbol, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.max_positions]]
        selected_set = set(selected)
        self._last_scores = scores
        self._guard_diagnostics["last_selected_count"] = len(selected)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_set:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_value = nav * self.max_position_pct / float(max(1, len(selected)))
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

    def _exit_risk_positions(self) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            reason = self._position_exit_reason(symbol, bar)
            if not reason:
                continue
            self._count("exit_triggers", reason)
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def _candidate_snapshot(self, symbol: str, bar: Any) -> Dict[str, float | str]:
        fields = {
            "pe_ttm": self._positive_float(self._value(bar, "pe_ttm")),
            "pb": self._positive_float(self._value(bar, "pb")),
            "ps_ttm": self._positive_float(self._value(bar, "ps_ttm")),
            "dv_ttm": self._positive_float(self._value(bar, "dv_ttm")),
            "total_mv": self._positive_float(self._value(bar, "total_mv")),
            "circ_mv": self._positive_float(self._value(bar, "circ_mv")),
        }
        for field, value in fields.items():
            if value <= 0:
                return {"symbol": symbol, "missing_field": field}
        return {"symbol": symbol, **fields, "missing_field": ""}

    def _score_snapshots(self, snapshots: List[Dict[str, float | str]]) -> Dict[str, float]:
        scores = {str(item["symbol"]): 0.0 for item in snapshots}
        weights = {
            "pe_ttm": (0.25, False),
            "pb": (0.25, False),
            "ps_ttm": (0.20, False),
            "dv_ttm": (0.20, True),
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
        reason = self._position_exit_reason(symbol, bar)
        if reason:
            return reason
        if self._positive_float(self._value(bar, "total_mv")) < self.min_total_mv:
            return "small_total_mv"
        if self._positive_float(self._value(bar, "circ_mv")) < self.min_circ_mv:
            return "small_circ_mv"
        if self._bar_turnover(bar) < self.min_turnover:
            return "low_turnover"
        return ""

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if not self._is_mainland_a_symbol(symbol):
            return "not_mainland_a"
        if self._bool_value(self._value(bar, "is_st", False), False):
            return "st"
        if self._bool_value(self._value(bar, "_suspended", False), False):
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
        return ""

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._positive_float(self._value(bar, "turnover"))
        if turnover > 0:
            return turnover
        return self._price(bar) * self._positive_float(self._value(bar, "volume"))

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._guard_diagnostics,
            "entry_rejections": dict(self._guard_diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._guard_diagnostics.get("exit_triggers") or {}),
            "field_missing": dict(self._guard_diagnostics.get("field_missing") or {}),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "holding_days": self.holding_days,
            "max_positions": self.max_positions,
            "max_position_pct": self.max_position_pct,
            "min_price": self.min_price,
            "min_total_mv": self.min_total_mv,
            "min_circ_mv": self.min_circ_mv,
            "min_turnover": self.min_turnover,
            "formula_key": "ashare_value_dividend_capacity",
            "required_fields": ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv"],
        }

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
