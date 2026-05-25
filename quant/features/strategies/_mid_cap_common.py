"""Shared A-share mid-cap composite strategy logic."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from quant.features.strategies.daily_bar import DailyBarStrategy

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


ScoreSpec = Tuple[str, float, bool]


class AShareMidCapCompositeBase(DailyBarStrategy):
    def __init__(
        self,
        name: str,
        symbols: Optional[List[str]] = None,
        holding_days: int = 20,
        max_positions: int = 50,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.30,
        cap_percentile_high: float = 0.80,
        min_price: float = 5.0,
        min_turnover: float = 50000.0,
        lot_size: int = 100,
        max_lookback: int = 126,
        target_weight_slots: Optional[int] = None,
    ):
        self._symbols = [str(symbol) for symbol in symbols] if symbols else []
        self.max_positions = max(1, int(max_positions))
        self.max_position_pct = min(max(float(max_position_pct), 0.01), 1.0)
        self.cap_percentile_low = min(max(float(cap_percentile_low), 0.0), 1.0)
        self.cap_percentile_high = min(max(float(cap_percentile_high), self.cap_percentile_low), 1.0)
        self.min_price = float(min_price)
        self.min_turnover = float(min_turnover)
        self.min_avg_turnover = self.min_turnover
        self.lot_size = max(1, int(lot_size))
        self.max_lookback = max(2, int(max_lookback))
        self.target_weight_slots = max(1, int(target_weight_slots)) if target_weight_slots else None
        self.delisting_risk_guard = True
        self.min_trade_price = self.min_price
        self.liquidity_lookback = 20
        self.max_recent_suspended_days = 0
        self._last_scores: Dict[str, float] = {}
        self._risk_exited_today: set[str] = set()
        self._guard_diagnostics: Dict[str, Any] = {
            "enabled": True,
            "parameters": {
                "cap_percentile_low": self.cap_percentile_low,
                "cap_percentile_high": self.cap_percentile_high,
                "min_price": self.min_price,
                "min_turnover": self.min_turnover,
                "target_weight_slots": self.target_weight_slots,
            },
            "entry_rejections": {},
            "exit_triggers": {},
            "field_missing": {},
            "rebalance_count": 0,
            "last_candidate_count": 0,
            "last_selected_count": 0,
            "last_cap_floor": 0.0,
            "last_cap_ceiling": 0.0,
        }
        super().__init__(name, self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return self.max_lookback + 5

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return ()

    @property
    def formula_key(self) -> str:
        return self.name

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv"]

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._risk_exited_today = self._exit_risk_positions()
        if not self._check_rebalance_gate(trading_date):
            return
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_candidates = []
        excluded = set(self._risk_exited_today)
        for symbol in self._symbols:
            if symbol in excluded:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            reason = self._candidate_rejection(symbol, bar)
            if reason:
                self._count("entry_rejections", reason)
                continue
            base = self._base_snapshot(symbol, bar)
            missing = str(base.pop("missing_field", "") or "")
            if missing:
                self._count("field_missing", missing)
                self._count("entry_rejections", f"missing_{missing}")
                continue
            raw_candidates.append(base)

        banded = self._apply_cap_band(raw_candidates)
        snapshots = []
        for base in banded:
            symbol = str(base["symbol"])
            bar = self._get_last_bar(symbol)
            snapshot = self._strategy_snapshot(symbol, bar, base)
            rejection = str(snapshot.pop("rejection_reason", "") or "")
            if rejection:
                self._count("entry_rejections", rejection)
                continue
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
        target_slots = self.target_weight_slots or len(selected)
        target_value = nav * self.max_position_pct / float(max(1, target_slots))
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

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        return dict(base)

    def _base_snapshot(self, symbol: str, bar: Any) -> Dict[str, float | str]:
        total_mv = self._positive_float(self._value(bar, "total_mv"))
        circ_mv = self._positive_float(self._value(bar, "circ_mv"))
        if total_mv <= 0:
            return {"symbol": symbol, "missing_field": "total_mv"}
        if circ_mv <= 0:
            return {"symbol": symbol, "missing_field": "circ_mv"}
        return {
            "symbol": symbol,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "turnover_rate": self._turnover_rate(bar),
            "missing_field": "",
        }

    def _apply_cap_band(self, candidates: List[Dict[str, float | str]]) -> List[Dict[str, float | str]]:
        if not candidates:
            self._guard_diagnostics["last_cap_floor"] = 0.0
            self._guard_diagnostics["last_cap_ceiling"] = 0.0
            return []
        ordered = sorted(candidates, key=lambda item: (float(item["total_mv"]), str(item["symbol"])))
        denominator = float(len(ordered) - 1)
        selected = []
        for index, item in enumerate(ordered):
            percentile = 0.5 if denominator <= 0 else index / denominator
            if percentile < self.cap_percentile_low:
                self._count("entry_rejections", "below_mid_cap_band")
                continue
            if percentile > self.cap_percentile_high:
                self._count("entry_rejections", "above_mid_cap_band")
                continue
            selected.append(item)
        if selected:
            self._guard_diagnostics["last_cap_floor"] = float(min(item["total_mv"] for item in selected))
            self._guard_diagnostics["last_cap_ceiling"] = float(max(item["total_mv"] for item in selected))
        else:
            self._guard_diagnostics["last_cap_floor"] = 0.0
            self._guard_diagnostics["last_cap_ceiling"] = 0.0
        return selected

    def _score_snapshots(self, snapshots: List[Dict[str, float | str]]) -> Dict[str, float]:
        scores = {str(item["symbol"]): 0.0 for item in snapshots}
        for field, weight, higher_is_better in self.score_specs:
            ranks = self._percentile_rank(snapshots, field, higher_is_better)
            for symbol, rank in ranks.items():
                scores[symbol] += float(weight) * rank
        return scores

    @staticmethod
    def _percentile_rank(
        snapshots: List[Dict[str, float | str]],
        field: str,
        higher_is_better: bool,
    ) -> Dict[str, float]:
        ordered = sorted(
            ((str(item["symbol"]), float(item[field])) for item in snapshots),
            key=lambda item: (item[1], item[0]),
            reverse=higher_is_better,
        )
        if len(ordered) == 1:
            return {ordered[0][0]: 1.0}
        denominator = float(len(ordered) - 1)
        return {symbol: 1.0 - index / denominator for index, (symbol, _) in enumerate(ordered)}

    def _exit_risk_positions(self) -> set[str]:
        exited = set()
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
            exited.add(symbol)
        return exited

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        reason = self._position_exit_reason(symbol, bar)
        if reason:
            return reason
        if self._average_turnover(symbol) < self.min_turnover:
            return "low_turnover"
        return ""

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
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
        return ""

    def _average_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_lookback :]
        values = [self._bar_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        if not values:
            return 0.0
        return sum(values) / float(len(values))

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._positive_float(self._value(bar, "turnover"))
        if turnover > 0:
            return turnover
        return self._price(bar) * self._positive_float(self._value(bar, "volume"))

    def _turnover_rate(self, bar: Any) -> float:
        turnover_rate = self._positive_float(self._value(bar, "turnover_rate_f"))
        if turnover_rate <= 0:
            turnover_rate = self._positive_float(self._value(bar, "turnover_rate"))
        return turnover_rate

    def _return(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) <= lookback:
            return None
        current = self._adj_price(bars[-1])
        base = self._adj_price(bars[-lookback - 1])
        if current <= 0 or base <= 0:
            return None
        return current / base - 1.0

    def _skip_recent_return(self, symbol: str, lookback: int, skip: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if lookback <= skip or len(bars) <= lookback:
            return None
        end_index = -skip - 1 if skip > 0 else -1
        end_price = self._adj_price(bars[end_index])
        start_price = self._adj_price(bars[-lookback - 1])
        if end_price <= 0 or start_price <= 0:
            return None
        return end_price / start_price - 1.0

    def _volatility(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) <= lookback:
            return None
        prices = [self._adj_price(bar) for bar in bars[-lookback - 1 :]]
        prices = [price for price in prices if price > 0 and math.isfinite(price)]
        if len(prices) < 3:
            return None
        returns = [prices[index] / prices[index - 1] - 1.0 for index in range(1, len(prices))]
        mean = sum(returns) / float(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / float(max(1, len(returns) - 1))
        return math.sqrt(max(variance, 0.0)) * math.sqrt(252.0)

    def _max_drawdown(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) <= lookback:
            return None
        prices = [self._adj_price(bar) for bar in bars[-lookback - 1 :]]
        prices = [price for price in prices if price > 0 and math.isfinite(price)]
        if len(prices) < 2:
            return None
        peak = prices[0]
        worst = 0.0
        for price in prices:
            peak = max(peak, price)
            if peak > 0:
                worst = min(worst, price / peak - 1.0)
        return worst

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
            "cap_percentile_low": self.cap_percentile_low,
            "cap_percentile_high": self.cap_percentile_high,
            "min_price": self.min_price,
            "min_turnover": self.min_turnover,
            "lot_size": self.lot_size,
            "target_weight_slots": self.target_weight_slots,
            "formula_key": self.formula_key,
            "required_fields": self.required_fields,
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

    def _adj_price(self, bar: Any) -> float:
        try:
            price = float(self._adj(bar, "close"))
        except (TypeError, ValueError):
            return 0.0
        return price if math.isfinite(price) and price > 0 else 0.0

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
