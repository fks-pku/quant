"""Run a strict BigQuant-inspired A-share small-cap experiment suite."""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider


START = datetime(2016, 1, 1)
END = datetime(2026, 5, 31)
INITIAL_CASH = 20000.0
STRATEGY_ID = "bigquant_small_cap_suite"
TITLE = "BigQuant-inspired small-cap suite"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
DEFAULT_EXCLUDED_BOARD_PREFIXES = ("300", "301", "688", "689")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
SOURCE_URLS = {
    "pure_size": "https://bigquant.com/square/ai",
    "growth_low_vol": "https://bigquant.com/square/ai/a812e23e-35c6-4ad3-b2e1-ebdc86f41f63",
    "value_quality": "https://bigquant.com/square/ai/5e3075be-8441-7181-28c9-54b4dbb9c6dc",
    "dividend_low_vol": "https://bigquant.com/square/ai/cfb0c8a3-de2e-4f27-8d06-5fd838e08fd0",
    "style_timing": "https://bigquant.com/wiki/doc/IxiE9aD6qu",
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "pure_size_top20_full",
        "profile": "pure_size",
        "description": "Pure small-cap baseline: rank eligible stocks by the smallest PIT market cap.",
        "max_positions": 20,
        "rebalance_interval": 10,
        "target_exposure": 1.0,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
    },
    {
        "name": "concentrated_size_top5_full",
        "profile": "pure_size",
        "description": "Concentrated small-cap baseline similar to two-to-five-stock forum variants.",
        "max_positions": 5,
        "rebalance_interval": 5,
        "target_exposure": 1.0,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
    },
    {
        "name": "growth_low_vol_top20_80pct",
        "profile": "growth_low_vol",
        "description": "Small cap plus disclosed profit/sales growth, ROE, low volatility, and low crowding.",
        "max_positions": 20,
        "rebalance_interval": 20,
        "target_exposure": 0.8,
        "min_price": 5.0,
        "min_adv_value": 50000.0,
    },
    {
        "name": "value_quality_top20_80pct",
        "profile": "value_quality",
        "description": "Small cap plus low PE/PB/PS, positive ROE, and low turnover.",
        "max_positions": 20,
        "rebalance_interval": 20,
        "target_exposure": 0.8,
        "min_price": 5.0,
        "min_adv_value": 50000.0,
    },
    {
        "name": "dividend_low_vol_top20_70pct",
        "profile": "dividend_low_vol",
        "description": "Small cap plus dividend yield, low volatility, low PB, and positive quality.",
        "max_positions": 20,
        "rebalance_interval": 20,
        "target_exposure": 0.7,
        "min_price": 5.0,
        "min_adv_value": 80000.0,
    },
    {
        "name": "style_timing_value_quality_top20_80pct",
        "profile": "style_timing",
        "description": "Value-quality small-cap sleeve with CSI 300 trend and momentum risk-off overlay.",
        "max_positions": 20,
        "rebalance_interval": 20,
        "target_exposure": 0.8,
        "min_price": 5.0,
        "min_adv_value": 50000.0,
        "market_timing_symbol": "000300",
        "market_trend_window": 120,
        "market_momentum_lookback": 60,
        "market_momentum_threshold": -0.08,
        "market_risk_off_exposure": 0.0,
    },
]


