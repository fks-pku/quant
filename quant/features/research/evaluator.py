import json
import logging
from typing import Optional

from quant.features.research.models import RawStrategy, EvaluationReport
from quant.features.cio.llm_adapters.base import LLMAdapter

logger = logging.getLogger(__name__)


class StrategyEvaluator:
    _PROMPT_TEMPLATE = (
        "Evaluate this quantitative trading strategy for daily-bar (EOD) trading.\n\n"
        "Title: {title}\n"
        "Description: {description}\n\n"
        "Respond ONLY with a JSON object containing these exact keys:\n"
        '- "suitability_score": float (0-10, how suitable for daily-bar trading)\n'
        '- "complexity_score": float (0-10, implementation complexity)\n'
        '- "data_requirement": string ("low", "medium", "high-frequency")\n'
        '- "daily_adaptable": boolean (can a high-frequency version be adapted to daily bars?)\n'
        '- "estimated_edge": float (estimated annual return as decimal, e.g. 0.12 for 12%)\n'
        '- "recommended_symbols": list of strings (e.g. ["AAPL", "SPY"])\n'
        '- "strategy_type": string (e.g. "momentum", "mean_reversion", "stat_arb")\n'
        '- "summary": string (one-sentence assessment)\n'
    )

    def __init__(self, llm_adapter: Optional[LLMAdapter] = None):
        self.llm_adapter = llm_adapter

    def evaluate(self, raw: RawStrategy) -> EvaluationReport:
        if self.llm_adapter is None:
            logger.warning("No LLM adapter configured, returning neutral evaluation")
            return self._neutral_report()

        prompt = self._PROMPT_TEMPLATE.format(title=raw.title, description=raw.description[:2000])
        context = {"source": raw.source, "source_url": raw.source_url}

        try:
            result = self.llm_adapter.analyze(prompt, context)
            return self._parse_result(result)
        except Exception as e:
            logger.warning(f"LLM evaluation failed for '{raw.title}': {e}")
            return self._neutral_report()

    def _parse_result(self, result: dict) -> EvaluationReport:
        if not isinstance(result, dict):
            return self._neutral_report()
        return EvaluationReport(
            suitability_score=float(result.get("suitability_score", 0)),
            complexity_score=float(result.get("complexity_score", 5)),
            data_requirement=str(result.get("data_requirement", "unknown")),
            daily_adaptable=bool(result.get("daily_adaptable", False)),
            estimated_edge=float(result.get("estimated_edge", 0)),
            recommended_symbols=list(result.get("recommended_symbols", [])),
            strategy_type=str(result.get("strategy_type", "unknown")),
            summary=str(result.get("summary", "")),
        )

    @staticmethod
    def _neutral_report() -> EvaluationReport:
        return EvaluationReport(
            suitability_score=0.0,
            complexity_score=5.0,
            data_requirement="unknown",
            daily_adaptable=False,
            estimated_edge=0.0,
            recommended_symbols=[],
            strategy_type="unknown",
            summary="Evaluation failed",
        )
