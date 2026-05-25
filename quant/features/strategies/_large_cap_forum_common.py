"""Shared large-cap A-share forum strategy building blocks."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec


class AShareLargeCapForumCompositeStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        name: str,
        symbols: Optional[List[str]] = None,
        timing_symbol: str = "000300",
        holding_days: int = 20,
        max_positions: int = 20,
        max_position_pct: float = 0.95,
        cap_percentile_low: float = 0.80,
        cap_percentile_high: float = 1.00,
        min_price: float = 5.0,
        min_turnover: float = 80_000.0,
        use_market_timing: bool = True,
        timing_ma: int = 120,
        timing_exit_buffer: float = 0.96,
        timing_momentum_lookback: int = 60,
        min_timing_momentum: float = -0.08,
        symbol_trend_ma: int = 0,
        symbol_entry_buffer: float = 1.00,
        symbol_exit_buffer: float = 0.97,
        min_long_momentum: float = -0.15,
        min_recent_momentum: float = -0.10,
        max_volatility: float = 0.0,
        min_drawdown: float = -1.0,
        max_pb: float = 0.0,
        max_ps_ttm: float = 0.0,
        min_roe: float = 0.0,
        max_debt_to_assets: float = 0.0,
        min_dividend_yield: float = 0.0,
        score_profile: str = "low_vol_value_momentum",
        target_weight_slots: Optional[int] = None,
        max_replacements_per_rebalance: Optional[int] = None,
        stop_loss_pct: float = 0.0,
        take_profit_pct: float = 0.0,
        trailing_stop_pct: float = 0.0,
        lot_size: int = 100,
    ):
        self.timing_symbol = str(timing_symbol)
        base_symbols = [str(symbol) for symbol in (symbols or [])]
        if self.timing_symbol not in base_symbols:
            base_symbols.append(self.timing_symbol)
        self.trade_symbols = [symbol for symbol in base_symbols if symbol != self.timing_symbol]
        self.use_market_timing = bool(use_market_timing)
        self.timing_ma = max(20, int(timing_ma))
        self.timing_exit_buffer = min(max(float(timing_exit_buffer), 0.75), 1.00)
        self.timing_momentum_lookback = max(5, int(timing_momentum_lookback))
        self.min_timing_momentum = float(min_timing_momentum)
        self.symbol_trend_ma = max(0, int(symbol_trend_ma))
        self.symbol_entry_buffer = min(max(float(symbol_entry_buffer), 0.80), 1.20)
        self.symbol_exit_buffer = min(max(float(symbol_exit_buffer), 0.80), 1.05)
        self.min_long_momentum = float(min_long_momentum)
        self.min_recent_momentum = float(min_recent_momentum)
        self.max_volatility = max(0.0, float(max_volatility))
        self.min_drawdown = min(0.0, float(min_drawdown))
        self.max_pb = max(0.0, float(max_pb))
        self.max_ps_ttm = max(0.0, float(max_ps_ttm))
        self.min_roe = float(min_roe)
        self.max_debt_to_assets = max(0.0, float(max_debt_to_assets))
        self.min_dividend_yield = max(0.0, float(min_dividend_yield))
        self.score_profile = str(score_profile)
        self.max_replacements_per_rebalance = (
            max(1, int(max_replacements_per_rebalance))
            if max_replacements_per_rebalance is not None
            else None
        )
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.momentum_lookback = 252
        self.momentum_skip = 21
        self.recent_momentum_lookback = 60
        self.volatility_lookback = 120
        self.drawdown_lookback = 120
        self._risk_on = False
        self._last_timing_state: Dict[str, Any] = {}
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        super().__init__(
            name,
            symbols=list(dict.fromkeys(base_symbols)),
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=lot_size,
            max_lookback=max(
                self.momentum_lookback,
                self.recent_momentum_lookback,
                self.volatility_lookback,
                self.drawdown_lookback,
                self.timing_ma,
                self.symbol_trend_ma,
            )
            + 5,
            target_weight_slots=target_weight_slots,
        )

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
            "debt_to_assets",
            "grossprofit_margin",
            "adj_close",
        ]

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        profiles = {
            "csi300_low_turnover_multifactor": [
                ("momentum", 0.22, True),
                ("volatility", 0.18, False),
                ("pb", 0.15, False),
                ("pe_ttm", 0.10, False),
                ("roe", 0.15, True),
                ("dv_ttm", 0.10, True),
                ("turnover_rate", 0.10, False),
            ],
            "alpha158_factor_composite": [
                ("momentum", 0.30, True),
                ("recent_momentum", 0.15, True),
                ("trend_strength", 0.15, True),
                ("volatility", 0.15, False),
                ("liquidity", 0.10, True),
                ("pb", 0.10, False),
                ("drawdown", 0.05, True),
            ],
            "white_horse_temperature": [
                ("roe", 0.25, True),
                ("grossprofit_margin", 0.15, True),
                ("debt_to_assets", 0.15, False),
                ("volatility", 0.15, False),
                ("momentum", 0.10, True),
                ("dv_ttm", 0.10, True),
                ("pb", 0.10, False),
            ],
            "dividend_low_vol_smart_beta": [
                ("dv_ttm", 0.35, True),
                ("volatility", 0.25, False),
                ("pb", 0.15, False),
                ("drawdown", 0.10, True),
                ("circ_mv", 0.10, True),
                ("recent_momentum", 0.05, True),
            ],
        }
        return profiles.get(
            self.score_profile,
            [
                ("volatility", 0.30, False),
                ("momentum", 0.25, True),
                ("pb", 0.20, False),
                ("dv_ttm", 0.10, True),
                ("drawdown", 0.10, True),
                ("circ_mv", 0.05, True),
            ],
        )

    def on_after_trading(self, context: Any, trading_date) -> None:
        self._risk_exited_today = self._exit_risk_positions()
        risk_on = self._update_timing_state()
        if not risk_on:
            self._liquidate_trade_positions(self._risk_exited_today)
            self._last_rebalance_date = None
            self._days_since_rebalance = 0
            return
        if not self._check_rebalance_gate(trading_date):
            return
        if self._execute_rebalance(context, trading_date):
            self._last_rebalance_date = trading_date
            self._days_since_rebalance = 0

    def _execute_rebalance(self, context: Any, trading_date) -> bool:
        snapshots = self._eligible_snapshots()
        self._guard_diagnostics["rebalance_count"] += 1
        self._guard_diagnostics["last_candidate_count"] = len(snapshots)
        if not snapshots:
            self._guard_diagnostics["last_selected_count"] = 0
            return False
        scores = self._score_snapshots(snapshots)
        desired = [symbol for symbol, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.max_positions]]
        selected = self._apply_replacement_limit(desired, scores)
        selected_set = set(selected)
        self._last_scores = scores
        self._guard_diagnostics["last_selected_count"] = len(selected)

        for symbol, quantity in list(self._positions.items()):
            if (
                quantity > 0
                and symbol != self.timing_symbol
                and symbol not in self._risk_exited_today
                and symbol not in selected_set
            ):
                price = self._get_last_price(symbol)
                sell_quantity = self._whole_share_quantity(quantity)
                if sell_quantity > 0:
                    self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)

        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0 or not selected:
            return bool(selected)
        target_slots = self.target_weight_slots or len(selected)
        target_value = nav * self.max_position_pct / float(max(1, target_slots))
        for symbol in selected:
            if symbol in self._risk_exited_today:
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

    def _eligible_snapshots(self) -> List[Dict[str, float | str]]:
        raw_candidates = []
        excluded = set(self._risk_exited_today)
        for symbol in self.trade_symbols:
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

        snapshots = []
        for base in self._apply_cap_band(raw_candidates):
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
        return snapshots

    def _apply_replacement_limit(self, desired: List[str], scores: Dict[str, float]) -> List[str]:
        if not self.max_replacements_per_rebalance:
            return desired
        held = [
            symbol
            for symbol, quantity in self._positions.items()
            if quantity > 0 and symbol != self.timing_symbol and symbol not in self._risk_exited_today
        ]
        if not held:
            return desired[: self.max_positions]
        desired_set = set(desired)
        keep = [symbol for symbol in held if symbol in desired_set]
        sell_candidates = [symbol for symbol in held if symbol not in desired_set]
        buy_candidates = [symbol for symbol in desired if symbol not in held]
        sell_candidates = sorted(sell_candidates, key=lambda symbol: (scores.get(symbol, -1.0), symbol))
        replace_count = min(self.max_replacements_per_rebalance, len(sell_candidates), len(buy_candidates))
        retained = [symbol for symbol in held if symbol not in set(sell_candidates[:replace_count])]
        selected = retained + buy_candidates[:replace_count]
        for symbol in buy_candidates[replace_count:]:
            if len(selected) >= self.max_positions:
                break
            selected.append(symbol)
        ranked_selected = sorted(dict.fromkeys(selected), key=lambda symbol: (-scores.get(symbol, 0.0), symbol))
        return ranked_selected[: self.max_positions]

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        if symbol == self.timing_symbol:
            return "timing_symbol"
        return super()._candidate_rejection(symbol, bar)

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if symbol == self.timing_symbol:
            return "timing_symbol"
        reason = super()._position_exit_reason(symbol, bar)
        if reason:
            return reason
        trend_reason = self._symbol_trend_exit_reason(symbol)
        if trend_reason:
            return trend_reason
        profit_reason = self._profit_exit_reason(symbol, self._price(bar))
        if profit_reason:
            return profit_reason
        return ""

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
            sell_quantity = self._whole_share_quantity(quantity)
            if sell_quantity <= 0:
                self._count("exit_triggers", "dust_position")
                continue
            self._count("exit_triggers", reason)
            price = self._get_last_price(symbol)
            self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pb = self._positive_float(self._value(bar, "pb"))
        pe_ttm = self._positive_float(self._value(bar, "pe_ttm"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        dv_ttm = self._nonnegative_float(self._value(bar, "dv_ttm"))
        roe = self._finite_or_zero(self._value(bar, "roe"))
        grossprofit_margin = self._finite_or_zero(self._value(bar, "grossprofit_margin"))
        debt_to_assets = self._finite_or_zero(self._value(bar, "debt_to_assets"))
        if pe_ttm <= 0:
            pe_ttm = self._positive_float(self._value(bar, "pe"))
        if ps_ttm <= 0:
            ps_ttm = self._positive_float(self._value(bar, "ps"))
        required = {"pb": pb, "pe_ttm": pe_ttm, "ps_ttm": ps_ttm}
        for field, value in required.items():
            if value <= 0:
                return {"symbol": symbol, "missing_field": field}
        if self.max_pb > 0 and pb > self.max_pb:
            return {"symbol": symbol, "rejection_reason": "high_pb"}
        if self.max_ps_ttm > 0 and ps_ttm > self.max_ps_ttm:
            return {"symbol": symbol, "rejection_reason": "high_ps_ttm"}
        if self.min_roe > 0 and roe < self.min_roe:
            return {"symbol": symbol, "rejection_reason": "low_roe"}
        if self.max_debt_to_assets > 0 and debt_to_assets > self.max_debt_to_assets:
            return {"symbol": symbol, "rejection_reason": "high_debt_to_assets"}
        if self.min_dividend_yield > 0 and dv_ttm < self.min_dividend_yield:
            return {"symbol": symbol, "rejection_reason": "low_dividend_yield"}

        recent_momentum = self._return(symbol, self.recent_momentum_lookback)
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        volatility = self._volatility(symbol, self.volatility_lookback)
        drawdown = self._max_drawdown(symbol, self.drawdown_lookback)
        if recent_momentum is None:
            return {"symbol": symbol, "missing_field": "recent_momentum"}
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        if drawdown is None:
            return {"symbol": symbol, "missing_field": "drawdown"}
        if recent_momentum < self.min_recent_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_recent_momentum"}
        if momentum < self.min_long_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_long_momentum"}
        if self.max_volatility > 0 and volatility > self.max_volatility:
            return {"symbol": symbol, "rejection_reason": "high_volatility"}
        if drawdown < self.min_drawdown:
            return {"symbol": symbol, "rejection_reason": "deep_recent_drawdown"}

        trend_fields = self._symbol_trend_snapshot(symbol)
        rejection = str(trend_fields.pop("rejection_reason", "") or "")
        if rejection:
            return {"symbol": symbol, "rejection_reason": rejection}
        missing = str(trend_fields.pop("missing_field", "") or "")
        if missing:
            return {"symbol": symbol, "missing_field": missing}

        return {
            **base,
            "pb": pb,
            "pe_ttm": pe_ttm,
            "ps_ttm": ps_ttm,
            "dv_ttm": dv_ttm,
            "roe": roe,
            "grossprofit_margin": grossprofit_margin,
            "debt_to_assets": debt_to_assets,
            "momentum": momentum,
            "recent_momentum": recent_momentum,
            "volatility": volatility,
            "drawdown": drawdown,
            "liquidity": self._average_turnover(symbol),
            **trend_fields,
            "missing_field": "",
        }

    def _update_timing_state(self) -> bool:
        if not self.use_market_timing:
            self._risk_on = True
            self._last_timing_state = {"risk_on": True, "market_timing": "disabled"}
            return True
        closes = [price for price in self._get_closes(self.timing_symbol) if price > 0 and math.isfinite(price)]
        needed = max(self.timing_ma, self.timing_momentum_lookback) + 1
        if len(closes) < needed:
            self._risk_on = False
            self._last_timing_state = {"risk_on": False, "reason": "warmup"}
            return False
        last = closes[-1]
        moving_average = sum(closes[-self.timing_ma :]) / float(self.timing_ma)
        momentum = last / closes[-self.timing_momentum_lookback - 1] - 1.0
        if last >= moving_average and momentum >= 0.0:
            self._risk_on = True
        elif last < moving_average * self.timing_exit_buffer or momentum < self.min_timing_momentum:
            self._risk_on = False
        self._last_timing_state = {
            "risk_on": self._risk_on,
            "close": last,
            "ma": moving_average,
            "momentum": momentum,
            "timing_ma": self.timing_ma,
            "timing_momentum_lookback": self.timing_momentum_lookback,
        }
        return self._risk_on

    def _liquidate_trade_positions(self, exclude: Optional[set[str]] = None) -> None:
        excluded = exclude or set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0 or symbol == self.timing_symbol or symbol in excluded:
                continue
            price = self._get_last_price(symbol)
            sell_quantity = self._whole_share_quantity(quantity)
            if sell_quantity > 0:
                self.sell(symbol, sell_quantity, "MARKET", price if price > 0 else None)

    def _symbol_trend_snapshot(self, symbol: str) -> Dict[str, float | str]:
        if self.symbol_trend_ma <= 0:
            return {"trend_strength": 0.0, "missing_field": ""}
        state = self._symbol_trend_state(symbol)
        if not state:
            return {"symbol": symbol, "missing_field": "symbol_trend"}
        if not bool(state["trend_on"]):
            return {"symbol": symbol, "rejection_reason": "symbol_trend_off"}
        return {"trend_strength": float(state["trend_strength"]), "missing_field": ""}

    def _symbol_trend_exit_reason(self, symbol: str) -> str:
        if self.symbol_trend_ma <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        state = self._symbol_trend_state(symbol)
        if not state:
            return ""
        moving_average = float(state["moving_average"])
        adjusted_close = float(state["adjusted_close"])
        if moving_average > 0 and adjusted_close < moving_average * self.symbol_exit_buffer:
            return "symbol_trend_exit"
        return ""

    def _symbol_trend_state(self, symbol: str) -> Optional[Dict[str, float | bool]]:
        closes = [price for price in self._get_closes(symbol) if price > 0 and math.isfinite(price)]
        if self.symbol_trend_ma <= 0 or len(closes) < self.symbol_trend_ma:
            return None
        adjusted_close = closes[-1]
        moving_average = sum(closes[-self.symbol_trend_ma :]) / float(self.symbol_trend_ma)
        if moving_average <= 0:
            return None
        return {
            "trend_on": adjusted_close >= moving_average * self.symbol_entry_buffer,
            "adjusted_close": adjusted_close,
            "moving_average": moving_average,
            "trend_strength": adjusted_close / moving_average - 1.0,
        }

    def on_fill(self, context: Any, fill: Any) -> None:
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

    @staticmethod
    def _whole_share_quantity(quantity: Any) -> int:
        try:
            number = float(quantity)
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(number) or number <= 0:
            return 0
        return int(number)

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

    @staticmethod
    def _finite_or_zero(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    @staticmethod
    def _nonnegative_float(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number >= 0 else 0.0

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        diagnostics = super().get_guard_diagnostics()
        diagnostics["timing"] = dict(self._last_timing_state)
        diagnostics["parameters"].update(
            {
                "timing_symbol": self.timing_symbol,
                "use_market_timing": self.use_market_timing,
                "timing_ma": self.timing_ma,
                "timing_exit_buffer": self.timing_exit_buffer,
                "timing_momentum_lookback": self.timing_momentum_lookback,
                "min_timing_momentum": self.min_timing_momentum,
                "symbol_trend_ma": self.symbol_trend_ma,
                "symbol_entry_buffer": self.symbol_entry_buffer,
                "symbol_exit_buffer": self.symbol_exit_buffer,
                "min_long_momentum": self.min_long_momentum,
                "min_recent_momentum": self.min_recent_momentum,
                "max_volatility": self.max_volatility,
                "min_drawdown": self.min_drawdown,
                "max_pb": self.max_pb,
                "max_ps_ttm": self.max_ps_ttm,
                "min_roe": self.min_roe,
                "max_debt_to_assets": self.max_debt_to_assets,
                "min_dividend_yield": self.min_dividend_yield,
                "score_profile": self.score_profile,
                "max_replacements_per_rebalance": self.max_replacements_per_rebalance,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
            }
        )
        return diagnostics

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(self.get_guard_diagnostics().get("parameters") or {})
        params["trade_symbols"] = list(self.trade_symbols)
        return params
