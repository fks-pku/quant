"""Strict CSI300 internal index-enhanced multifactor strategy."""

from __future__ import annotations

import bisect
import math
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_csi300_strict_index_enhanced"
ScoreSpec = Tuple[str, float, bool]


@strategy(STRATEGY_NAME)
class AShareCsi300StrictIndexEnhancedStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        index_weights: Optional[Iterable[Dict[str, Any]]] = None,
        benchmark_symbol: str = "000300",
        holding_days: int = 20,
        max_positions: int = 90,
        target_exposure: float = 0.98,
        active_tilt: float = 3.10,
        min_weight_multiplier: float = 0.0,
        max_weight_multiplier: float = 7.20,
        max_single_weight: float = 0.11,
        min_price: float = 2.0,
        min_turnover: float = 50_000.0,
        max_volatility: float = 0.0,
        min_drawdown: float = -1.0,
        min_recent_momentum: float = -1.0,
        min_long_momentum: float = -1.0,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.40,
        take_profit_pct: float = 1.00,
        trailing_stop_pct: float = 0.30,
        lot_size: int = 100,
    ):
        self.benchmark_symbol = str(benchmark_symbol)
        self.trade_symbols = [str(symbol) for symbol in list(symbols or []) if str(symbol) != self.benchmark_symbol]
        all_symbols = list(dict.fromkeys([*self.trade_symbols, self.benchmark_symbol]))
        super().__init__(STRATEGY_NAME, all_symbols, holding_days=holding_days)
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = self._bool_value(risk_exit_config.get("enabled"), bool(enable_risk_exit))
        self.max_positions = max(1, int(max_positions))
        self.target_exposure = min(max(float(target_exposure), 0.01), 1.0)
        self.active_tilt = max(0.0, float(active_tilt))
        self.min_weight_multiplier = max(0.0, float(min_weight_multiplier))
        self.max_weight_multiplier = max(self.min_weight_multiplier, float(max_weight_multiplier))
        self.max_single_weight = min(max(float(max_single_weight), 0.001), 0.20)
        self.min_price = float(min_price)
        self.min_turnover = float(min_turnover)
        self.min_avg_turnover = self.min_turnover
        self.max_volatility = max(0.0, float(max_volatility))
        self.min_drawdown = min(0.0, float(min_drawdown))
        self.min_recent_momentum = float(min_recent_momentum)
        self.min_long_momentum = float(min_long_momentum)
        self.enable_risk_exit = bool(enable_risk_exit)
        self.stop_loss_pct = float(risk_exit_config.get("stop_loss_pct", stop_loss_pct)) if self.enable_risk_exit else 0.0
        self.take_profit_pct = float(risk_exit_config.get("take_profit_pct", take_profit_pct)) if self.enable_risk_exit else 0.0
        self.trailing_stop_pct = float(risk_exit_config.get("trailing_stop_pct", trailing_stop_pct)) if self.enable_risk_exit else 0.0
        self.lot_size = max(1, int(lot_size))
        self.momentum_lookback = 252
        self.momentum_skip = 21
        self.recent_momentum_lookback = 60
        self.volatility_lookback = 120
        self.drawdown_lookback = 120
        self.liquidity_lookback = 20
        self.delisting_risk_guard = True
        self.min_trade_price = self.min_price
        self.max_recent_suspended_days = 0
        self._index_weight_dates, self._index_weights = self._prepare_index_weights(index_weights or [])
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._last_scores: Dict[str, float] = {}
        self._last_target_weights: Dict[str, float] = {}
        self._risk_exited_today: set[str] = set()
        self._guard_diagnostics: Dict[str, Any] = {
            "enabled": True,
            "parameters": self._get_parameters(),
            "entry_rejections": {},
            "exit_triggers": {},
            "field_missing": {},
            "rebalance_count": 0,
            "last_index_weight_date": "",
            "last_index_constituent_count": 0,
            "last_candidate_count": 0,
            "last_selected_count": 0,
            "last_total_selected_benchmark_weight": 0.0,
            "last_estimated_active_share": 0.0,
        }

    @property
    def _max_keep_hint(self) -> int:
        return self.momentum_lookback + 10

    def required_snapshot_symbols(self) -> List[str]:
        return []

    @property
    def required_fields(self) -> List[str]:
        return [
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ttm",
            "roe",
            "grossprofit_margin",
            "debt_to_assets",
            "adj_close",
        ]

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("momentum", 0.34, True),
            ("recent_momentum", 0.20, True),
            ("roe", 0.16, True),
            ("volatility", 0.10, False),
            ("pb", 0.08, False),
            ("dv_ttm", 0.06, True),
            ("index_weight", 0.06, True),
        ]

    def on_after_trading(self, context: Any, trading_date: date) -> None:
        self._risk_exited_today = self._exit_risk_positions(trading_date)
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: Any, trading_date: date) -> bool:
        weight_date, weights = self._weights_for_date(trading_date)
        self._guard_diagnostics["rebalance_count"] += 1
        self._guard_diagnostics["last_index_weight_date"] = weight_date.isoformat() if weight_date else ""
        self._guard_diagnostics["last_index_constituent_count"] = len(weights)
        if not weights:
            self._guard_diagnostics["last_candidate_count"] = 0
            self._guard_diagnostics["last_selected_count"] = 0
            return False
        snapshots = self._eligible_snapshots(weights)
        self._guard_diagnostics["last_candidate_count"] = len(snapshots)
        if not snapshots:
            self._guard_diagnostics["last_selected_count"] = 0
            return False
        scores = self._score_snapshots(snapshots)
        targets = self._target_weights(snapshots, scores)
        selected_set = set(targets)
        self._last_scores = dict(scores)
        self._last_target_weights = dict(targets)
        self._guard_diagnostics["last_selected_count"] = len(targets)
        self._guard_diagnostics["last_total_selected_benchmark_weight"] = sum(weights.get(symbol, 0.0) for symbol in selected_set)
        self._guard_diagnostics["last_estimated_active_share"] = self._estimated_active_share(targets, weights)

        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol != self.benchmark_symbol and symbol not in selected_set and symbol not in self._risk_exited_today:
                self._sell_whole_position(symbol)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return bool(targets)
        for symbol, target_weight in targets.items():
            if symbol in self._risk_exited_today:
                continue
            price = self._get_last_price(symbol)
            if price <= 0:
                continue
            target_quantity = self._round_lot(nav * target_weight / price)
            current_quantity = int(self._positions.get(symbol, 0) or 0)
            delta = target_quantity - current_quantity
            if delta > 0:
                self.buy(symbol, delta, "MARKET", price)
            elif delta < 0:
                self.sell(symbol, abs(delta), "MARKET", price)
        return bool(targets)

    def _eligible_snapshots(self, weights: Dict[str, float]) -> List[Dict[str, float | str]]:
        snapshots = []
        excluded = set(self._risk_exited_today)
        for symbol, index_weight in weights.items():
            if symbol in excluded:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                self._count("entry_rejections", "no_current_bar")
                continue
            reason = self._candidate_rejection(symbol, bar)
            if reason:
                self._count("entry_rejections", reason)
                continue
            snapshot = self._snapshot(symbol, bar, index_weight)
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
        return snapshots

    def _snapshot(self, symbol: str, bar: Any, index_weight: float) -> Dict[str, float | str]:
        total_mv = self._positive_float(self._value(bar, "total_mv"))
        circ_mv = self._positive_float(self._value(bar, "circ_mv"))
        pb = self._positive_float(self._value(bar, "pb"))
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm")) or self._positive_float(self._value(bar, "pe"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm")) or self._positive_float(self._value(bar, "ps"))
        dv_ttm = self._nonnegative_float(self._value(bar, "dv_ttm"))
        roe = self._finite_or_zero(self._value(bar, "roe"))
        grossprofit_margin = self._finite_or_zero(self._value(bar, "grossprofit_margin"))
        debt_to_assets = self._finite_or_zero(self._value(bar, "debt_to_assets"))
        required = {
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "pb": pb,
            "pe_ttm": pe_ttm,
            "ps_ttm": ps_ttm,
        }
        for field, value in required.items():
            if value <= 0:
                return {"symbol": symbol, "missing_field": field}
        recent_momentum = self._return(symbol, self.recent_momentum_lookback)
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        volatility = self._volatility(symbol, self.volatility_lookback)
        drawdown = self._max_drawdown(symbol, self.drawdown_lookback)
        if recent_momentum is None:
            self._count("field_missing", "recent_momentum")
            recent_momentum = 0.0
        if momentum is None:
            self._count("field_missing", "momentum")
            momentum = 0.0
        if volatility is None:
            self._count("field_missing", "volatility")
            volatility = 0.35
        if drawdown is None:
            self._count("field_missing", "drawdown")
            drawdown = 0.0
        if recent_momentum < self.min_recent_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_recent_momentum"}
        if momentum < self.min_long_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_long_momentum"}
        if self.max_volatility > 0 and volatility > self.max_volatility:
            return {"symbol": symbol, "rejection_reason": "high_volatility"}
        if drawdown < self.min_drawdown:
            return {"symbol": symbol, "rejection_reason": "deep_recent_drawdown"}
        return {
            "symbol": symbol,
            "index_weight": index_weight,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "pb": pb,
            "pe_ttm": pe_ttm,
            "ps_ttm": ps_ttm,
            "dv_ttm": dv_ttm,
            "roe": roe,
            "grossprofit_margin": grossprofit_margin,
            "debt_to_assets": debt_to_assets,
            "recent_momentum": recent_momentum,
            "momentum": momentum,
            "volatility": volatility,
            "drawdown": drawdown,
            "missing_field": "",
        }

    def _target_weights(self, snapshots: List[Dict[str, float | str]], scores: Dict[str, float]) -> Dict[str, float]:
        if not snapshots:
            return {}
        mean_score = sum(scores.get(str(item["symbol"]), 0.0) for item in snapshots) / float(len(snapshots))
        max_abs = max(abs(scores.get(str(item["symbol"]), 0.0) - mean_score) for item in snapshots) or 1.0
        rows = []
        for item in snapshots:
            symbol = str(item["symbol"])
            index_weight = float(item["index_weight"])
            score = scores.get(symbol, 0.0)
            centered = (score - mean_score) / max_abs
            multiplier = 1.0 + self.active_tilt * centered
            multiplier = min(max(multiplier, self.min_weight_multiplier), self.max_weight_multiplier)
            tilted_weight = index_weight * multiplier
            rows.append((symbol, index_weight, score, tilted_weight))
        selected = sorted(rows, key=lambda row: (-row[3], -row[2], row[0]))[: self.max_positions]
        total = sum(row[3] for row in selected)
        if total <= 0:
            return {}
        raw_targets = {symbol: min(self.max_single_weight, self.target_exposure * tilted / total) for symbol, _, _, tilted in selected}
        total_after_cap = sum(raw_targets.values())
        if total_after_cap <= 0:
            return {}
        if total_after_cap < self.target_exposure:
            scalable = [symbol for symbol, weight in raw_targets.items() if weight < self.max_single_weight]
            remainder = self.target_exposure - total_after_cap
            for symbol in scalable:
                if remainder <= 0:
                    break
                room = self.max_single_weight - raw_targets[symbol]
                add = min(room, remainder / float(max(1, len(scalable))))
                raw_targets[symbol] += add
                remainder -= add
        return {symbol: weight for symbol, weight in raw_targets.items() if weight > 0}

    def _score_snapshots(self, snapshots: List[Dict[str, float | str]]) -> Dict[str, float]:
        scores = {str(item["symbol"]): 0.0 for item in snapshots}
        for field, weight, higher_is_better in self.score_specs:
            ranks = self._percentile_rank(snapshots, field, higher_is_better)
            for symbol, rank in ranks.items():
                scores[symbol] += float(weight) * rank
        return scores

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        reason = self._status_exit_reason(symbol, bar)
        if reason:
            return reason
        if self._average_turnover(symbol) < self.min_turnover:
            return "low_turnover"
        return ""

    def _position_exit_reason(self, symbol: str, bar: Any, trading_date: Optional[date] = None) -> str:
        reason = self._status_exit_reason(symbol, bar)
        if reason:
            return reason
        if trading_date is not None:
            _, weights = self._weights_for_date(trading_date)
            if weights and symbol not in weights:
                return "removed_from_index"
        if not self.enable_risk_exit:
            return ""
        return self._profit_exit_reason(symbol, self._price(bar))

    def _status_exit_reason(self, symbol: str, bar: Any) -> str:
        if symbol == self.benchmark_symbol:
            return "benchmark_symbol"
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

    def _exit_risk_positions(self, trading_date: date) -> set[str]:
        exited = set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0 or symbol == self.benchmark_symbol:
                continue
            bar = self._get_last_bar(symbol)
            if not bar:
                continue
            reason = self._position_exit_reason(symbol, bar, trading_date)
            if not reason:
                continue
            sell_quantity = self._whole_share_quantity(quantity)
            if sell_quantity <= 0:
                self._count("exit_triggers", "dust_position")
                continue
            self._count("exit_triggers", reason)
            price = self._get_last_price(symbol)
            self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _sell_whole_position(self, symbol: str) -> None:
        quantity = self._whole_share_quantity(self._positions.get(symbol, 0) or 0)
        if quantity <= 0:
            self._count("exit_triggers", "dust_position")
            return
        price = self._get_last_price(symbol)
        self.sell(symbol, quantity, "MARKET", price if price > 0 else None)

    def _profit_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)
        if self.stop_loss_pct > 0 and price <= entry_price * (1.0 - self.stop_loss_pct):
            return "stop_loss"
        peak = self._peak_prices.get(symbol, price)
        if (
            self.take_profit_pct > 0
            and self.trailing_stop_pct > 0
            and peak >= entry_price * (1.0 + self.take_profit_pct)
            and price <= peak * (1.0 - self.trailing_stop_pct)
        ):
            return "trailing_take_profit"
        return ""

    def on_fill(self, context: Any, fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if not symbol or symbol == self.benchmark_symbol:
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
            elif fill_price > 0:
                self._entry_prices[symbol] = fill_price
            if fill_price > 0:
                self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

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
                number = self._finite_or_zero(value)
                if number > 0:
                    return number
        return self._entry_prices.get(symbol, 0.0)

    def _weights_for_date(self, trading_date: date) -> Tuple[Optional[date], Dict[str, float]]:
        if not self._index_weight_dates:
            return None, {}
        position = bisect.bisect_right(self._index_weight_dates, trading_date) - 1
        if position < 0:
            return None, {}
        weight_date = self._index_weight_dates[position]
        return weight_date, dict(self._index_weights[weight_date])

    @staticmethod
    def _prepare_index_weights(rows: Iterable[Dict[str, Any]]) -> Tuple[List[date], Dict[date, Dict[str, float]]]:
        prepared: Dict[date, Dict[str, float]] = {}
        raw_rows = list(rows)
        max_weight = 0.0
        for row in raw_rows:
            try:
                max_weight = max(max_weight, float(row.get("weight") or 0.0))
            except (TypeError, ValueError):
                continue
        divisor = 100.0 if max_weight > 1.0 else 1.0
        for row in raw_rows:
            symbol = str(row.get("symbol") or row.get("con_code") or "").split(".")[0]
            if not symbol:
                continue
            trade_date = AShareCsi300StrictIndexEnhancedStrategy._parse_date(row.get("trade_date"))
            if trade_date is None:
                continue
            try:
                weight = float(row.get("weight") or 0.0) / divisor
            except (TypeError, ValueError):
                continue
            if weight <= 0 or not math.isfinite(weight):
                continue
            prepared.setdefault(trade_date, {})[symbol] = weight
        dates = sorted(prepared)
        return dates, prepared

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        text = str(value or "")
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

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
        end_price = self._adj_price(bars[-skip - 1])
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

    def _average_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-self.liquidity_lookback :]
        values = [self._bar_turnover(bar) for bar in bars]
        values = [value for value in values if value > 0 and math.isfinite(value)]
        return sum(values) / float(len(values)) if values else 0.0

    def _bar_turnover(self, bar: Any) -> float:
        turnover = self._positive_float(self._value(bar, "turnover"))
        if turnover > 0:
            return turnover
        return self._price(bar) * self._positive_float(self._value(bar, "volume"))

    def _adj_price(self, bar: Any) -> float:
        try:
            price = float(self._adj(bar, "close"))
        except (TypeError, ValueError):
            return 0.0
        return price if math.isfinite(price) and price > 0 else 0.0

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _whole_share_quantity(self, quantity: float) -> int:
        return int(float(quantity) // 1)

    def _estimated_active_share(self, targets: Dict[str, float], index_weights: Dict[str, float]) -> float:
        symbols = set(targets) | set(index_weights)
        return 0.5 * sum(abs(float(targets.get(symbol, 0.0)) - self.target_exposure * float(index_weights.get(symbol, 0.0))) for symbol in symbols)

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        return {
            **self._guard_diagnostics,
            "entry_rejections": dict(self._guard_diagnostics.get("entry_rejections") or {}),
            "exit_triggers": dict(self._guard_diagnostics.get("exit_triggers") or {}),
            "field_missing": dict(self._guard_diagnostics.get("field_missing") or {}),
        }

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "benchmark_symbol": self.benchmark_symbol,
            "holding_days": self.holding_days,
            "max_positions": self.max_positions,
            "target_exposure": self.target_exposure,
            "active_tilt": self.active_tilt,
            "min_weight_multiplier": self.min_weight_multiplier,
            "max_weight_multiplier": self.max_weight_multiplier,
            "max_single_weight": self.max_single_weight,
            "min_price": self.min_price,
            "min_turnover": self.min_turnover,
            "max_volatility": self.max_volatility,
            "min_drawdown": self.min_drawdown,
            "min_recent_momentum": self.min_recent_momentum,
            "min_long_momentum": self.min_long_momentum,
            "enable_risk_exit": self.enable_risk_exit,
            "risk_exit": {
                "enabled": self.enable_risk_exit,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
            },
            "index_weight_dates": len(getattr(self, "_index_weight_dates", []) or []),
            "required_fields": self.required_fields,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {
            "last_scores": dict(self._last_scores),
            "last_target_weights": dict(self._last_target_weights),
            "guard_diagnostics": self.get_guard_diagnostics(),
        }

    def _on_stop_cleanup(self) -> None:
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._last_scores.clear()
        self._last_target_weights.clear()

    def _count(self, bucket: str, key: str) -> None:
        values = self._guard_diagnostics.setdefault(bucket, {})
        values[key] = int(values.get(key, 0)) + 1

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
    def _finite_or_zero(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

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

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            number = AShareCsi300StrictIndexEnhancedStrategy._finite_or_zero(value)
            if number > 0:
                return number
        return 0.0
