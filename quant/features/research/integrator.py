import importlib.util
import re
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from quant.analytics.signal_kernels import SUPPORTED_FORMULAS, uses_positive_signal_filter
from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, RawStrategy, EvaluationReport, StrategySpec

logger = logging.getLogger(__name__)

RESEARCH_DEFAULT_MAX_POSITION_PCT = 1.0


class StrategyIntegrator:
    def __init__(
        self,
        strategies_dir: Path,
        strategy_registry: Optional[Dict[str, Any]] = None,
        strategy_params: Optional[Dict[str, Any]] = None,
        on_register: Optional[Callable] = None,
        research_store: Optional[ResearchStore] = None,
    ):
        self.strategies_dir = strategies_dir
        self._registry = strategy_registry if strategy_registry is not None else {}
        self._params = strategy_params if strategy_params is not None else {}
        self._dir_map: Dict[str, str] = {}
        self._on_register = on_register
        self.research_store = research_store

    @property
    def registry(self) -> Dict[str, Any]:
        return self._registry

    def integrate(self, raw: RawStrategy, report: EvaluationReport, spec: Optional[StrategySpec] = None) -> Optional[str]:
        name = self._strategy_id(raw, spec)
        class_name = self._to_class_name(raw.title)
        strategy_dir = self.strategies_dir / name

        if strategy_dir.exists():
            strategy_file = strategy_dir / "strategy.py"
            if not strategy_file.exists():
                logger.warning(f"Strategy directory {strategy_dir} already exists without strategy.py, skipping")
                return None
            if not self._load_generated_strategy(strategy_file, name):
                return None
            entry = self._register_in_runtime(name, class_name, raw, report, spec)
            if self.research_store is not None:
                self.research_store.upsert_candidate(entry)
            logger.info(f"Reused existing candidate strategy {name}")
            return name

        try:
            strategy_dir.mkdir(parents=True)
            code = self._generate_strategy_code(name, raw, report, spec)
            strategy_file = strategy_dir / "strategy.py"
            strategy_file.write_text(code, encoding="utf-8")
            readme = self._generate_readme(raw, report, spec)
            (strategy_dir / "README.md").write_text(readme, encoding="utf-8")
            config = self._generate_config(raw, report, spec)
            (strategy_dir / "config.yaml").write_text(config, encoding="utf-8")
            if not self._load_generated_strategy(strategy_file, name):
                return None
        except Exception as e:
            logger.error(f"Failed to write strategy files: {e}")
            return None

        entry = self._register_in_runtime(name, class_name, raw, report, spec)
        if self.research_store is not None:
            self.research_store.upsert_candidate(entry)
        return name

    def write_screening_source(
        self,
        raw: RawStrategy,
        report: EvaluationReport,
        spec: Optional[StrategySpec] = None,
    ) -> Optional[Path]:
        if spec is None or getattr(spec, "status", "") != "ready":
            return None
        name = self._strategy_id(raw, spec)
        strategy_dir = self.strategies_dir / "reject" / name
        try:
            strategy_dir.mkdir(parents=True, exist_ok=True)
            code = self._generate_strategy_code(name, raw, report, spec)
            strategy_file = strategy_dir / "strategy.py"
            if not strategy_file.exists() or strategy_file.read_text(encoding="utf-8") != code:
                strategy_file.write_text(code, encoding="utf-8")
            readme = self._generate_readme(raw, report, spec)
            readme_file = strategy_dir / "README.md"
            if not readme_file.exists() or readme_file.read_text(encoding="utf-8") != readme:
                readme_file.write_text(readme, encoding="utf-8")
            config = self._generate_config(raw, report, spec)
            config_file = strategy_dir / "config.yaml"
            if not config_file.exists() or config_file.read_text(encoding="utf-8") != config:
                config_file.write_text(config, encoding="utf-8")
            return strategy_file
        except Exception as e:
            logger.warning(f"Failed to write screening strategy source for {name}: {e}")
            return None

    def get_registry_entry(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Return the registry entry for strategy_id, or None."""
        for info in self._registry.values():
            if info.get("id") == strategy_id:
                return info
        return None

    @staticmethod
    def _normalize_name(title: str) -> str:
        hyphen_replaced = title.replace("-", " ")
        cleaned = re.sub(r"[^a-zA-Z0-9_\s]", "", hyphen_replaced)
        underscored = re.sub(r"\s+", "_", cleaned.strip()).lower()
        return re.sub(r"_+", "_", underscored).strip("_")

    def _strategy_id(self, raw: RawStrategy, spec: Optional[StrategySpec] = None) -> str:
        spec_id = getattr(spec, "strategy_id", "") if spec is not None else ""
        return self._normalize_name(spec_id or raw.title)

    def _load_generated_strategy(self, strategy_file: Path, strategy_id: str) -> bool:
        try:
            module_name = f"quant.generated_strategies.{strategy_id}.strategy"
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            if spec is None or spec.loader is None:
                return False
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return True
        except Exception as e:
            logger.error(f"Failed to load generated strategy {strategy_id}: {e}")
            return False

    @staticmethod
    def _to_class_name(title: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", title)
        class_name = "".join(word.capitalize() for word in cleaned.strip().split()) + "Strategy"
        if not class_name or not class_name[0].isalpha():
            return f"Strategy{class_name}"
        return class_name

    def _generate_strategy_code(
        self,
        name: str,
        raw: RawStrategy,
        report: EvaluationReport,
        spec: Optional[StrategySpec] = None,
    ) -> str:
        class_name = self._to_class_name(raw.title)
        default_symbols = _a_share_symbols(getattr(spec, "universe", []) or report.recommended_symbols)
        symbols_str = ", ".join(f'"{s}"' for s in default_symbols)
        default_symbols_block = _default_symbols_block(default_symbols)
        default_symbols_expr = "DEFAULT_SYMBOLS" if default_symbols_block else f"[{symbols_str}]"
        lookback = int(getattr(spec, "lookback_days", 20) or 20)
        horizon = int(getattr(spec, "horizon_days", 5) or 5)
        formula_key = getattr(spec, "signal_formula_key", "") or ""
        body = self._formula_body(formula_key)
        rebalance_body = self._generic_rebalance_body(uses_positive_signal_filter(formula_key))

        return f'''"""{raw.title}

Source: {raw.source} ({raw.source_url})
Authors: {raw.authors or "Unknown"}
Type: {report.strategy_type}
Formula: {formula_key or "manual_review_required"}
Summary: {report.summary}
"""

from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np
import pandas as pd

from quant.analytics.signal_kernels import compute_signal
from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


{default_symbols_block}
@strategy("{name}")
class {class_name}(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        lookback: int = {lookback},
        holding_days: int = {horizon},
        max_position_pct: float = {RESEARCH_DEFAULT_MAX_POSITION_PCT:.1f},
        max_positions: int = 20,
        delisting_risk_guard: bool = True,
        min_trade_price: float = 2.0,
        min_avg_turnover: float = 20000.0,
        liquidity_lookback: int = 20,
        max_recent_suspended_days: int = 0,
    ):
        self._symbols = symbols or {default_symbols_expr}
        self.lookback = lookback
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        self.delisting_risk_guard = bool(delisting_risk_guard)
        self.min_trade_price = float(min_trade_price)
        self.min_avg_turnover = float(min_avg_turnover)
        self.liquidity_lookback = int(liquidity_lookback)
        self.max_recent_suspended_days = int(max_recent_suspended_days)
        self._risk_exit_symbols = set()
        self._signal_cache_key = None
        self._signal_cache_scores: Dict[str, float] = {{}}
        super().__init__("{name}", self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return max(self.lookback * 3, self.lookback + 5, self.liquidity_lookback + 5)

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("{class_name}")

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._risk_exit_symbols = self._exit_delisting_risk_positions(context)
        try:
            super().on_after_trading(context, trading_date)
        finally:
            self._risk_exit_symbols = set()

{rebalance_body}
    def _target_quantity(self, context: "Context", price: float, slots: int) -> int:
        portfolio = getattr(context, "portfolio", None)
        nav = float(getattr(portfolio, "nav", 0.0) or 0.0)
        if nav <= 0 or price <= 0:
            return 0
        return int((nav * self.max_position_pct / max(1, slots)) / price)

    @staticmethod
    def _bar_volume(bar: Any) -> float:
        value = bar.get("volume", 0.0) if isinstance(bar, dict) else getattr(bar, "volume", 0.0)
        return float(value) if value is not None and value == value else 0.0

    def _bar_turnover(self, bar: Any) -> float:
        value = bar.get("turnover", None) if isinstance(bar, dict) else getattr(bar, "turnover", None)
        if value is not None and value == value:
            return float(value)
        return self._bar_volume(bar) * self._adj(bar, "close")

    def _exit_delisting_risk_positions(self, context: "Context") -> set:
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
        if price > 0.0 and price < self.min_trade_price:
            return True
        return False

    def _average_turnover(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])[-max(1, self.liquidity_lookback):]
        values = [self._bar_turnover(bar) for bar in bars if not self._bar_bool(bar, "_suspended")]
        values = [value for value in values if value == value and value > 0.0]
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

    @staticmethod
    def _bar_bool(bar: Any, field: str, default: bool = False) -> bool:
        value = bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)
        return bool(value)

    @staticmethod
    def _bar_text(bar: Any, field: str, default: str = "") -> str:
        value = bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)
        return "" if value is None else str(value)

{body}

    def _get_parameters(self) -> Dict[str, Any]:
        return {{
            "lookback": self.lookback,
            "max_position_pct": self.max_position_pct,
            "max_positions": self.max_positions,
            "delisting_risk_guard": self.delisting_risk_guard,
            "min_trade_price": self.min_trade_price,
            "min_avg_turnover": self.min_avg_turnover,
            "liquidity_lookback": self.liquidity_lookback,
            "max_recent_suspended_days": self.max_recent_suspended_days,
            "formula_key": "{formula_key or "manual_review_required"}",
        }}
'''

    @staticmethod
    def _generic_rebalance_body(positive_only: bool = True) -> str:
        if not positive_only:
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        candidates = []
        risk_exits = getattr(self, "_risk_exit_symbols", set())
        scores = self._signal_scores() if hasattr(self, "_signal_scores") else {}
        for symbol, signal in scores.items():
            if symbol in risk_exits:
                continue
            price = self._get_last_price(symbol)
            if signal == signal and price > 0:
                candidates.append((signal, symbol, price))
        if not candidates:
            return
        candidates.sort(reverse=True)
        selected = candidates[:max(1, min(self.max_positions, len(candidates)))]
        selected_symbols = {symbol for _, symbol, _ in selected}
        slots = len(selected)
        for _, symbol, price in candidates:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            current_pos = self._positions.get(symbol, 0)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

'''
        return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        candidates = []
        risk_exits = getattr(self, "_risk_exit_symbols", set())
        for symbol in self._symbols:
            if symbol in risk_exits:
                continue
            signal = self._signal(symbol)
            price = self._get_last_price(symbol)
            current_pos = self._positions.get(symbol, 0)
            if signal <= 0 and current_pos > 0:
                self.sell(symbol, int(current_pos), "MARKET", price if price > 0 else None)
            elif signal > 0 and price > 0:
                candidates.append((signal, symbol, price))
        if not candidates:
            return
        candidates.sort(reverse=True)
        selected = candidates[:max(1, min(self.max_positions, len(candidates)))]
        selected_symbols = {symbol for _, symbol, _ in selected}
        slots = len(selected)
        for _, symbol, price in candidates:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            current_pos = self._positions.get(symbol, 0)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

'''

    def _formula_body(self, formula_key: str) -> str:
        if formula_key in SUPPORTED_FORMULAS:
            return self._analytics_formula_body(formula_key)
        if formula_key == "momentum_close_return":
            return '''    def _signal(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.lookback + 1:
            return 0.0
        past = closes[-self.lookback - 1]
        current = closes[-1]
        if past <= 0:
            return 0.0
        return float(current / past - 1.0)
'''
        if formula_key == "mean_reversion_close_to_ma":
            return '''    def _signal(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        if len(closes) < self.lookback:
            return 0.0
        current = closes[-1]
        moving_average = float(np.mean(closes[-self.lookback:]))
        if moving_average <= 0:
            return 0.0
        return float((moving_average - current) / moving_average)
'''
        if formula_key == "volatility_breakout_atr":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.lookback + 1:
            return 0.0
        current_close = self._adj(bars[-1], "close")
        previous_high = max(self._adj(bar, "high") for bar in bars[-self.lookback - 1:-1])
        atr = self._atr(symbol)
        if atr <= 0:
            return 0.0
        return float((current_close - previous_high) / atr)

    def _atr(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        if len(bars) < self.lookback + 1:
            return 0.0
        ranges = []
        for index in range(-self.lookback, 0):
            high = self._adj(bars[index], "high")
            low = self._adj(bars[index], "low")
            prev_close = self._adj(bars[index - 1], "close")
            ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        return float(np.mean(ranges)) if ranges else 0.0
'''
        if formula_key == "ashare_short_reversal_5d":
            return '''    def _signal(self, symbol: str) -> float:
        closes = self._get_closes(symbol)
        lookback = max(1, int(self.lookback))
        if len(closes) < lookback + 1:
            return 0.0
        past = closes[-lookback - 1]
        current = closes[-1]
        if past <= 0:
            return 0.0
        return float(-(current / past - 1.0))
'''
        if formula_key == "ashare_volume_exhaustion_reversal":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window + 1:
            return 0.0
        closes = np.asarray([self._adj(bar, "close") for bar in bars], dtype=float)
        if closes.size < 6 or closes[-6] <= 0:
            return 0.0
        recent_return = float(closes[-1] / closes[-6] - 1.0)
        if recent_return >= 0.0:
            return 0.0
        volumes = np.asarray([self._bar_volume(bar) for bar in bars[-window:]], dtype=float)
        avg_volume = float(np.mean(volumes)) if volumes.size else 0.0
        current_volume = float(volumes[-1]) if volumes.size else 0.0
        if avg_volume <= 0 or current_volume <= 0:
            return 0.0
        return float((-recent_return) * np.log1p(current_volume / avg_volume))
'''
        if formula_key == "ashare_volume_dryup_pullback":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window + 1:
            return 0.0
        closes = np.asarray([self._adj(bar, "close") for bar in bars], dtype=float)
        if closes.size < 6 or closes[-6] <= 0:
            return 0.0
        recent_return = float(closes[-1] / closes[-6] - 1.0)
        if recent_return >= 0.0:
            return 0.0
        volumes = np.asarray([self._bar_volume(bar) for bar in bars[-window:]], dtype=float)
        avg_volume = float(np.mean(volumes)) if volumes.size else 0.0
        current_volume = float(volumes[-1]) if volumes.size else 0.0
        if avg_volume <= 0 or current_volume <= 0:
            return 0.0
        dryup = max(0.0, 1.0 - current_volume / avg_volume)
        return float((-recent_return) * dryup)
'''
        if formula_key == "ashare_lottery_demand_avoidance":
            return '''    def _signal(self, symbol: str) -> float:
        closes = np.asarray(self._get_closes(symbol), dtype=float)
        window = max(5, int(self.lookback))
        if closes.size < window + 1 or np.any(closes[-window - 1:] <= 0):
            return 0.0
        returns = closes[-window:] / closes[-window - 1:-1] - 1.0
        if not np.isfinite(returns).all():
            return 0.0
        max_return = max(0.0, float(np.max(returns)))
        volatility = max(0.0, float(np.std(returns, ddof=1))) if returns.size > 1 else 0.0
        return float(1.0 / (1.0 + max_return + volatility * np.sqrt(252.0)))
'''
        if formula_key == "ashare_low_volatility_defensive":
            return '''    def _signal(self, symbol: str) -> float:
        closes = np.asarray(self._get_closes(symbol), dtype=float)
        window = max(5, int(self.lookback))
        if closes.size < window + 1 or np.any(closes[-window - 1:] <= 0):
            return 0.0
        returns = closes[-window:] / closes[-window - 1:-1] - 1.0
        if not np.isfinite(returns).all():
            return 0.0
        volatility = max(0.0, float(np.std(returns, ddof=1))) if returns.size > 1 else 0.0
        return float(1.0 / (1.0 + volatility * np.sqrt(252.0)))
'''
        if formula_key == "ashare_gap_down_reversal":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        if len(bars) < 2:
            return 0.0
        previous_close = self._adj(bars[-2], "close")
        current_open = self._adj(bars[-1], "open")
        if previous_close <= 0 or current_open <= 0:
            return 0.0
        return float(-(current_open / previous_close - 1.0))
'''
        if formula_key == "ashare_volatility_scaled_reversal":
            return '''    def _signal(self, symbol: str) -> float:
        closes = np.asarray(self._get_closes(symbol), dtype=float)
        window = max(10, int(self.lookback))
        if closes.size < window + 1 or np.any(closes[-window - 1:] <= 0):
            return 0.0
        recent_return = float(closes[-1] / closes[-6] - 1.0) if closes.size >= 6 and closes[-6] > 0 else 0.0
        if recent_return >= 0.0:
            return 0.0
        returns = closes[-window:] / closes[-window - 1:-1] - 1.0
        volatility = float(np.std(returns, ddof=1)) if returns.size > 1 and np.isfinite(returns).all() else 0.0
        if volatility <= 0.0:
            return 0.0
        return float((-recent_return) / volatility)
'''
        if formula_key == "ashare_liquidity_weighted_low_volatility":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window + 1:
            return 0.0
        closes = np.asarray([self._adj(bar, "close") for bar in bars[-window - 1:]], dtype=float)
        if closes.size < window + 1 or np.any(closes <= 0):
            return 0.0
        returns = closes[1:] / closes[:-1] - 1.0
        volatility = float(np.std(returns, ddof=1)) * np.sqrt(252.0) if returns.size > 1 else 0.0
        turnovers = np.asarray([self._bar_turnover(bar) for bar in bars[-window:]], dtype=float)
        avg_turnover = float(np.mean(turnovers)) if turnovers.size else 0.0
        if avg_turnover <= 0.0:
            return 0.0
        return float(np.log1p(avg_turnover) / (1.0 + max(0.0, volatility)))
'''
        if formula_key == "ashare_low_volatility_momentum":
            return '''    def _signal(self, symbol: str) -> float:
        closes = np.asarray(self._get_closes(symbol), dtype=float)
        window = max(10, int(self.lookback))
        if closes.size < window + 1 or np.any(closes[-window - 1:] <= 0):
            return 0.0
        momentum = float(closes[-1] / closes[-window - 1] - 1.0)
        if momentum <= 0.0:
            return 0.0
        returns = closes[-window:] / closes[-window - 1:-1] - 1.0
        volatility = float(np.std(returns, ddof=1)) * np.sqrt(252.0) if returns.size > 1 and np.isfinite(returns).all() else 0.0
        return float(momentum / (1.0 + max(0.0, volatility)))
'''
        if formula_key == "ashare_industry_prosperity_trend_crowding_rotation":
            return '''    def _signal(self, symbol: str) -> float:
        return float(self._industry_rotation_scores().get(symbol, 0.0))

    def _industry_rotation_scores(self) -> Dict[str, float]:
        window = max(20, int(self.lookback))
        short_window = max(10, min(20, window))
        rows = []
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            if len(bars) < window + 1:
                continue
            industry = self._bar_industry(bars[-1])
            if not industry:
                continue
            closes = np.asarray([self._adj(bar, "close") for bar in bars[-window - 1:]], dtype=float)
            if closes.size < window + 1 or np.any(closes <= 0):
                continue
            returns = closes[1:] / closes[:-1] - 1.0
            if not np.isfinite(returns).all():
                continue
            momentum_short = float(closes[-1] / closes[-short_window - 1] - 1.0)
            momentum_long = float(closes[-1] / closes[0] - 1.0)
            vol_short = float(np.std(returns[-short_window:], ddof=1)) if short_window > 1 else 0.0
            trend = momentum_long / max(vol_short, 1e-12)
            positive_trend = 1.0 if momentum_short > 0.0 else 0.0
            volumes = np.asarray([self._bar_volume(bar) for bar in bars[-window:]], dtype=float)
            avg_volume = float(np.mean(volumes)) if volumes.size else 0.0
            latest_volume = float(volumes[-1]) if volumes.size else 0.0
            crowding = latest_volume / max(avg_volume, 1e-12) + max(0.0, vol_short * np.sqrt(252.0))
            stock_tiebreaker = momentum_short
            rows.append((symbol, industry, momentum_short, positive_trend, trend, crowding, stock_tiebreaker))
        if not rows:
            return {}
        industries = sorted({item[1] for item in rows})
        by_industry = {}
        for industry in industries:
            group = [item for item in rows if item[1] == industry]
            by_industry[industry] = {
                "prosperity": float(np.mean([item[2] for item in group])),
                "breadth": float(np.mean([item[3] for item in group])),
                "trend": float(np.mean([item[4] for item in group])),
                "crowding": float(np.mean([item[5] for item in group])),
            }
        prosperity_rank = self._rank_dict({k: v["prosperity"] for k, v in by_industry.items()})
        breadth_rank = self._rank_dict({k: v["breadth"] for k, v in by_industry.items()})
        trend_rank = self._rank_dict({k: v["trend"] for k, v in by_industry.items()})
        crowding_rank = self._rank_dict({k: v["crowding"] for k, v in by_industry.items()})
        stock_rank = self._rank_dict({item[0]: item[6] for item in rows})
        scores = {}
        for symbol, industry, *_ in rows:
            scores[symbol] = (
                0.30 * prosperity_rank.get(industry, 0.0)
                + 0.25 * breadth_rank.get(industry, 0.0)
                + 0.35 * trend_rank.get(industry, 0.0)
                - 0.20 * crowding_rank.get(industry, 0.0)
                + 0.01 * stock_rank.get(symbol, 0.0)
            )
        return scores

    @staticmethod
    def _rank_dict(values: Dict[str, float]) -> Dict[str, float]:
        valid = [(key, float(value)) for key, value in values.items() if value == value]
        if not valid:
            return {}
        ordered = sorted(valid, key=lambda item: item[1])
        denom = max(1, len(ordered) - 1)
        return {key: idx / denom for idx, (key, _) in enumerate(ordered)}

    @staticmethod
    def _bar_industry(bar: Any) -> str:
        for field in ("l1_code", "l2_code", "industry_code", "l1_name", "industry_name"):
            value = bar.get(field, "") if isinstance(bar, dict) else getattr(bar, field, "")
            text = "" if value is None else str(value).strip()
            if text:
                return text
        return ""
'''
        if formula_key == "ashare_range_contraction_breakout":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window:
            return 0.0
        highs = np.asarray([self._adj(bar, "high") for bar in bars[-window:]], dtype=float)
        lows = np.asarray([self._adj(bar, "low") for bar in bars[-window:]], dtype=float)
        closes = np.asarray([self._adj(bar, "close") for bar in bars[-window:]], dtype=float)
        if not (np.isfinite(highs).all() and np.isfinite(lows).all() and np.isfinite(closes).all()):
            return 0.0
        high_roll = float(np.max(highs))
        low_roll = float(np.min(lows))
        current = float(closes[-1])
        if high_roll <= low_roll or current <= 0.0:
            return 0.0
        range_position = max(0.0, min(1.0, (current - low_roll) / (high_roll - low_roll)))
        daily_range = np.divide(highs - lows, closes, out=np.zeros_like(closes), where=closes > 0)
        range_vol = max(0.0, float(np.mean(daily_range))) if daily_range.size else 0.0
        return float(range_position / (1.0 + range_vol * 100.0))
'''
        if formula_key == "ashare_gap_down_liquid_reversal":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window + 1:
            return 0.0
        previous_close = self._adj(bars[-2], "close")
        current_open = self._adj(bars[-1], "open")
        if previous_close <= 0 or current_open <= 0:
            return 0.0
        gap_signal = -(current_open / previous_close - 1.0)
        if gap_signal <= 0.0:
            return 0.0
        turnovers = np.asarray([self._bar_turnover(bar) for bar in bars[-window:]], dtype=float)
        avg_turnover = float(np.mean(turnovers)) if turnovers.size else 0.0
        if avg_turnover <= 0.0:
            return 0.0
        return float(gap_signal * np.log1p(avg_turnover))
'''
        if formula_key == "ashare_turnover_stability_factor":
            return '''    def _signal(self, symbol: str) -> float:
        bars = self._day_data.get(symbol, [])
        window = max(10, int(self.lookback))
        if len(bars) < window:
            return 0.0
        turnovers = np.asarray([self._bar_turnover(bar) for bar in bars[-window:]], dtype=float)
        turnovers = turnovers[np.isfinite(turnovers) & (turnovers > 0.0)]
        if turnovers.size < 2:
            return 0.0
        avg_turnover = float(np.mean(turnovers))
        turnover_vol = float(np.std(turnovers, ddof=1))
        if avg_turnover <= 0.0 or turnover_vol <= 0.0:
            return 0.0
        return float(np.log1p(avg_turnover) * avg_turnover / turnover_vol)
'''
        if formula_key == "joinquant_small_cap_low_price_factor":
            return '''    def _signal(self, symbol: str) -> float:
        bar = self._get_last_bar(symbol)
        if not bar:
            return 0.0
        if self._delisting_entry_risk(symbol, bar):
            return 0.0
        price = self._price(bar)
        if price <= 0.0 or price > 20.0:
            return 0.0
        market_cap = self._market_cap(bar)
        if market_cap <= 0.0:
            return 0.0
        return float(1.0 / market_cap)

    @staticmethod
    def _market_cap(bar: Any) -> float:
        for field in ("total_mv", "circ_mv", "market_cap", "total_market_cap", "float_market_cap", "circulating_market_cap"):
            value = bar.get(field, None) if isinstance(bar, dict) else getattr(bar, field, None)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(numeric) and numeric > 0.0:
                return numeric
        return 0.0
'''
        if formula_key == "worldquant_alpha_001":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = []
        for symbol in self._symbols:
            raw_signal = self._worldquant_alpha_001_raw(symbol)
            price = self._get_last_price(symbol)
            if np.isfinite(raw_signal) and price > 0:
                raw_scores.append((raw_signal, symbol, price))
        if not raw_scores:
            return
        raw_scores.sort()
        count = len(raw_scores)
        ranked = []
        for rank_index, (_, symbol, price) in enumerate(raw_scores, start=1):
            ranked.append((rank_index / count - 0.5, symbol, price))
        ranked.sort(reverse=True)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = [(signal, symbol, price) for signal, symbol, price in ranked if signal > 0][:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        if not selected:
            for _, symbol, price in ranked:
                current_pos = self._positions.get(symbol, 0)
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
            return
        slots = len(selected)
        for signal, symbol, price in ranked:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            if signal <= 0 and current_pos > 0:
                self.sell(symbol, int(current_pos), "MARKET", price)
            elif signal > 0:
                target_qty = self._target_quantity(context, price, slots)
                delta = target_qty - current_pos
                if delta > 0:
                    self.buy(symbol, int(delta), "MARKET", price)
                elif delta < 0:
                    self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_001_raw(self, symbol: str) -> float:
        closes = np.asarray(self._get_closes(symbol), dtype=float)
        if len(closes) < self.lookback + 5:
            return float("nan")
        returns = closes[1:] / closes[:-1] - 1.0
        values = []
        for close_index in range(len(closes) - 5, len(closes)):
            return_index = close_index - 1
            if return_index < self.lookback - 1:
                return float("nan")
            if returns[return_index] < 0:
                window = returns[return_index - self.lookback + 1:return_index + 1]
                base = float(np.std(window, ddof=1))
            else:
                base = float(closes[close_index])
            values.append(float(np.sign(base) * abs(base) ** 2))
        return float(np.argmax(values))

    def _signal(self, symbol: str) -> float:
        return self._worldquant_alpha_001_raw(symbol)
'''
        if formula_key == "worldquant_alpha_002":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = self._worldquant_alpha_002_scores()
        if not raw_scores:
            return
        raw_scores.sort(reverse=True)
        count = len(raw_scores)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = [(signal, symbol, price) for signal, symbol, price in raw_scores if signal > 0][:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        if not selected:
            for _, symbol, price in raw_scores:
                current_pos = self._positions.get(symbol, 0)
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
            return
        slots = len(selected)
        for signal, symbol, price in raw_scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_002_scores(self) -> List[tuple[float, str, float]]:
        corr_window = max(2, int(self.lookback))
        eligible = {}
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) >= corr_window + 2 and price > 0:
                eligible[symbol] = (bars, price)
        if len(eligible) < 2:
            return []

        ranked_delta: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        ranked_intraday: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-corr_window, 0):
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

    @staticmethod
    def _rank_map(values: Dict[str, float]) -> Dict[str, float]:
        items = sorted((value, symbol) for symbol, value in values.items() if np.isfinite(value))
        count = len(items)
        if count == 0:
            return {}
        return {symbol: (rank_index + 1) / count for rank_index, (_, symbol) in enumerate(items)}

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

    @staticmethod
    def _volume(bar: Any) -> float:
        if isinstance(bar, dict):
            value = bar.get("volume", 0.0)
        else:
            value = getattr(bar, "volume", 0.0)
        return float(value) if value is not None and value == value else 0.0

    def _signal(self, symbol: str) -> float:
        for signal, score_symbol, _ in self._worldquant_alpha_002_scores():
            if score_symbol == symbol:
                return signal
        return 0.0
'''
        if formula_key == "worldquant_alpha_003":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = self._worldquant_alpha_003_scores()
        if not raw_scores:
            return
        raw_scores.sort(reverse=True)
        count = len(raw_scores)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = [(signal, symbol, price) for signal, symbol, price in raw_scores if signal > 0][:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        if not selected:
            for _, symbol, price in raw_scores:
                current_pos = self._positions.get(symbol, 0)
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
            return
        slots = len(selected)
        for signal, symbol, price in raw_scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_003_scores(self) -> List[tuple[float, str, float]]:
        corr_window = max(2, int(self.lookback))
        eligible = {}
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) >= corr_window and price > 0:
                eligible[symbol] = (bars, price)
        if len(eligible) < 2:
            return []

        ranked_open: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        ranked_volume: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-corr_window, 0):
            open_values = {}
            volume_values = {}
            for symbol, (bars, _) in eligible.items():
                current_bar = bars[offset]
                open_price = self._adj(current_bar, "open")
                volume = self._volume(current_bar)
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

    @staticmethod
    def _rank_map(values: Dict[str, float]) -> Dict[str, float]:
        items = sorted((value, symbol) for symbol, value in values.items() if np.isfinite(value))
        count = len(items)
        if count == 0:
            return {}
        return {symbol: (rank_index + 1) / count for rank_index, (_, symbol) in enumerate(items)}

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

    @staticmethod
    def _volume(bar: Any) -> float:
        if isinstance(bar, dict):
            value = bar.get("volume", 0.0)
        else:
            value = getattr(bar, "volume", 0.0)
        return float(value) if value is not None and value == value else 0.0

    def _signal(self, symbol: str) -> float:
        for signal, score_symbol, _ in self._worldquant_alpha_003_scores():
            if score_symbol == symbol:
                return signal
        return 0.0
'''
        if formula_key == "worldquant_alpha_004":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = self._worldquant_alpha_004_scores()
        if not raw_scores:
            return
        raw_scores.sort(reverse=True)
        count = len(raw_scores)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = raw_scores[:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        slots = len(selected)
        for _, symbol, price in raw_scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_004_scores(self) -> List[tuple[float, str, float]]:
        rank_window = max(2, int(self.lookback))
        eligible = {}
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) >= rank_window and price > 0:
                eligible[symbol] = (bars, price)
        if len(eligible) < 2:
            return []

        ranked_low_history: Dict[str, List[float]] = {symbol: [] for symbol in eligible}
        for offset in range(-rank_window, 0):
            low_values = {}
            for symbol, (bars, _) in eligible.items():
                low_price = self._adj(bars[offset], "low")
                if low_price > 0:
                    low_values[symbol] = float(low_price)
            low_ranks = self._rank_map(low_values)
            for symbol in eligible:
                ranked_low_history[symbol].append(low_ranks.get(symbol, float("nan")))

        scores = []
        for symbol, (_, price) in eligible.items():
            ts_rank = self._time_series_rank_last(ranked_low_history[symbol])
            signal = -ts_rank
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

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
    def _time_series_rank_last(values: List[float]) -> float:
        clean = np.asarray(values, dtype=float)
        if clean.size == 0 or not np.isfinite(clean).all():
            return float("nan")
        current = clean[-1]
        less = float(np.sum(clean < current))
        equal = float(np.sum(clean == current))
        return float((less + (equal + 1.0) / 2.0) / len(clean))

    def _signal(self, symbol: str) -> float:
        for signal, score_symbol, _ in self._worldquant_alpha_004_scores():
            if score_symbol == symbol:
                return signal
        return 0.0
'''
        if formula_key == "worldquant_alpha_006":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = self._worldquant_alpha_006_scores()
        if not raw_scores:
            return
        raw_scores.sort(reverse=True)
        count = len(raw_scores)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = [(signal, symbol, price) for signal, symbol, price in raw_scores if signal > 0][:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        if not selected:
            for _, symbol, price in raw_scores:
                current_pos = self._positions.get(symbol, 0)
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
            return
        slots = len(selected)
        for signal, symbol, price in raw_scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_006_scores(self) -> List[tuple[float, str, float]]:
        corr_window = max(2, int(self.lookback))
        scores = []
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) < corr_window or price <= 0:
                continue
            open_values = []
            volume_values = []
            for bar in bars[-corr_window:]:
                open_price = self._adj(bar, "open")
                volume = self._volume(bar)
                open_values.append(float(open_price) if open_price > 0 else float("nan"))
                volume_values.append(float(volume) if volume > 0 else float("nan"))
            signal = -self._correlation(open_values, volume_values)
            if np.isfinite(signal):
                scores.append((float(signal), symbol, price))
        return scores

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

    @staticmethod
    def _volume(bar: Any) -> float:
        if isinstance(bar, dict):
            value = bar.get("volume", 0.0)
        else:
            value = getattr(bar, "volume", 0.0)
        return float(value) if value is not None and value == value else 0.0

    def _signal(self, symbol: str) -> float:
        for signal, score_symbol, _ in self._worldquant_alpha_006_scores():
            if score_symbol == symbol:
                return signal
        return 0.0
'''
        if formula_key == "worldquant_alpha_010":
            return '''    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        raw_scores = self._worldquant_alpha_010_scores()
        if not raw_scores:
            return
        raw_scores.sort(reverse=True)
        count = len(raw_scores)
        top_count = max(1, min(self.max_positions, int(np.ceil(count * 0.01))))
        selected = raw_scores[:top_count]
        selected_symbols = {symbol for _, symbol, _ in selected}
        slots = len(selected)
        for _, symbol, price in raw_scores:
            current_pos = self._positions.get(symbol, 0)
            if symbol not in selected_symbols:
                if current_pos > 0:
                    self.sell(symbol, int(current_pos), "MARKET", price)
                continue
            target_qty = self._target_quantity(context, price, slots)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _worldquant_alpha_010_scores(self) -> List[tuple[float, str, float]]:
        delta_window = max(2, int(self.lookback))
        raw_values = {}
        prices = {}
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            price = self._get_last_price(symbol)
            if len(bars) < delta_window + 1 or price <= 0:
                continue
            closes = np.asarray([self._adj(bar, "close") for bar in bars[-delta_window - 1:]], dtype=float)
            if closes.size < delta_window + 1 or not np.isfinite(closes).all():
                continue
            deltas = np.diff(closes)
            if deltas.size < delta_window or not np.isfinite(deltas).all():
                continue
            current_delta = float(deltas[-1])
            if float(np.min(deltas)) > 0.0 or float(np.max(deltas)) < 0.0:
                raw_values[symbol] = current_delta
            else:
                raw_values[symbol] = -current_delta
            prices[symbol] = price
        ranks = self._rank_map(raw_values)
        return [
            (float(signal), symbol, prices[symbol])
            for symbol, signal in ranks.items()
            if np.isfinite(signal) and symbol in prices
        ]

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

    def _signal(self, symbol: str) -> float:
        for signal, score_symbol, _ in self._worldquant_alpha_010_scores():
            if score_symbol == symbol:
                return signal
        return 0.0
'''
        return '''    def _signal(self, symbol: str) -> float:
        self.logger.warning("Manual implementation required for unsupported formula: %s", symbol)
        return 0.0
'''

    @staticmethod
    def _analytics_formula_body(formula_key: str) -> str:
        return f'''    def _signal(self, symbol: str) -> float:
        return float(self._signal_scores().get(symbol, 0.0))

    def _signal_scores(self) -> Dict[str, float]:
        cache_key = self._current_signal_cache_key()
        if cache_key == self._signal_cache_key:
            return dict(self._signal_cache_scores)
        frame = self._signal_frame()
        if frame.empty:
            self._signal_cache_key = cache_key
            self._signal_cache_scores = {{}}
            return {{}}
        signal = compute_signal("{formula_key}", frame, self.lookback)
        if signal is None or getattr(signal, "empty", True):
            self._signal_cache_key = cache_key
            self._signal_cache_scores = {{}}
            return {{}}
        latest = signal.iloc[-1].dropna()
        scores = {{str(symbol): float(value) for symbol, value in latest.items() if value == value}}
        self._signal_cache_key = cache_key
        self._signal_cache_scores = scores
        return dict(scores)

    def _signal_frame(self) -> pd.DataFrame:
        rows = []
        for symbol, bars in self._day_data.items():
            for index, bar in enumerate(bars):
                row = self._bar_record(bar, symbol)
                if row.get("date") is None:
                    row["date"] = index
                if row.get("date") is not None:
                    rows.append(row)
        return pd.DataFrame(rows)

    def _current_signal_cache_key(self) -> tuple:
        values = []
        for symbol in self._symbols:
            bars = self._day_data.get(symbol, [])
            last = bars[-1] if bars else None
            values.append((symbol, len(bars), self._bar_date(last) if last is not None else None))
        return tuple(values)

    def _bar_record(self, bar: Any, symbol: str) -> Dict[str, Any]:
        fields = (
            "date", "timestamp", "open", "high", "low", "close",
            "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor",
            "volume", "turnover", "turnover_rate", "turnover_rate_f",
            "total_mv", "circ_mv", "market_cap", "total_market_cap",
            "float_market_cap", "circulating_market_cap",
            "l1_code", "l2_code", "industry_code", "l1_name", "industry_name",
        )
        row = {{"symbol": symbol}}
        for field in fields:
            value = self._bar_value(bar, field)
            if value is not None:
                row[field] = value
        row["date"] = row.get("date") or row.get("timestamp")
        return row

    @staticmethod
    def _bar_value(bar: Any, field: str) -> Any:
        return bar.get(field, None) if isinstance(bar, dict) else getattr(bar, field, None)

    def _bar_date(self, bar: Any) -> Any:
        return self._bar_value(bar, "date") or self._bar_value(bar, "timestamp")
'''

    def _generate_readme(self, raw: RawStrategy, report: EvaluationReport, spec: Optional[StrategySpec] = None) -> str:
        return f"""# {raw.title}

## Source
- **URL:** {raw.source_url}
- **Authors:** {raw.authors or "Unknown"}
- **Published:** {raw.published_date or "Unknown"}

## Evaluation
- **Suitability Score:** {report.suitability_score}/10
- **Admission Score:** {getattr(report, "admission_score", 0.0)}/10
- **Signal Quality Score:** {getattr(report, "signal_quality_score", 0.0)}/10
- **Complexity Score:** {report.complexity_score}/10
- **Data Requirement:** {report.data_requirement}
- **Daily Adaptable:** {report.daily_adaptable}
- **Estimated Edge:** {report.estimated_edge * 100:.1f}%
- **Type:** {report.strategy_type}
- **Formula Key:** {getattr(spec, "signal_formula_key", "") or "manual_review_required"}
- **Required Data Fields:** {", ".join(getattr(report, "required_data_fields", []) or []) or "Unknown"}
- **Validation Tests:** {", ".join(getattr(report, "validation_tests", []) or []) or "Unknown"}

## Summary
{report.summary}
"""

    def _generate_config(self, raw: RawStrategy, report: EvaluationReport, spec: Optional[StrategySpec] = None) -> str:
        strategy_id = self._strategy_id(raw, spec)
        symbols = _a_share_symbols(getattr(spec, "universe", []) or report.recommended_symbols)
        symbols_text = ", ".join(f'"{symbol}"' for symbol in symbols)
        if len(symbols) > 20:
            symbols_yaml = "symbols:\n" + "\n".join(f'    - "{symbol}"' for symbol in symbols)
        else:
            symbols_yaml = f"symbols: [{symbols_text}]"
        lookback = int(getattr(spec, "lookback_days", 20) or 20)
        horizon = int(getattr(spec, "horizon_days", 5) or 5)
        return f"""strategy:
  name: {strategy_id}
  enabled: false
  status: candidate
  priority: 999

parameters:
  {symbols_yaml}
  lookback: {lookback}
  holding_days: {horizon}
  max_position_pct: {RESEARCH_DEFAULT_MAX_POSITION_PCT:.1f}
  max_positions: 20
  delisting_risk_guard: true
  min_trade_price: 2.0
  min_avg_turnover: 20000.0
  liquidity_lookback: 20
  max_recent_suspended_days: 0
"""

    def _register_in_runtime(
        self,
        name: str,
        class_name: str,
        raw: RawStrategy,
        report: EvaluationReport,
        spec: Optional[StrategySpec] = None,
    ) -> Dict[str, Any]:
        strategy_id = name
        entry = {
            "id": strategy_id,
            "name": raw.title,
            "description": raw.description[:200],
            "status": "candidate",
            "priority": max(info.get("priority", 0) for info in self._registry.values()) + 1 if self._registry else 1,
            "doc_file": f"{name}.md",
            "backtest": {},
            "research_meta": {
                "source": raw.source,
                "source_url": raw.source_url,
                "suitability_score": report.suitability_score,
                "admission_score": getattr(report, "admission_score", 0.0),
                "signal_quality_score": getattr(report, "signal_quality_score", 0.0),
                "research_confidence_score": getattr(report, "research_confidence_score", 0.0),
                "data_risk_score": getattr(report, "data_risk_score", 0.0),
                "bias_risk_score": getattr(report, "bias_risk_score", 0.0),
                "complexity_score": report.complexity_score,
                "data_requirement": report.data_requirement,
                "daily_adaptable": report.daily_adaptable,
                "estimated_edge": report.estimated_edge,
                "strategy_type": report.strategy_type,
                "economic_rationale_score": report.economic_rationale_score,
                "factor_uniqueness_score": report.factor_uniqueness_score,
                "data_availability_score": report.data_availability_score,
                "implementation_score": report.implementation_score,
                "overfit_risk_score": report.overfit_risk_score,
                "cost_capacity_score": report.cost_capacity_score,
                "regime_robustness_score": report.regime_robustness_score,
                "required_data_fields": list(getattr(report, "required_data_fields", []) or []),
                "validation_tests": list(getattr(report, "validation_tests", []) or []),
                "score_breakdown": dict(getattr(report, "score_breakdown", {}) or {}),
                "discovery_quality": dict((raw.metadata or {}).get("discovery_quality", {}) or {}),
                "strategy_spec": {
                    "strategy_id": strategy_id,
                    "strategy_type": getattr(spec, "strategy_type", ""),
                    "signal_formula_key": getattr(spec, "signal_formula_key", ""),
                    "horizon_days": getattr(spec, "horizon_days", 0),
                    "lookback_days": getattr(spec, "lookback_days", 0),
                    "execution_lag_days": getattr(spec, "execution_lag_days", 0),
                    "universe": list(getattr(spec, "universe", []) or []),
                    "required_fields": list(getattr(spec, "required_fields", []) or []),
                    "status": getattr(spec, "status", ""),
                } if spec is not None else {},
                "risk_flags": report.risk_flags,
                "rejection_reason": report.rejection_reason,
                "discovered_at": "",
                "evaluated_at": "",
            },
        }
        self._registry[class_name] = entry
        self._dir_map[strategy_id] = name
        lookback = int(getattr(spec, "lookback_days", 20) or 20)
        self._params[strategy_id] = {
            "lookback": {"type": "int", "default": lookback, "description": "Default lookback period"},
        }
        if self._on_register:
            self._on_register(class_name, entry)
        logger.info(f"Registered candidate strategy {strategy_id}")
        return entry


def _a_share_symbols(symbols) -> list[str]:
    resolved = [str(symbol) for symbol in symbols or [] if re.fullmatch(r"\d{6}", str(symbol))]
    return resolved or list(DEFAULT_A_SHARE_SYMBOLS)


def _default_symbols_block(symbols) -> str:
    if len(symbols or []) <= 20:
        return ""
    body = ",\n    ".join(f'"{symbol}"' for symbol in symbols)
    return f"DEFAULT_SYMBOLS = [\n    {body}\n]\n\n\n"