class BigquantSmallCapProfileStrategy(AShareSmallCapRotationBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        profile: str = "pure_size",
        scenario_name: str = "bigquant_small_cap_profile",
        excluded_board_prefixes: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.18,
        min_stop_loss_pct: float = 0.12,
        max_stop_loss_pct: float = 0.28,
        stop_volatility_multiplier: float = 3.5,
        take_profit_pct: float = 0.35,
        trailing_stop_pct: float = 0.14,
        trailing_volatility_multiplier: float = 2.8,
        max_trailing_stop_pct: float = 0.28,
        max_holding_days: int = 90,
        min_time_stop_return: float = 0.0,
        **kwargs: Any,
    ):
        self.profile = str(profile)
        self.excluded_board_prefixes = tuple(excluded_board_prefixes or DEFAULT_EXCLUDED_BOARD_PREFIXES)
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = self._bool_value(risk_exit_config.get("enabled"), bool(enable_risk_exit))
        self.enable_risk_exit = bool(enable_risk_exit)
        self.stop_loss_pct = float(risk_exit_config.get("stop_loss_pct", stop_loss_pct))
        self.min_stop_loss_pct = float(risk_exit_config.get("min_stop_loss_pct", min_stop_loss_pct))
        self.max_stop_loss_pct = float(risk_exit_config.get("max_stop_loss_pct", max_stop_loss_pct))
        self.stop_volatility_multiplier = float(
            risk_exit_config.get("stop_volatility_multiplier", stop_volatility_multiplier)
        )
        self.take_profit_pct = float(risk_exit_config.get("take_profit_pct", take_profit_pct))
        self.trailing_stop_pct = float(risk_exit_config.get("trailing_stop_pct", trailing_stop_pct))
        self.trailing_volatility_multiplier = float(
            risk_exit_config.get("trailing_volatility_multiplier", trailing_volatility_multiplier)
        )
        self.max_trailing_stop_pct = float(risk_exit_config.get("max_trailing_stop_pct", max_trailing_stop_pct))
        self.max_holding_days = int(risk_exit_config.get("max_holding_days", max_holding_days))
        self.min_time_stop_return = float(risk_exit_config.get("min_time_stop_return", min_time_stop_return))
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._entry_bar_counts: Dict[str, int] = {}
        super().__init__(str(scenario_name), symbols=symbols, **kwargs)

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if self._permission_excluded(symbol):
            self._count("entry_rejections", "excluded_permission_board")
            return True
        if super()._entry_risk(symbol, bar):
            return True
        profile = self.profile
        if profile == "pure_size":
            return False
        if profile in {"growth_low_vol", "style_timing"}:
            if self._profit_growth(bar) < 8.0:
                self._count("entry_rejections", "weak_profit_growth")
                return True
            if self._sales_growth(bar) < -10.0:
                self._count("entry_rejections", "weak_sales_growth")
                return True
            if self._roe(bar) < 3.0:
                self._count("entry_rejections", "weak_roe")
                return True
        if profile in {"value_quality", "style_timing"}:
            if self._positive_field_value(bar, ("pe_ttm", "pe")) <= 0:
                self._count("entry_rejections", "missing_or_negative_pe")
                return True
            if self._positive_field_value(bar, ("pe_ttm", "pe")) > 100.0:
                self._count("entry_rejections", "high_pe")
                return True
            if self._positive_field_value(bar, ("pb",)) > 8.0:
                self._count("entry_rejections", "high_pb")
                return True
            if self._positive_field_value(bar, ("ps_ttm", "ps")) > 12.0:
                self._count("entry_rejections", "high_ps")
                return True
        if profile == "dividend_low_vol":
            if self._nonnegative_field(bar, "dv_ttm") < 0.8:
                self._count("entry_rejections", "low_dividend_yield")
                return True
            if self._positive_field_value(bar, ("pb",)) > 10.0:
                self._count("entry_rejections", "high_pb")
                return True
            if self._roe(bar) < 0.0:
                self._count("entry_rejections", "negative_roe")
                return True
        volatility = self._annual_volatility(symbol, 60)
        if profile in {"growth_low_vol", "dividend_low_vol"} and volatility is not None and volatility > 0.85:
            self._count("entry_rejections", "high_volatility")
            return True
        return False

    def _candidate_score(self, symbol: str, bar: Any) -> float:
        market_cap = max(self._market_cap(bar), 1.0)
        size_score = -math.log(market_cap)
        if self.profile == "pure_size":
            return size_score
        volatility = self._annual_volatility(symbol, 60)
        volatility_penalty = 0.0 if volatility is None else min(max(volatility, 0.0), 2.0)
        turnover_rate = self._turnover_rate(bar)
        low_turnover = -math.log1p(max(turnover_rate, 0.0))
        pb = self._positive_field_value(bar, ("pb",))
        pe = self._positive_field_value(bar, ("pe_ttm", "pe"))
        ps = self._positive_field_value(bar, ("ps_ttm", "ps"))
        value_score = (
            -0.30 * math.log(max(pb, 0.05))
            -0.25 * math.log(max(pe, 1.0))
            -0.20 * math.log(max(ps, 0.05))
        )
        growth = self._clip(self._profit_growth(bar), -50.0, 120.0) / 100.0
        sales_growth = self._clip(self._sales_growth(bar), -50.0, 120.0) / 100.0
        roe = self._clip(self._roe(bar), -20.0, 50.0) / 50.0
        dividend = min(self._nonnegative_field(bar, "dv_ttm") / 5.0, 1.5)
        momentum = self._return(symbol, 60)
        momentum_score = 0.0 if momentum is None else self._clip(momentum, -0.5, 0.8)
        if self.profile == "growth_low_vol":
            return 0.75 * size_score + 1.00 * growth + 0.35 * sales_growth + 0.50 * roe - 0.80 * volatility_penalty + 0.15 * low_turnover + 0.20 * value_score
        if self.profile == "value_quality":
            return 0.80 * size_score + 0.75 * value_score + 0.35 * roe + 0.20 * momentum_score + 0.20 * low_turnover - 0.25 * volatility_penalty
        if self.profile == "dividend_low_vol":
            return 0.60 * size_score + 0.85 * dividend + 0.45 * value_score + 0.35 * roe - 0.90 * volatility_penalty + 0.15 * low_turnover
        if self.profile == "style_timing":
            return 0.70 * size_score + 0.60 * value_score + 0.40 * growth + 0.30 * roe + 0.25 * momentum_score - 0.35 * volatility_penalty
        return size_score

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

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if self._permission_excluded(symbol):
            return "excluded_permission_board"
        if AShareSmallCapRotationBase._exit_risk(self, symbol, bar):
            return "status_or_quality_exit"
        if not self.enable_risk_exit:
            return ""
        return self._profit_exit_reason(symbol, self._price(bar))

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

    def on_stop(self, context: Any) -> None:
        super().on_stop(context)
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._entry_bar_counts.clear()

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        params = dict(state.get("parameters") or {})
        params.update(
            {
                "profile": self.profile,
                "excluded_board_prefixes": list(self.excluded_board_prefixes),
                "enable_risk_exit": self.enable_risk_exit,
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
                    "max_holding_days": self.max_holding_days,
                    "min_time_stop_return": self.min_time_stop_return,
                },
            }
        )
        state["parameters"] = params
        return state

    def _profit_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        stop_loss = self._effective_stop_loss_pct(symbol)
        if stop_loss > 0 and price <= entry_price * (1.0 - stop_loss):
            return "stop_loss"
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
        value = max(self.stop_loss_pct, self.min_stop_loss_pct)
        volatility = self._daily_volatility(symbol, 20)
        if volatility is not None and self.stop_volatility_multiplier > 0:
            value = max(value, volatility * self.stop_volatility_multiplier)
        return min(value, self.max_stop_loss_pct) if self.max_stop_loss_pct > 0 else value

    def _effective_trailing_stop_pct(self, symbol: str) -> float:
        value = self.trailing_stop_pct
        volatility = self._daily_volatility(symbol, 20)
        if volatility is not None and self.trailing_volatility_multiplier > 0:
            value = max(value, volatility * self.trailing_volatility_multiplier)
        return min(value, self.max_trailing_stop_pct) if self.max_trailing_stop_pct > 0 else value

    def _time_stop_triggered(self, symbol: str, price: float, entry_price: float) -> bool:
        if self.max_holding_days <= 0:
            return False
        entry_count = self._entry_bar_counts.get(symbol)
        if entry_count is None:
            return False
        holding_days = len(self._bars.get(symbol, [])) - entry_count
        return holding_days >= self.max_holding_days and price / entry_price - 1.0 < self.min_time_stop_return

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
            if total_quantity <= 0:
                return
            if fill_price > 0:
                self._entry_prices[symbol] = (
                    self._entry_prices[symbol] * previous_quantity + fill_price * fill_quantity
                ) / total_quantity
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

    def _permission_excluded(self, symbol: str) -> bool:
        return any(str(symbol).startswith(prefix) for prefix in self.excluded_board_prefixes)

    def _profit_growth(self, bar: Any) -> float:
        return self._first_finite(bar, "q_netprofit_yoy", "netprofit_yoy")

    def _sales_growth(self, bar: Any) -> float:
        return self._first_finite(bar, "q_sales_yoy", "or_yoy")

    def _roe(self, bar: Any) -> float:
        return self._first_finite(bar, "q_roe", "roe")

    def _turnover_rate(self, bar: Any) -> float:
        value = self._positive_field_value(bar, ("turnover_rate_f", "turnover_rate"))
        return value if value > 0 else 0.0

    def _nonnegative_field(self, bar: Any, field: str) -> float:
        value = self._float_value(self._value(bar, field, None), 0.0)
        return value if value > 0 else 0.0

    def _first_finite(self, bar: Any, *fields: str) -> float:
        for field in fields:
            value = self._float_value(self._value(bar, field, None), float("nan"))
            if math.isfinite(value):
                return value
        return 0.0

    def _annual_volatility(self, symbol: str, lookback: int) -> Optional[float]:
        daily = self._daily_volatility(symbol, lookback)
        return None if daily is None else daily * math.sqrt(252.0)

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
        return volatility if volatility > 0 and math.isfinite(volatility) else None

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return min(max(float(value), float(low)), float(high))

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


