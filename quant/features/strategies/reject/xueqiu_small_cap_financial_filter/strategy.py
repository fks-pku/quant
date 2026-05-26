"""Xueqiu small-cap financial-filter rotation."""

from datetime import date
from typing import Any, List, Optional

from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.strategies.registry import strategy


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
    ):
        extra_symbols = list(symbols or [])
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

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if self.risk_index_symbol and symbol == self.risk_index_symbol:
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
        return self._status_or_delisting_risk(symbol, bar)

    def _status_or_delisting_risk(self, symbol: str, bar: Any) -> bool:
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
        return self._average_turnover(symbol) < self.min_adv_value

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
            }
        )
        state["parameters"] = params
        return state
