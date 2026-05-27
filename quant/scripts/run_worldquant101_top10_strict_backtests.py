"""Run strict Backtester reports for WorldQuant 101 Alpha #001-#010."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import numpy as np

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


DEFAULT_START = datetime(2016, 1, 1)
DEFAULT_END = datetime(2025, 12, 31)
DEFAULT_INITIAL_CASH = 20000.0
DEFAULT_HOLDING_DAYS = 5
DEFAULT_MAX_POSITIONS = 20
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
SOURCE_URL = "https://arxiv.org/abs/1601.00991"
PARAMETER_SOURCE_URL = "https://docs.dolphindb.com/en/3.00.5/Tutorials/wq101alpha.html"

COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
CN_DAILY_COST_MODEL = {
    "enabled": True,
    "name": "cn_daily_liquidity_impact",
    "markets": ["CN"],
    "tick_size": 0.01,
    "half_spread_ticks": 0.5,
    "min_slippage_bps": 5,
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.35,
    "volatility_fallback": 0.03,
    "adv_value_field": "adv20_value",
    "volatility_field": "volatility20",
}


@dataclass(frozen=True)
class AlphaSpec:
    number: int
    required_fields: Tuple[str, ...]
    lookback_days: int
    formula_label: str
    signal_note: str

    @property
    def strategy_id(self) -> str:
        return f"worldquant_101_alpha_{self.number:03d}"

    @property
    def title(self) -> str:
        return f"WorldQuant 101 Alpha #{self.number:03d}"


ALPHA_SPECS: Dict[int, AlphaSpec] = {
    1: AlphaSpec(1, ("close",), 25, "worldquant_alpha_001", "Ranked recent signed-power close/volatility response."),
    2: AlphaSpec(2, ("volume", "open", "close"), 8, "worldquant_alpha_002", "Negative correlation between ranked volume shock and ranked intraday return."),
    3: AlphaSpec(3, ("volume", "open"), 10, "worldquant_alpha_003", "Negative correlation between ranked open and ranked volume."),
    4: AlphaSpec(4, ("low",), 9, "worldquant_alpha_004", "Negative time-series rank of cross-sectional low-price rank."),
    5: AlphaSpec(5, ("turnover", "volume", "open", "close"), 10, "worldquant_alpha_005", "Open versus 10-day VWAP proxy, scaled by close versus VWAP proxy rank."),
    6: AlphaSpec(6, ("volume", "open"), 10, "worldquant_alpha_006", "Negative correlation between open and volume."),
    7: AlphaSpec(7, ("volume", "close"), 67, "worldquant_alpha_007", "Volume-conditioned time-series rank of seven-day close change."),
    8: AlphaSpec(8, ("open", "close"), 16, "worldquant_alpha_008", "Cross-sectional rank of the delayed open-sum and return-sum interaction."),
    9: AlphaSpec(9, ("close",), 6, "worldquant_alpha_009", "Trend-continuation else one-day close-change reversal rule."),
    10: AlphaSpec(10, ("close",), 5, "worldquant_alpha_010", "Cross-sectional rank of the Alpha #009 four-day variant."),
}


class WorldQuant101TopNStrategy(DailyBarStrategy):
    def __init__(
        self,
        alpha_number: int,
        symbols: List[str],
        holding_days: int = DEFAULT_HOLDING_DAYS,
        max_position_pct: float = 1.0,
        max_positions: int = DEFAULT_MAX_POSITIONS,
        delisting_risk_guard: bool = True,
        min_trade_price: float = 2.0,
        min_avg_turnover: float = 20000.0,
        liquidity_lookback: int = 20,
        max_recent_suspended_days: int = 0,
    ):
        if alpha_number not in ALPHA_SPECS:
            raise ValueError(f"Unsupported alpha_number: {alpha_number}")
        self.alpha_number = int(alpha_number)
        self.spec = ALPHA_SPECS[self.alpha_number]
        self.max_position_pct = float(max_position_pct)
        self.max_positions = int(max_positions)
        self.delisting_risk_guard = bool(delisting_risk_guard)
        self.min_trade_price = float(min_trade_price)
        self.min_avg_turnover = float(min_avg_turnover)
        self.liquidity_lookback = int(liquidity_lookback)
        self.max_recent_suspended_days = int(max_recent_suspended_days)
        self._risk_exit_symbols = set()
        super().__init__(self.spec.strategy_id, symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return max(90, self.spec.lookback_days + 10, self.liquidity_lookback + 5)

    def on_after_trading(self, context: Any, trading_date: Any) -> None:
        self._risk_exit_symbols = self._exit_delisting_risk_positions()
        try:
            super().on_after_trading(context, trading_date)
        finally:
            self._risk_exit_symbols = set()

    def _execute_rebalance(self, context: Any, trading_date: Any) -> None:
        scores = self._scores()
        if not scores:
            self._liquidate_missing_scores()
            return
        scores.sort(reverse=True)
        selected = scores[: max(1, min(self.max_positions, len(scores)))]
        selected_symbols = {symbol for _, symbol, _ in selected}
        slots = len(selected)
        for _, symbol, price in scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price if price > 0 else None)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol not in selected_symbols and symbol not in {item[1] for item in scores}:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def _scores(self) -> List[Tuple[float, str, float]]:
        dispatch = {
            1: self._alpha001_scores,
            2: self._alpha002_scores,
            3: self._alpha003_scores,
            4: self._alpha004_scores,
            5: self._alpha005_scores,
            6: self._alpha006_scores,
            7: self._alpha007_scores,
            8: self._alpha008_scores,
            9: self._alpha009_scores,
            10: self._alpha010_scores,
        }
        return dispatch[self.alpha_number]()

    def _candidate_symbols(self, min_bars: int) -> Dict[str, Tuple[List[Any], float]]:
        eligible = {}
        risk_exits = getattr(self, "_risk_exit_symbols", set())
        for symbol in self._symbols:
            if symbol in risk_exits:
                continue
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) < min_bars or price <= 0:
                continue
            if self._delisting_entry_risk(symbol, bars[-1]):
                continue
            eligible[symbol] = (bars, price)
        return eligible

    def _alpha001_scores(self) -> List[Tuple[float, str, float]]:
        raw_values = {}
        prices = {}
        for symbol, (bars, price) in self._candidate_symbols(25).items():
            closes = np.asarray([self._adj(bar, "close") for bar in bars], dtype=float)
            if closes.size < 25 or np.any(closes[-25:] <= 0):
                continue
            returns = closes[1:] / closes[:-1] - 1.0
            values = []
            for close_index in range(len(closes) - 5, len(closes)):
                return_index = close_index - 1
                window = returns[return_index - 19:return_index + 1]
                if window.size < 20:
                    values = []
                    break
                base = float(np.std(window, ddof=1)) if returns[return_index] < 0 else float(closes[close_index])
                values.append(float(np.sign(base) * abs(base) ** 2))
            if values:
                raw_values[symbol] = float(np.argmax(values))
                prices[symbol] = price
        ranks = self._rank_map(raw_values)
        return [(float(rank - 0.5), symbol, prices[symbol]) for symbol, rank in ranks.items() if symbol in prices]

    def _alpha002_scores(self) -> List[Tuple[float, str, float]]:
        window = 6
        eligible = self._candidate_symbols(window + 2)
        ranked_delta: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        ranked_intraday: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-window, 0):
            delta_values = {}
            intraday_values = {}
            for symbol, (bars, _) in eligible.items():
                current_bar = bars[offset]
                previous_bar = bars[offset - 2]
                volume = self._volume(current_bar)
                previous_volume = self._volume(previous_bar)
                open_price = self._adj(current_bar, "open")
                close_price = self._adj(current_bar, "close")
                if volume > 0 and previous_volume > 0 and open_price > 0:
                    delta_values[symbol] = float(np.log(volume) - np.log(previous_volume))
                    intraday_values[symbol] = float((close_price - open_price) / open_price)
            delta_ranks = self._rank_map(delta_values)
            intraday_ranks = self._rank_map(intraday_values)
            for symbol in eligible:
                ranked_delta[symbol].append(delta_ranks.get(symbol, float("nan")))
                ranked_intraday[symbol].append(intraday_ranks.get(symbol, float("nan")))
        scores = []
        for symbol, (_, price) in eligible.items():
            signal = -self._correlation(ranked_delta[symbol], ranked_intraday[symbol])
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha003_scores(self) -> List[Tuple[float, str, float]]:
        window = 10
        eligible = self._candidate_symbols(window)
        ranked_open: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        ranked_volume: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-window, 0):
            open_values = {}
            volume_values = {}
            for symbol, (bars, _) in eligible.items():
                open_price = self._adj(bars[offset], "open")
                volume = self._volume(bars[offset])
                if open_price > 0 and volume > 0:
                    open_values[symbol] = float(open_price)
                    volume_values[symbol] = float(volume)
            open_ranks = self._rank_map(open_values)
            volume_ranks = self._rank_map(volume_values)
            for symbol in eligible:
                ranked_open[symbol].append(open_ranks.get(symbol, float("nan")))
                ranked_volume[symbol].append(volume_ranks.get(symbol, float("nan")))
        scores = []
        for symbol, (_, price) in eligible.items():
            signal = -self._correlation(ranked_open[symbol], ranked_volume[symbol])
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha004_scores(self) -> List[Tuple[float, str, float]]:
        window = 9
        eligible = self._candidate_symbols(window)
        ranked_low_history: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-window, 0):
            low_values = {
                symbol: float(self._adj(bars[offset], "low"))
                for symbol, (bars, _) in eligible.items()
                if self._adj(bars[offset], "low") > 0
            }
            low_ranks = self._rank_map(low_values)
            for symbol in eligible:
                ranked_low_history[symbol].append(low_ranks.get(symbol, float("nan")))
        scores = []
        for symbol, (_, price) in eligible.items():
            signal = -self._time_series_rank_last(ranked_low_history[symbol])
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha005_scores(self) -> List[Tuple[float, str, float]]:
        window = 10
        eligible = self._candidate_symbols(window)
        open_vwap_values = {}
        close_vwap_values = {}
        prices = {}
        for symbol, (bars, price) in eligible.items():
            vwaps = np.asarray([self._bar_vwap(bar) for bar in bars[-window:]], dtype=float)
            if vwaps.size < window or not np.isfinite(vwaps).all() or np.any(vwaps <= 0):
                continue
            current_bar = bars[-1]
            current_vwap = float(vwaps[-1])
            open_price = self._adj(current_bar, "open")
            close_price = self._adj(current_bar, "close")
            if open_price <= 0 or close_price <= 0:
                continue
            open_vwap_values[symbol] = float(open_price - np.mean(vwaps))
            close_vwap_values[symbol] = float(close_price - current_vwap)
            prices[symbol] = price
        rank_open_vwap = self._rank_map(open_vwap_values)
        rank_close_vwap = self._rank_map(close_vwap_values)
        scores = []
        for symbol in prices:
            if symbol not in rank_open_vwap or symbol not in rank_close_vwap:
                continue
            signal = rank_open_vwap[symbol] * (-abs(rank_close_vwap[symbol]))
            if np.isfinite(signal):
                scores.append((float(signal), symbol, prices[symbol]))
        return scores

    def _alpha006_scores(self) -> List[Tuple[float, str, float]]:
        window = 10
        scores = []
        for symbol, (bars, price) in self._candidate_symbols(window).items():
            open_values = [float(self._adj(bar, "open")) if self._adj(bar, "open") > 0 else float("nan") for bar in bars[-window:]]
            volume_values = [float(self._volume(bar)) if self._volume(bar) > 0 else float("nan") for bar in bars[-window:]]
            signal = -self._correlation(open_values, volume_values)
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha007_scores(self) -> List[Tuple[float, str, float]]:
        scores = []
        for symbol, (bars, price) in self._candidate_symbols(67).items():
            closes = np.asarray([self._adj(bar, "close") for bar in bars[-67:]], dtype=float)
            volumes = np.asarray([self._volume(bar) for bar in bars[-20:]], dtype=float)
            if closes.size < 67 or volumes.size < 20 or np.any(closes <= 0) or np.any(volumes <= 0):
                continue
            current_volume = float(volumes[-1])
            adv20 = float(np.mean(volumes))
            deltas = closes[7:] - closes[:-7]
            if deltas.size < 60:
                continue
            current_delta = float(deltas[-1])
            if current_volume > adv20:
                signal = -self._time_series_rank_last(np.abs(deltas).tolist()) * np.sign(current_delta)
            else:
                signal = -1.0
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha008_scores(self) -> List[Tuple[float, str, float]]:
        raw_values = {}
        prices = {}
        for symbol, (bars, price) in self._candidate_symbols(16).items():
            current = self._open_return_product(bars, len(bars))
            delayed = self._open_return_product(bars, len(bars) - 10)
            if np.isfinite(current) and np.isfinite(delayed):
                raw_values[symbol] = float(current - delayed)
                prices[symbol] = price
        ranks = self._rank_map(raw_values)
        return [(-float(rank), symbol, prices[symbol]) for symbol, rank in ranks.items() if symbol in prices]

    def _alpha009_scores(self) -> List[Tuple[float, str, float]]:
        scores = []
        for symbol, (bars, price) in self._candidate_symbols(6).items():
            closes = np.asarray([self._adj(bar, "close") for bar in bars[-6:]], dtype=float)
            if closes.size < 6 or not np.isfinite(closes).all() or np.any(closes <= 0):
                continue
            deltas = np.diff(closes)
            current_delta = float(deltas[-1])
            if float(np.min(deltas)) > 0.0 or float(np.max(deltas)) < 0.0:
                signal = current_delta
            else:
                signal = -current_delta
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

    def _alpha010_scores(self) -> List[Tuple[float, str, float]]:
        raw_values = {}
        prices = {}
        for symbol, (bars, price) in self._candidate_symbols(5).items():
            closes = np.asarray([self._adj(bar, "close") for bar in bars[-5:]], dtype=float)
            if closes.size < 5 or not np.isfinite(closes).all() or np.any(closes <= 0):
                continue
            deltas = np.diff(closes)
            current_delta = float(deltas[-1])
            if float(np.min(deltas)) > 0.0 or float(np.max(deltas)) < 0.0:
                raw_values[symbol] = current_delta
            else:
                raw_values[symbol] = -current_delta
            prices[symbol] = price
        ranks = self._rank_map(raw_values)
        return [(float(rank), symbol, prices[symbol]) for symbol, rank in ranks.items() if symbol in prices]

    def _target_quantity(self, context: Any, price: float, slots: int) -> int:
        portfolio = getattr(context, "portfolio", None)
        nav = float(getattr(portfolio, "nav", 0.0) or 0.0)
        if nav <= 0 or price <= 0:
            return 0
        return int((nav * self.max_position_pct / max(1, slots)) / price)

    def _liquidate_missing_scores(self) -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def _exit_delisting_risk_positions(self) -> set:
        exited = set()
        if not self.delisting_risk_guard:
            return exited
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0:
                continue
            bar = self._get_last_bar(symbol)
            if not bar or not self._delisting_exit_risk(symbol, bar):
                continue
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
            exited.add(symbol)
        return exited

    def _delisting_entry_risk(self, symbol: str, bar: Any) -> bool:
        if not self.delisting_risk_guard:
            return False
        if self._bar_bool(bar, "is_st") or self._bar_bool(bar, "_suspended"):
            return True
        if self._bar_bool(bar, "tradable", default=True) is False:
            return True
        if self._bar_bool(bar, "is_listed", default=True) is False:
            return True
        list_status = self._bar_text(bar, "list_status", "L").upper()
        if list_status and list_status != "L":
            return True
        price = self._price(bar)
        if price <= 0.0 or price < self.min_trade_price:
            return True
        if self._recent_suspended_days(symbol) > self.max_recent_suspended_days:
            return True
        return self._average_turnover(symbol) < self.min_avg_turnover

    def _delisting_exit_risk(self, symbol: str, bar: Any) -> bool:
        if self._delisting_entry_risk(symbol, bar):
            return True
        price = self._price(bar)
        return bool(price > 0.0 and price < self.min_trade_price)

    def _average_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-max(1, self.liquidity_lookback):]
        values = [self._bar_turnover(bar) for bar in bars if not self._bar_bool(bar, "_suspended")]
        values = [value for value in values if np.isfinite(value) and value > 0.0]
        return float(np.mean(values)) if values else 0.0

    def _recent_suspended_days(self, symbol: str) -> int:
        bars = self._day_data.get(symbol, [])[-max(1, self.liquidity_lookback):]
        return sum(
            1
            for bar in bars
            if self._bar_bool(bar, "_suspended")
            or self._bar_bool(bar, "tradable", default=True) is False
            or self._bar_bool(bar, "has_daily_bar", default=True) is False
        )

    def _bar_turnover(self, bar: Any) -> float:
        value = self._field(bar, "turnover")
        if value is not None and np.isfinite(float(value)):
            return float(value)
        return self._volume(bar) * self._price(bar)

    def _bar_vwap(self, bar: Any) -> float:
        direct = self._field(bar, "adj_vwap")
        if direct is not None and np.isfinite(float(direct)) and float(direct) > 0:
            return float(direct)
        direct = self._field(bar, "vwap")
        raw_close = self._price(bar)
        raw_vwap = float(direct) if direct is not None and np.isfinite(float(direct)) and float(direct) > 0 else 0.0
        if raw_vwap <= 0.0:
            turnover = self._field(bar, "turnover")
            volume = self._field(bar, "volume")
            if turnover is not None and volume is not None and float(turnover) > 0 and float(volume) > 0:
                turnover_value = float(turnover)
                volume_value = float(volume)
                candidates = [
                    turnover_value / volume_value,
                    turnover_value * 10.0 / volume_value,
                    turnover_value * 1000.0 / volume_value,
                    turnover_value / (volume_value * 100.0),
                ]
                plausible = [item for item in candidates if np.isfinite(item) and item > 0]
                if raw_close > 0 and plausible:
                    bounded = [item for item in plausible if 0.2 <= item / raw_close <= 5.0]
                    pool = bounded or plausible
                    raw_vwap = min(pool, key=lambda item: abs(np.log(item / raw_close)) if item > 0 else float("inf"))
                elif plausible:
                    raw_vwap = plausible[0]
        adj_factor = self._field(bar, "adj_factor")
        if raw_vwap > 0 and adj_factor is not None and np.isfinite(float(adj_factor)) and float(adj_factor) > 0:
            return float(raw_vwap * float(adj_factor))
        return raw_vwap

    @staticmethod
    def _field(bar: Any, field: str, default: Any = None) -> Any:
        return bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)

    @staticmethod
    def _bar_bool(bar: Any, field: str, default: bool = False) -> bool:
        value = bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)
        return bool(value)

    @staticmethod
    def _bar_text(bar: Any, field: str, default: str = "") -> str:
        value = bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)
        return "" if value is None else str(value)

    @staticmethod
    def _volume(bar: Any) -> float:
        value = bar.get("volume", 0.0) if isinstance(bar, dict) else getattr(bar, "volume", 0.0)
        return float(value) if value is not None and value == value else 0.0

    @staticmethod
    def _rank_map(values: Dict[str, float]) -> Dict[str, float]:
        items = sorted((value, symbol) for symbol, value in values.items() if np.isfinite(value))
        count = len(items)
        if count == 0:
            return {}
        ranked = {}
        index = 0
        while index < count:
            end = index + 1
            while end < count and items[end][0] == items[index][0]:
                end += 1
            rank_value = float(((index + 1) + end) / 2.0 / count)
            for _, symbol in items[index:end]:
                ranked[symbol] = rank_value
            index = end
        return ranked

    @staticmethod
    def _time_series_rank_last(values: Iterable[float]) -> float:
        clean = np.asarray(list(values), dtype=float)
        if clean.size == 0 or not np.isfinite(clean).all():
            return float("nan")
        current = clean[-1]
        less = float(np.sum(clean < current))
        equal = float(np.sum(clean == current))
        return float((less + (equal + 1.0) / 2.0) / len(clean))

    @staticmethod
    def _correlation(left: List[float], right: List[float]) -> float:
        x = np.asarray(left, dtype=float)
        y = np.asarray(right, dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if int(mask.sum()) < 2:
            return float("nan")
        x = x[mask]
        y = y[mask]
        if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
            return float("nan")
        return float(np.corrcoef(x, y)[0, 1])

    def _open_return_product(self, bars: List[Any], end: int) -> float:
        start = end - 5
        if start < 1 or end > len(bars):
            return float("nan")
        open_sum = 0.0
        return_sum = 0.0
        for index in range(start, end):
            open_price = self._adj(bars[index], "open")
            current_close = self._adj(bars[index], "close")
            previous_close = self._adj(bars[index - 1], "close")
            if open_price <= 0 or current_close <= 0 or previous_close <= 0:
                return float("nan")
            open_sum += float(open_price)
            return_sum += float(current_close / previous_close - 1.0)
        return float(open_sum * return_sum)

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "alpha_number": self.alpha_number,
            "formula_key": self.spec.formula_label,
            "holding_days": self.holding_days,
            "max_position_pct": self.max_position_pct,
            "max_positions": self.max_positions,
            "delisting_risk_guard": self.delisting_risk_guard,
            "min_trade_price": self.min_trade_price,
            "min_avg_turnover": self.min_avg_turnover,
            "liquidity_lookback": self.liquidity_lookback,
            "max_recent_suspended_days": self.max_recent_suspended_days,
        }


def main() -> None:
    args = _parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    alphas = _parse_alpha_numbers(args.alphas)
    symbols, benchmark_provider, benchmark_meta, lot_sizes, survivorship = _load_inputs(start, end, args.symbol_limit)
    rows = []
    for alpha_number in alphas:
        spec = ALPHA_SPECS[alpha_number]
        print(f"Running {spec.strategy_id} on {len(symbols)} symbols from {start.date()} to {end.date()}", flush=True)
        strict_report = _run_one(spec, symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship, start, end, args.initial_cash)
        report_path, result_path = _write_strategy_report(spec, strict_report, start, end, args.initial_cash, args.output_root)
        metrics = strict_report.get("metrics") or {}
        row = {
            "strategy_id": spec.strategy_id,
            "report_path": str(report_path),
            "result_path": str(result_path),
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
            "max_adv_participation": (strict_report.get("capacity") or {}).get("max_adv_participation"),
            "cost_drag_pct": (strict_report.get("costs") or {}).get("cost_drag_pct"),
            "go_no_go": _verdict(metrics),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    batch_path = _write_batch_report(rows, start, end, args.initial_cash, args.output_root)
    print(json.dumps({"batch_report": str(batch_path), "rows": rows}, ensure_ascii=False, indent=2), flush=True)


def _load_inputs(
    start: datetime,
    end: datetime,
    symbol_limit: Optional[int],
) -> Tuple[List[str], BenchmarkProvider, Dict[str, Any], Dict[str, int], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        symbols = _load_ashare_symbols(db_provider, start, end, symbol_limit)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        survivorship = _cn_survivorship_audit(db_provider, start, end, formula_key="worldquant_101_top10")
    finally:
        db_provider.disconnect()
    return symbols, benchmark_provider, benchmark_meta, lot_sizes, survivorship


def _load_ashare_symbols(db_provider: DuckDBProvider, start: datetime, end: datetime, limit: Optional[int]) -> List[str]:
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = db_provider.storage.conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{{5}}$')
          AND NOT starts_with(symbol, '200')
          AND symbol != '000300'
        ORDER BY symbol
        {limit_clause}
        """,
        [start, end],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_one(
    spec: AlphaSpec,
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
) -> Dict[str, Any]:
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_execution_liquidity_features=True,
    )
    strategy = WorldQuant101TopNStrategy(alpha_number=spec.number, symbols=symbols)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(CN_DAILY_COST_MODEL)}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
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
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=initial_cash,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, initial_cash) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        start,
        end,
        initial_cash,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _write_strategy_report(
    spec: AlphaSpec,
    strict_report: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    output_root: str,
) -> Tuple[Path, Path]:
    root = Path(output_root)
    strategy_dir = root / spec.strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result = _result_payload(spec, strict_report)
    result_path = strategy_dir / "last_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    row = _hypothesis_row(spec, strict_report, start, end, initial_cash)
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, _detail_html(spec))
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    run_dir = strategy_dir / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (run_dir / f"{timestamp}_strict_backtest_report.html").write_text(html, encoding="utf-8")
    (run_dir / f"{timestamp}_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report_path, result_path


def _result_payload(spec: AlphaSpec, strict_report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": f"{spec.strategy_id}_strict",
        "discovered": 0,
        "evaluated": 0,
        "integrated": 0,
        "backtested": 1,
        "rejected": 0,
        "walkforward_passed": 0,
        "errors": [],
        "log": [],
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def _hypothesis_row(
    spec: AlphaSpec,
    strict_report: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    verdict = _verdict(metrics)
    return {
        "strategy_id": spec.strategy_id,
        "title": spec.title,
        "source": "worldquant101",
        "source_url": SOURCE_URL,
        "status": "needs_walkforward_validation" if verdict == "pass" else "needs_more_research",
        "decision_reason": _decision_reason(metrics),
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "strict backtest",
                    "verdict": verdict,
                    "conclusion": _decision_reason(metrics),
                    "method": (
                        "项目 Backtester；收盘后生成信号，T+1 下一交易日执行；约束包括 A 股佣金税费、100 股手数、"
                        "涨跌停/停牌拒单、股息送转处理、2% ADV 参与率与 cn_daily_liquidity_impact。"
                    ),
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": spec.strategy_id,
                "strategy_type": "worldquant_factor_long_only_top20",
                "signal_formula_key": spec.formula_label,
                "required_fields": list(spec.required_fields),
                "lookback_days": spec.lookback_days,
                "horizon_days": DEFAULT_HOLDING_DAYS,
                "execution_lag_days": 1,
                "source_url": SOURCE_URL,
                "parameter_source_url": PARAMETER_SOURCE_URL,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "initial_cash": float(initial_cash),
                "max_positions": DEFAULT_MAX_POSITIONS,
                "max_position_pct": 1.0,
                "strategy_logic": {
                    "core_idea": (
                        "把 WorldQuant 101 公式因子转换成 A 股 long-only 横截面排序组合；只持有当前有效分最高的 20 只股票。"
                    ),
                    "universe": "本地 daily_cn_ochl 覆盖的 A 股股票，剔除 200 前缀、000300 指数、ST、停牌、非上市、低价和低流动性样本。",
                    "entry_filters": [
                        "调仓日必须有可交易日线 bar",
                        "价格不低于 2 元",
                        "最近流动性窗口平均 turnover 不低于 20000",
                        "持仓遇到 ST/停牌/退市状态或低价风险时每日尝试退出",
                    ],
                    "ranking_rule": spec.signal_note,
                    "portfolio_construction": "每 5 个交易日等权目标持有 Top20；信号不足时只持有可用候选，其余现金。",
                    "rebalance_rule": "收盘后计算因子和目标权重，订单由 Backtester 在下一交易日执行。",
                    "exit_rule": "不在新 Top20、触发退市风险、或信号不可得的持仓会提交卖出。",
                    "risk_budget": "单策略满仓目标，单票目标约 5%；最终成交仍受 Backtester 风控和 ADV 参与率限制。",
                },
            }
        },
    }


def _detail_html(spec: AlphaSpec) -> str:
    fields = ", ".join(spec.required_fields)
    return f"""
<h3>策略执行逻辑</h3>
<div class="table-wrap"><table><thead><tr><th>部分</th><th>说明</th></tr></thead><tbody>
<tr><td>信号详细说明</td><td>{spec.title}，公式键 {spec.formula_label}，本地输入字段：{fields}。#005 的 VWAP 使用 turnover/volume 推导，并按 adj_factor 转为后复权信号价。</td></tr>
<tr><td>每日运行步骤</td><td>每日收盘缓存日线，先处理持仓退市/停牌/低价风险；到 5 日调仓周期时计算全市场因子分，选择有效分最高的 20 只股票等权配置。</td></tr>
<tr><td>执行约束摘要</td><td>订单 T+1 执行，Backtester 启用 A 股佣金税费、100 股手数、涨跌停/停牌约束、股息送转处理、2% ADV 参与率和 cn_daily_liquidity_impact。</td></tr>
</tbody></table></div>
"""


def _write_batch_report(rows: List[Dict[str, Any]], start: datetime, end: datetime, initial_cash: float, output_root: str) -> Path:
    root = Path(output_root) / "worldquant_101_top10_strict_batch"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": f"{start.date()}-{end.date()}",
        "initial_cash": float(initial_cash),
        "rows": rows,
    }
    (root / "last_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_rows = "\n".join(
        "<tr>"
        f"<td>{row['strategy_id']}</td>"
        f"<td>{_fmt(row.get('sharpe'))}</td>"
        f"<td>{_pct(row.get('cagr'))}</td>"
        f"<td>{_pct(row.get('max_drawdown_pct'))}</td>"
        f"<td>{row.get('total_trades')}</td>"
        f"<td>{row.get('go_no_go')}</td>"
        f"<td><a href='../{row['strategy_id']}/strict_backtest_report.html'>strict report</a></td>"
        "</tr>"
        for row in rows
    )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>WorldQuant 101 Top10 Strict Backtest Batch</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;line-height:1.5;color:#17202a}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d8dee4;padding:8px;text-align:left}}th{{background:#f6f8fa}}code{{background:#f6f8fa;padding:1px 4px;border-radius:4px}}</style>