def main() -> None:
    args = _parse_args()
    start = _parse_date(args.start, START)
    end = _parse_date(args.end, END)
    scenarios = SCENARIOS[: args.max_scenarios] if args.max_scenarios else list(SCENARIOS)
    report_dir = REPORT_ROOT / STRATEGY_ID
    (report_dir / "runs").mkdir(parents=True, exist_ok=True)
    stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(start, end, scenarios)
    rows = []
    strict_reports = {}
    for index, scenario in enumerate(scenarios, start=1):
        scenario_symbols = list(dict.fromkeys([*stock_symbols, *_scenario_extras(scenario)]))
        print(f"[{index}/{len(scenarios)}] {scenario['name']} symbols={len(scenario_symbols)}", flush=True)
        strict_report = _run_one(
            scenario,
            stock_symbols,
            scenario_symbols,
            lot_sizes,
            benchmark_provider,
            benchmark_meta,
            survivorship_audit,
            start,
            end,
        )
        strict_reports[scenario["name"]] = strict_report
        row = _summary_row(scenario, strict_report, len(stock_symbols))
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)
    best = max(rows, key=_rank_key)
    payload_path, html_path = _write_outputs(report_dir, rows, strict_reports, best, start, end)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": _compact_row(best),
                "payload_path": str(payload_path),
                "html_path": str(html_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START.date().isoformat())
    parser.add_argument("--end", default=END.date().isoformat())
    parser.add_argument("--max-scenarios", type=int, default=0)
    return parser.parse_args()


