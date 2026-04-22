import re
import logging
from pathlib import Path
from typing import Dict, Optional

from quant.features.research.models import RawStrategy, EvaluationReport
from quant.api.state.runtime import AVAILABLE_STRATEGIES, STRATEGY_PARAMETERS, _STRATEGY_DIR_MAP, _save_strategy_state

logger = logging.getLogger(__name__)


class StrategyIntegrator:
    def __init__(self, strategies_dir: Optional[Path] = None):
        if strategies_dir is None:
            from quant.features.strategies import __file__ as _strat_file
            strategies_dir = Path(_strat_file).parent
        self.strategies_dir = strategies_dir

    def integrate(self, raw: RawStrategy, report: EvaluationReport) -> Optional[str]:
        name = self._normalize_name(raw.title)
        class_name = self._to_class_name(raw.title)
        strategy_dir = self.strategies_dir / name

        if strategy_dir.exists():
            logger.warning(f"Strategy directory {strategy_dir} already exists, skipping")
            return None

        try:
            strategy_dir.mkdir(parents=True)
            code = self._generate_strategy_code(name, raw, report)
            (strategy_dir / "strategy.py").write_text(code, encoding="utf-8")
            readme = self._generate_readme(raw, report)
            (strategy_dir / "README.md").write_text(readme, encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write strategy files: {e}")
            return None

        self._register_in_runtime(name, class_name, raw, report)
        return name

    @staticmethod
    def _normalize_name(title: str) -> str:
        hyphen_replaced = title.replace("-", " ")
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", hyphen_replaced)
        return re.sub(r"\s+", "_", cleaned.strip()).lower()

    @staticmethod
    def _to_class_name(title: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", title)
        return "".join(word.capitalize() for word in cleaned.strip().split()) + "Strategy"

    def _generate_strategy_code(self, name: str, raw: RawStrategy, report: EvaluationReport) -> str:
        class_name = self._to_class_name(raw.title)
        default_symbols = report.recommended_symbols or ["AAPL"]
        symbols_str = ", ".join(f'"{s}"' for s in default_symbols)

        return f'''"""{raw.title}

Source: {raw.source} ({raw.source_url})
Authors: {raw.authors or "Unknown"}
Type: {report.strategy_type}
Summary: {report.summary}
"""

from typing import Any, List

from quant.features.strategies import Strategy, strategy


@strategy("{class_name}")
class {class_name}(Strategy):
    def __init__(self, symbols: List[str] = None):
        super().__init__(name="{class_name}")
        self._symbols = symbols or [{symbols_str}]

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_data(self, context: Any, data: Any) -> None:
        # TODO: Implement {report.strategy_type} logic based on paper
        pass

    def on_before_trading(self, context: Any, trading_date: Any) -> None:
        pass

    def on_after_trading(self, context: Any, trading_date: Any) -> None:
        pass
'''

    def _generate_readme(self, raw: RawStrategy, report: EvaluationReport) -> str:
        return f"""# {raw.title}

## Source
- **URL:** {raw.source_url}
- **Authors:** {raw.authors or "Unknown"}
- **Published:** {raw.published_date or "Unknown"}

## Evaluation
- **Suitability Score:** {report.suitability_score}/10
- **Complexity Score:** {report.complexity_score}/10
- **Data Requirement:** {report.data_requirement}
- **Daily Adaptable:** {report.daily_adaptable}
- **Estimated Edge:** {report.estimated_edge * 100:.1f}%
- **Type:** {report.strategy_type}

## Summary
{report.summary}
"""

    def _register_in_runtime(self, name: str, class_name: str, raw: RawStrategy, report: EvaluationReport) -> None:
        strategy_id = name
        AVAILABLE_STRATEGIES[class_name] = {
            "id": strategy_id,
            "name": raw.title,
            "description": raw.description[:200],
            "status": "candidate",
            "priority": max(info.get("priority", 0) for info in AVAILABLE_STRATEGIES.values()) + 1,
            "doc_file": f"{name}.md",
            "backtest": {},
            "research_meta": {
                "source": raw.source,
                "source_url": raw.source_url,
                "suitability_score": report.suitability_score,
                "complexity_score": report.complexity_score,
                "data_requirement": report.data_requirement,
                "daily_adaptable": report.daily_adaptable,
                "estimated_edge": report.estimated_edge,
                "discovered_at": "",
                "evaluated_at": "",
            },
        }
        _STRATEGY_DIR_MAP[strategy_id] = name
        STRATEGY_PARAMETERS[strategy_id] = {
            "lookback": {"type": "int", "default": 20, "description": "Default lookback period"},
        }
        _save_strategy_state()
        logger.info(f"Registered candidate strategy {strategy_id}")
