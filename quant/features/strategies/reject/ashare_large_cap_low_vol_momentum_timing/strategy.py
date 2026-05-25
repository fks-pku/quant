"""A-share large-cap low-volatility momentum strategy with market timing."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_large_cap_low_vol_momentum_timing"


@strategy(STRATEGY_NAME)
class AShareLargeCapLowVolMomentumTimingStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timing_symbol: str = "000300",
        holding_days: int = 20,
        max_positions: int = 30,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.80,
        cap_percentile_high: float = 1.00,
        min_price: float = 5.0,
        min_turnover: float = 80_000.0,
        use_market_timing: bool = True,
        timing_ma: int = 200,
        timing_exit_buffer: float = 0.98,
        timing_momentum_lookback: int = 60,
        min_timing_momentum: float = -0.08,
        symbol_trend_ma: int = 0,
        symbol_entry_buffer: float = 1.0,
        symbol_exit_buffer: float = 0.98,
        min_long_momentum: float = -0.10,
        min_recent_momentum: float = -0.08,
        max_volatility: float = 0.0,
        min_drawdown: float = -1.0,
        max_pb: float = 0.0,
        max_ps_ttm: float = 0.0,
        stop_loss_pct: float = 0.10,
        take_profit_pct: float = 0.25,
        trailing_stop_pct: float = 0.10,
        score_profile: str = "momentum_lowvol",
        lot_size: int = 100,
        target_weight_slots: Optional[int] = None,
    ):
        self.timing_symbol = str(timing_symbol)
        self.use_market_timing = bool(use_market_timing)
        base_symbols = [str(symbol) for symbol in (symbols or [])]
        if self.timing_symbol not in base_symbols:
            base_symbols.append(self.timing_symbol)
        self.trade_symbols = [symbol for symbol in base_symbols if symbol != self.timing_symbol]
        self.timing_ma = max(20, int(timing_ma))
        self.timing_exit_buffer = min(max(float(timing_exit_buffer), 0.80), 1.00)
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
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.score_profile = str(score_profile)
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
            STRATEGY_NAME,
            symbols=base_symbols,
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
                self.timing_ma,
                self.volatility_lookback,
                self.symbol_trend_ma,
            )
            + 5,
            target_weight_slots=target_weight_slots,
        )

    @property
    def formula_key(self) -> str:
        return STRATEGY_NAME

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "pb", "ps_ttm", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        if self.score_profile == "quality_defensive":
            return [
                ("volatility", 0.25, False),
                ("drawdown", 0.20, True),
                ("momentum", 0.20, True),
                ("recent_momentum", 0.10, True),
                ("pb", 0.15, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "pure_momentum":
            return [
                ("momentum", 0.55, True),
                ("recent_momentum", 0.20, True),
                ("volatility", 0.10, False),
                ("drawdown", 0.10, True),
                ("circ_mv", 0.05, True),
            ]
        if self.score_profile == "quality_growth":
            return [
                ("roe", 0.25, True),
                ("netprofit_yoy", 0.15, True),
                ("momentum", 0.25, True),
                ("volatility", 0.15, False),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "reversal":
            return [
                ("reversal", 0.45, True),
                ("volatility", 0.20, False),
                ("drawdown", 0.15, True),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "volume_reversal":
            return [
                ("volume_reversal", 0.45, True),
                ("volatility", 0.20, False),
                ("drawdown", 0.15, True),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "trend_elastic":
            return [
                ("trend_strength", 0.35, True),
                ("recent_momentum", 0.25, True),
                ("momentum", 0.20, True),
                ("volatility", 0.15, True),
                ("drawdown", 0.05, True),
            ]
        if self.score_profile == "rule_based_quality_trend":
            return [
                ("trend_strength", 0.20, True),
                ("momentum", 0.20, True),
                ("recent_momentum", 0.20, True),
                ("volatility", 0.15, False),
                ("drawdown", 0.10, True),
                ("pb", 0.10, False),
                ("ps_ttm", 0.05, False),
            ]
        return [
            ("momentum", 0.35, True),
            ("recent_momentum", 0.15, True),
            ("volatility", 0.20, False),
            ("drawdown", 0.15, True),
            ("pb", 0.10, False),
            ("circ_mv", 0.05, True),
        ]

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
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

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

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pb = self._positive_float(self._value(bar, "pb"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        if pb <= 0:
            return {"symbol": symbol, "missing_field": "pb"}
        if ps_ttm <= 0:
            return {"symbol": symbol, "missing_field": "ps_ttm"}
        if self.max_pb > 0 and pb > self.max_pb:
            return {"symbol": symbol, "rejection_reason": "high_pb"}
        if self.max_ps_ttm > 0 and ps_ttm > self.max_ps_ttm:
            return {"symbol": symbol, "rejection_reason": "high_ps_ttm"}
        recent_momentum = self._return(symbol, self.recent_momentum_lookback)
        if recent_momentum is None:
            return {"symbol": symbol, "missing_field": "recent_momentum"}
        if recent_momentum < self.min_recent_momentum:
            return {"symbol": symbol, "rejection_reason": "weak_recent_momentum"}
        volatility = self._volatility(symbol, self.volatility_lookback)
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        if self.max_volatility > 0 and volatility > self.max_volatility:
            return {"symbol": symbol, "rejection_reason": "high_volatility"}
        drawdown = self._max_drawdown(symbol, self.drawdown_lookback)
        if drawdown is None:
            return {"symbol": symbol, "missing_field": "drawdown"}
        if drawdown < self.min_drawdown:
            return {"symbol": symbol, "rejection_reason": "deep_recent_drawdown"}
        trend_fields = self._symbol_trend_snapshot(symbol)
        rejection = str(trend_fields.pop("rejection_reason", "") or "")
        if rejection:
            return {"symbol": symbol, "rejection_reason": rejection}
        missing = str(trend_fields.pop("missing_field", "") or "")
        if missing:
            return {"symbol": symbol, "missing_field": missing}
        if self.score_profile == "quality_growth":
            return self._quality_growth_snapshot(
                symbol,
                bar,
                {**base, **trend_fields},
                pb,
                ps_ttm,
                recent_momentum,
                volatility,
                drawdown,
            )
        if self.score_profile in {"reversal", "volume_reversal"}:
            return self._reversal_snapshot(
                symbol,
                {**base, **trend_fields},
                pb,
                ps_ttm,
                recent_momentum,
                volatility,
                drawdown,
            )
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if momentum < self.min_long_momentum:
            return {"symbol": symbol, "rejection_reason": "negative_momentum_guard"}
        return {
            **base,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "momentum": momentum,
            "recent_momentum": recent_momentum,
            "volatility": volatility,
            "drawdown": drawdown,
            **trend_fields,
            "missing_field": "",
        }

    def _quality_growth_snapshot(
        self,
        symbol: str,
        bar: Any,
        base: Dict[str, float | str],
        pb: float,
        ps_ttm: float,
        recent_momentum: float,
        volatility: float,
        drawdown: float,
    ) -> Dict[str, float | str]:
        roe = self._positive_float(self._value(bar, "roe"))
        if roe <= 0:
            roe = self._positive_float(self._value(bar, "q_roe"))
        if roe <= 0:
            return {"symbol": symbol, "missing_field": "roe"}
        netprofit_yoy = self._finite_float(self._value(bar, "netprofit_yoy"))
        if netprofit_yoy is None:
            netprofit_yoy = self._finite_float(self._value(bar, "q_netprofit_yoy"))
        if netprofit_yoy is None:
            return {"symbol": symbol, "missing_field": "netprofit_yoy"}
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if momentum < self.min_long_momentum or recent_momentum < self.min_recent_momentum or netprofit_yoy <= -30.0:
            return {"symbol": symbol, "rejection_reason": "quality_growth_guard"}
        return {
            **base,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "roe": roe,
            "netprofit_yoy": netprofit_yoy,
            "momentum": momentum,
            "recent_momentum": recent_momentum,
            "volatility": volatility,
            "drawdown": drawdown,
            "missing_field": "",
        }

    def _reversal_snapshot(
        self,
        symbol: str,
        base: Dict[str, float | str],
        pb: float,
        ps_ttm: float,
        recent_momentum: float,
        volatility: float,
        drawdown: float,
    ) -> Dict[str, float | str]:
        five_day_return = self._return(symbol, 5)
        if five_day_return is None:
            return {"symbol": symbol, "missing_field": "five_day_return"}
        if five_day_return > -0.015:
            return {"symbol": symbol, "rejection_reason": "no_short_term_pullback"}
        if five_day_return < -0.12 or recent_momentum < -0.20:
            return {"symbol": symbol, "rejection_reason": "falling_knife_guard"}
        volume_ratio = self._volume_ratio(symbol, 20)
        if volume_ratio is None:
            return {"symbol": symbol, "missing_field": "volume_ratio"}
        reversal = abs(five_day_return) / max(volatility, 1e-9)
        return {
            **base,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "reversal": reversal,
            "volume_reversal": reversal * math.log1p(max(volume_ratio, 0.0)),
            "recent_momentum": recent_momentum,
            "volatility": volatility,
            "drawdown": drawdown,
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
            return False
        last = closes[-1]
        ma = sum(closes[-self.timing_ma :]) / float(self.timing_ma)
        momentum = last / closes[-self.timing_momentum_lookback - 1] - 1.0
        if last >= ma and momentum >= 0.0:
            self._risk_on = True
        elif last < ma * self.timing_exit_buffer or momentum < self.min_timing_momentum:
            self._risk_on = False
        self._last_timing_state = {
            "risk_on": self._risk_on,
            "close": last,
            "ma": ma,
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
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def _symbol_trend_snapshot(self, symbol: str) -> Dict[str, float | str]:
        if self.symbol_trend_ma <= 0:
            return {}
        state = self._symbol_trend_state(symbol)
        if not state:
            return {"symbol": symbol, "missing_field": "symbol_trend"}
        if not bool(state["trend_on"]):
            return {"symbol": symbol, "rejection_reason": "symbol_trend_off"}
        return {
            "trend_strength": float(state["trend_strength"]),
            "symbol_trend_ma": float(state["moving_average"]),
            "missing_field": "",
        }

    def _symbol_trend_exit_reason(self, symbol: str) -> str:
        if self.symbol_trend_ma <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        state = self._symbol_trend_state(symbol)
        if not state:
            return ""
        adjusted_close = float(state["adjusted_close"])
        moving_average = float(state["moving_average"])
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
        trend_strength = adjusted_close / moving_average - 1.0
        return {
            "trend_on": adjusted_close >= moving_average * self.symbol_entry_buffer,
            "adjusted_close": adjusted_close,
            "moving_average": moving_average,
            "trend_strength": trend_strength,
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

    def _volume_ratio(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) <= lookback:
            return None
        volumes = []
        for bar in bars[-lookback - 1 : -1]:
            try:
                volume = float(self._value(bar, "volume", 0.0) or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            if volume > 0 and math.isfinite(volume):
                volumes.append(volume)
        if len(volumes) < lookback:
            return None
        current = self._positive_float(self._value(bars[-1], "volume", 0.0))
        if current <= 0:
            return None
        return current / max(sum(volumes) / float(len(volumes)), 1e-9)

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
    def _finite_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

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
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "score_profile": self.score_profile,
            }
        )
        return diagnostics

    def _get_parameters(self) -> Dict[str, Any]:
        params = super()._get_parameters()
        params.update(
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
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "score_profile": self.score_profile,
            }
        )
        return params