def _parse_date(value: str, fallback: datetime) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d") if value else fallback


def _load_shared_inputs(
    start: datetime,
    end: datetime,
    scenarios: List[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        rows = db_provider.storage.conn.execute(
            """
            SELECT DISTINCT symbol
            FROM daily_cn_ochl
            WHERE CAST(timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY symbol
            """,
            [start, end],
        ).fetchall()
        stock_symbols = [
            str(row[0])
            for row in rows
            if is_cn_symbol(str(row[0])) and not _permission_excluded(str(row[0]))
        ]
        all_symbols = list(stock_symbols)
        for scenario in scenarios:
            all_symbols.extend(_scenario_extras(scenario))
        all_symbols = list(dict.fromkeys(all_symbols))
        lot_sizes = _load_lot_sizes(db_provider, all_symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _run_one(
    scenario: Dict[str, Any],
    stock_symbols: List[str],
    all_symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    strategy_info = {
        "name": TITLE,
        "description": f"bigquant small_cap {scenario['profile']}",
        "research_meta": {"strategy_spec": _strategy_spec(scenario, len(stock_symbols))},
    }
    execution_cost_model = _strict_execution_cost_model(STRATEGY_ID, strategy_info, True)
    data_provider = _DuckDBDailyDateProvider(
        all_symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    strategy = BigquantSmallCapProfileStrategy(symbols=all_symbols, scenario_name=scenario["name"], **_strategy_kwargs(scenario))
    backtest_config = {
        "slippage_bps": 5,
        "execution_cost_model": execution_cost_model,
        "strict_daily_snapshot": False,
    }
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
    }
    backtester = Backtester(
        bt_config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
        lot_sizes=lot_sizes,
        benchmark_provider=benchmark_provider,
    )
    try:
        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=all_symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity = benchmark_provider.get_benchmark_equity(start, end, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        result,
        start,
        end,
        INITIAL_CASH,
        all_symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _strategy_kwargs(scenario: Dict[str, Any]) -> Dict[str, Any]:
    keys = {
        "profile",
        "max_positions",
        "rebalance_interval",
        "target_exposure",
        "min_price",
        "min_adv_value",
        "market_timing_symbol",
        "market_trend_window",
        "market_momentum_lookback",
        "market_momentum_threshold",
        "market_risk_off_exposure",
    }
    return {key: scenario[key] for key in keys if key in scenario}


def _scenario_extras(scenario: Dict[str, Any]) -> List[str]:
    timing_symbol = str(scenario.get("market_timing_symbol") or "")
    return [timing_symbol] if timing_symbol else []


def _summary_row(scenario: Dict[str, Any], strict_report: Dict[str, Any], stock_universe_size: int) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    capacity = strict_report.get("capacity") or {}
    turnover = strict_report.get("turnover") or {}
    row = {
        "scenario": scenario["name"],
        "profile": scenario["profile"],
        "description": scenario.get("description", ""),
        "source_url": SOURCE_URLS.get(str(scenario.get("profile")), ""),
        "parameters": _strategy_kwargs(scenario),
        "stock_universe_size": stock_universe_size,
        "sharpe": _float(metrics.get("sharpe")),
        "sortino": _float(metrics.get("sortino")),
        "cagr": _float(metrics.get("cagr")),
        "total_return": _float(metrics.get("total_return")),
        "max_drawdown_pct": _float(metrics.get("max_drawdown_pct")),
        "calmar_ratio": _float(metrics.get("calmar_ratio")),
        "win_rate": _float(metrics.get("win_rate")),
        "profit_factor": _float(metrics.get("profit_factor")),
        "total_trades": int(metrics.get("total_trades") or 0),
        "cost_drag_pct": _float(diagnostics.get("cost_drag_pct")),
        "annual_gross_turnover": _float(turnover.get("annual_gross_turnover")),
        "max_adv_participation": _float(capacity.get("max_adv_participation")),
        "p95_adv_participation": _float(capacity.get("p95_adv_participation")),
        "limit_rejected_orders": int(diagnostics.get("limit_rejected_orders") or 0),
        "submission_rejected": int(diagnostics.get("submission_rejected") or 0),
        "discarded_orders": int(diagnostics.get("discarded_orders") or 0),
    }
    row["gate_verdict"] = _gate_verdict(row)
    return row


def _gate_verdict(row: Dict[str, Any]) -> str:
    if int(row.get("total_trades") or 0) <= 50:
        return "fail_low_trades"
    if float(row.get("max_adv_participation") or 0.0) > 0.05:
        return "fail_capacity"
    cagr = float(row.get("cagr") or 0.0)
    max_dd = abs(float(row.get("max_drawdown_pct") or 0.0))
    limit = _max_drawdown_limit_for_cagr(cagr)
    if limit is None:
        return "fail_low_cagr"
    if max_dd > limit:
        return "fail_drawdown"
    return "pass"


def _max_drawdown_limit_for_cagr(cagr: float) -> Optional[float]:
    if cagr < 0.05:
        return None
    if cagr < 0.10:
        return 0.15
    if cagr < 0.15:
        return 0.25
    if cagr < 0.20:
        return 0.30
    return 0.50


def _rank_key(row: Dict[str, Any]) -> Tuple[int, int, float, float, float]:
    return (
        1 if row.get("gate_verdict") == "pass" else 0,
        1 if float(row.get("max_adv_participation") or 0.0) <= 0.05 else 0,
        float(row.get("sharpe") or 0.0),
        float(row.get("max_drawdown_pct") or 0.0),
        float(row.get("cagr") or 0.0),
    )


def _write_outputs(
    report_dir: Path,
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Tuple[Path, Path]:
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "runs").mkdir(parents=True, exist_ok=True)
    payload = {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "run_ts": run_ts,
        "period": f"{start.date()}-{end.date()}",
        "initial_cash": INITIAL_CASH,
        "sources": SOURCE_URLS,
        "best": best,
        "rows": sorted(rows, key=_rank_key, reverse=True),
        "strict_reports": strict_reports,
        "bias_audit": _bias_audit_payload(),
    }
    payload_path = report_dir / "suite_result.json"
    latest_path = report_dir / "last_result.json"
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    payload_path.write_text(payload_text, encoding="utf-8")
    latest_path.write_text(payload_text, encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_suite_result.json").write_text(payload_text, encoding="utf-8")
    html_path = report_dir / "suite_summary.html"
    html_text = _summary_html(payload)
    html_path.write_text(html_text, encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_suite_summary.html").write_text(html_text, encoding="utf-8")
    return payload_path, html_path


def _summary_html(payload: Dict[str, Any]) -> str:
    rows = payload["rows"]
    body = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row['scenario']))}</td>"
        f"<td>{html.escape(str(row['profile']))}</td>"
        f"<td>{row['sharpe']:.2f}</td>"
        f"<td>{row['cagr']:.2%}</td>"
        f"<td>{row['max_drawdown_pct']:.2%}</td>"
        f"<td>{row['calmar_ratio']:.2f}</td>"
        f"<td>{row['total_return']:.2%}</td>"
        f"<td>{row['total_trades']}</td>"
        f"<td>{row['max_adv_participation']:.2%}</td>"
        f"<td>{row['cost_drag_pct']:.2f}%</td>"
        f"<td>{html.escape(str(row['gate_verdict']))}</td>"
        f"<td>{html.escape(json.dumps(row['parameters'], ensure_ascii=False))}</td>"
        "</tr>"
        for row in rows
    )
    best = payload["best"]
    bias_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['bias'])}</td>"
        f"<td>{html.escape(item['status'])}</td>"
        f"<td>{html.escape(item['evidence'])}</td>"
        f"<td>{html.escape(item['residual_risk'])}</td>"
        "</tr>"
        for item in payload["bias_audit"]["checklist"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(payload['title'])}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #172033; background: #f8fafc; }}
h1, h2 {{ margin-bottom: 8px; }}
p {{ color: #526072; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; margin: 14px 0 24px; }}
th, td {{ border: 1px solid #d8dee8; padding: 8px 10px; text-align: right; vertical-align: top; }}
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:last-child, td:last-child {{ text-align: left; }}
th {{ background: #eef2f7; }}
.summary {{ background: #fff; border: 1px solid #d8dee8; padding: 14px 16px; margin: 14px 0 18px; }}
code {{ white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>{html.escape(payload['title'])}</h1>
<p>Strict Backtester experiment. Period {html.escape(payload['period'])}; initial cash {payload['initial_cash']:,.0f}; CN commission, T+1, lot size, status/limit checks, daily_basic/financial-indicator PIT joins, and small-cap execution-cost model are enabled.</p>
<div class="summary">
<b>Best by gate/capacity/Sharpe:</b> {html.escape(str(best['scenario']))}
| Sharpe={best['sharpe']:.2f}
| CAGR={best['cagr']:.2%}
| MaxDD={best['max_drawdown_pct']:.2%}
| verdict={html.escape(str(best['gate_verdict']))}
</div>
<h2>Scenario Ranking</h2>
<table>
<thead><tr><th>scenario</th><th>profile</th><th>Sharpe</th><th>CAGR</th><th>MaxDD</th><th>Calmar</th><th>Total Return</th><th>Trades</th><th>Max ADV</th><th>Cost Drag</th><th>Gate</th><th>Parameters</th></tr></thead>
<tbody>{body}</tbody>
</table>
<h2>Bias Audit</h2>
<p>Overall: {html.escape(payload['bias_audit']['overall'])}</p>
<table>
<thead><tr><th>Bias</th><th>Status</th><th>Evidence</th><th>Residual risk</th></tr></thead>
<tbody>{bias_rows}</tbody>
</table>
</body>
</html>
"""


def _strategy_spec(scenario: Dict[str, Any], stock_count: int) -> Dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "signal_formula_key": f"bigquant_{scenario['profile']}",
        "strategy_type": "a_share_small_cap_profile_suite",
        "prediction_direction": "higher_profile_score_is_better",
        "source_url": SOURCE_URLS.get(str(scenario.get("profile")), ""),
        "universe": (
            f"A-share ordinary-account stock universe from daily_cn_ochl ({stock_count} symbols), "
            "excluding ChiNext 300/301 and STAR 688/689 stocks"
        ),
        "required_fields": [
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "turnover",
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "pe",
            "pb",
            "ps_ttm",
            "ps",
            "dv_ttm",
            "turnover_rate_f",
            "volume_ratio",
            "roe",
            "q_roe",
            "netprofit_yoy",
            "q_netprofit_yoy",
            "or_yoy",
            "q_sales_yoy",
            "is_st",
            "tradable",
            "has_daily_bar",
            "is_listed",
            "list_status",
        ],
        "lookback_days": 120,
        "horizon_days": int(scenario.get("rebalance_interval") or 20),
        "execution_lag_days": 1,
        "rebalance_frequency": f"every_{int(scenario.get('rebalance_interval') or 20)}_trading_days",
        "parameters": _strategy_kwargs(scenario),
    }


def _bias_audit_payload() -> Dict[str, Any]:
    return {
        "overall": "Warning",
        "checklist": [
            {
                "bias": "Look-ahead",
                "status": "Pass",
                "evidence": "Signals consume current and prior daily bars; financial indicators are joined by ann_date <= trade date; orders fill on the next trading day.",
                "residual_risk": "Daily_basic fields are assumed point-in-time at trade_date granularity.",
            },
            {
                "bias": "Survivorship",
                "status": "Warning",
                "evidence": "Universe comes from historical OHLC plus daily status/daily_basic sidecars, not a current constituent list.",
                "residual_risk": "OHLC symbols absent from daily_basic and delisted metadata coverage can still affect smallest-cap tails.",
            },
            {
                "bias": "Universe selection",
                "status": "Pass",
                "evidence": "All scenarios use the same historical A-share ordinary-account universe and exclude 300/301/688/689 permission boards before ranking.",
                "residual_risk": "North Exchange and special-permission assets are intentionally outside scope.",
            },
            {
                "bias": "Multiple testing",
                "status": "Warning",
                "evidence": "This is an exploratory six-scenario grid mapped from public BigQuant idea families.",
                "residual_risk": "The top result needs a separate formal full report and walk-forward strict replay before promotion.",
            },
            {
                "bias": "Execution/capacity",
                "status": "Pass",
                "evidence": "Backtester uses CN commission, lot size, T+1, limit/status checks, and small-cap realistic execution-cost model with ADV diagnostics.",
                "residual_risk": "Capacity must be rechecked at intended capital and with live broker-specific fill quality.",
            },
        ],
    }


def _permission_excluded(symbol: str) -> bool:
    return any(str(symbol).startswith(prefix) for prefix in DEFAULT_EXCLUDED_BOARD_PREFIXES)


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scenario": row.get("scenario"),
        "profile": row.get("profile"),
        "sharpe": row.get("sharpe"),
        "cagr": row.get("cagr"),
        "max_drawdown_pct": row.get("max_drawdown_pct"),
        "calmar_ratio": row.get("calmar_ratio"),
        "total_trades": row.get("total_trades"),
        "max_adv_participation": row.get("max_adv_participation"),
        "gate_verdict": row.get("gate_verdict"),
    }


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


if __name__ == "__main__":
    main()