</head><body>
<h1>WorldQuant 101 前 10 因子严格回测汇总</h1>
<p>区间：<code>{start.date()} 至 {end.date()}</code>；初始资金：<code>{initial_cash:.0f}</code>；组合转换：long-only Top20，5 日调仓，T+1 执行，CN 日线冲击成本。</p>
<table><thead><tr><th>策略</th><th>Sharpe</th><th>CAGR</th><th>MaxDD</th><th>Trades</th><th>Verdict</th><th>报告</th></tr></thead><tbody>
{html_rows}
</tbody></table>
</body></html>
"""
    report_path = root / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _insert_detail_section(html: str, detail: str) -> str:
    marker = "</body>"
    return html.replace(marker, detail + marker) if marker in html else html + detail


def _decision_reason(metrics: Dict[str, Any]) -> str:
    sharpe = float(metrics.get("sharpe") or 0.0)
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    trades = int(metrics.get("total_trades") or 0)
    return f"Strict backtest: Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}, trades={trades}."


def _verdict(metrics: Dict[str, Any]) -> str:
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = abs(float(metrics.get("max_drawdown_pct") or 0.0))
    trades = int(metrics.get("total_trades") or 0)
    if trades <= 50 or cagr < 0.05:
        return "fail"
    if cagr < 0.10:
        return "pass" if max_dd <= 0.15 else "fail"
    if cagr < 0.15:
        return "pass" if max_dd <= 0.25 else "fail"
    if cagr < 0.20:
        return "pass" if max_dd <= 0.30 else "fail"
    return "pass" if max_dd <= 0.50 else "fail"


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _parse_args() -> Any:
    parser = ArgumentParser(description="Run strict Backtester reports for WorldQuant 101 Alpha #001-#010")
    parser.add_argument("--alphas", default="1-10", help="Alpha numbers or ranges, e.g. 1-4,6,10")
    parser.add_argument("--start", default=DEFAULT_START.date().isoformat())
    parser.add_argument("--end", default=DEFAULT_END.date().isoformat())
    parser.add_argument("--initial-cash", type=float, default=DEFAULT_INITIAL_CASH)
    parser.add_argument("--symbol-limit", type=int, default=None, help="Limit symbols for smoke tests")
    parser.add_argument("--output-root", default=str(REPORT_ROOT))
    return parser.parse_args()


def _parse_alpha_numbers(text: str) -> List[int]:
    numbers = set()
    for chunk in str(text).replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            numbers.update(range(int(start), int(end) + 1))
        else:
            numbers.add(int(chunk))
    selected = sorted(number for number in numbers if number in ALPHA_SPECS)
    if not selected:
        raise ValueError(f"No supported alphas in {text!r}")
    return selected


if __name__ == "__main__":
    main()
