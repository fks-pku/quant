import importlib.util
import re
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, RawStrategy, EvaluationReport, StrategySpec

logger = logging.getLogger(__name__)


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
        return "".join(word.capitalize() for word in cleaned.strip().split()) + "Strategy"

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
        lookback = int(getattr(spec, "lookback_days", 20) or 20)
        horizon = int(getattr(spec, "horizon_days", 5) or 5)
        formula_key = getattr(spec, "signal_formula_key", "") or ""
        body = self._formula_body(formula_key)

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

from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context


@strategy("{name}")
class {class_name}(DailyBarStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        lookback: int = {lookback},
        holding_days: int = {horizon},
        max_position_pct: float = 0.10,
    ):
        self._symbols = symbols or [{symbols_str}]
        self.lookback = lookback
        self.max_position_pct = max_position_pct
        super().__init__("{name}", self._symbols, holding_days=holding_days)

    @property
    def _max_keep_hint(self) -> int:
        return max(self.lookback * 3, self.lookback + 5)

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("{class_name}")

    def _execute_rebalance(self, context: "Context", trading_date: date) -> None:
        candidates = []
        for symbol in self._symbols:
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
        slots = max(1, len(candidates))
        for _, symbol, price in candidates:
            target_qty = self._target_quantity(context, price, slots)
            current_pos = self._positions.get(symbol, 0)
            delta = target_qty - current_pos
            if delta > 0:
                self.buy(symbol, int(delta), "MARKET", price)
            elif delta < 0:
                self.sell(symbol, int(abs(delta)), "MARKET", price)

    def _target_quantity(self, context: "Context", price: float, slots: int) -> int:
        portfolio = getattr(context, "portfolio", None)
        nav = float(getattr(portfolio, "nav", 0.0) or 0.0)
        if nav <= 0 or price <= 0:
            return 0
        return int((nav * self.max_position_pct / max(1, slots)) / price)

{body}

    def _get_parameters(self) -> Dict[str, Any]:
        return {{
            "lookback": self.lookback,
            "max_position_pct": self.max_position_pct,
            "formula_key": "{formula_key or "manual_review_required"}",
        }}
'''

    def _formula_body(self, formula_key: str) -> str:
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
        return '''    def _signal(self, symbol: str) -> float:
        self.logger.warning("Manual implementation required for unsupported formula: %s", symbol)
        return 0.0
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
        symbols_text = ", ".join(symbols)
        lookback = int(getattr(spec, "lookback_days", 20) or 20)
        horizon = int(getattr(spec, "horizon_days", 5) or 5)
        return f"""strategy:
  name: {strategy_id}
  enabled: false
  status: candidate
  priority: 999

parameters:
  symbols: [{symbols_text}]
  lookback: {lookback}
  holding_days: {horizon}
  max_position_pct: 0.10
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
