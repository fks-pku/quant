import json
import logging
from typing import Optional

from quant.features.research.models import RawStrategy, EvaluationReport
from quant.features.research.evaluation_rubric import apply_professional_rubric, heuristic_evaluation
from quant.domain.ports.llm import LLMAdapterLike as LLMAdapter

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
        '- "economic_rationale_score": float (0-2, theory or behavioral rationale)\n'
        '- "factor_uniqueness_score": float (0-2, not explained by known factors)\n'
        '- "data_availability_score": float (0-2, daily OHLCV and accessible data preferred)\n'
        '- "implementation_score": float (0-2, simple daily implementation with <=5 parameters)\n'
        '- "overfit_risk_score": float (0-2, lower parameter/data-snooping risk scores higher)\n'
        '- "cost_capacity_score": float (0-1, liquidity and transaction-cost robustness)\n'
        '- "regime_robustness_score": float (0-1, plausible across regimes)\n'
        '- "required_data_fields": list of strings required to test the signal without look-ahead bias\n'
        '- "validation_tests": list of strings required before implementation (rank_ic, fdr_control, etc.)\n'
        '- "evidence_notes": list of concise evidence notes or caveats\n'
        '- "risk_flags": list of strings (e.g. ["survivorship_bias"])\n'
        '- "rejection_reason": string (empty when suitable)\n'
    )

    def __init__(self, llm_adapter: Optional[LLMAdapter] = None, rubric_config: Optional[dict] = None):
        self.llm_adapter = llm_adapter
        self.rubric_config = rubric_config or {}

    def evaluate(self, raw: RawStrategy) -> EvaluationReport:
        if self.llm_adapter is None:
            logger.info("No LLM adapter configured, using professional heuristic evaluation")
            return heuristic_evaluation(raw, self.rubric_config)

        prompt = self._PROMPT_TEMPLATE.format(title=raw.title, description=raw.description[:2000])
        context = {"source": raw.source, "source_url": raw.source_url}

        try:
            result = self.llm_adapter.analyze(prompt, context)
            return apply_professional_rubric(raw, self._parse_result(result), self.rubric_config)
        except Exception as e:
            logger.warning(f"LLM evaluation failed for '{raw.title}': {e}")
            report = heuristic_evaluation(raw, self.rubric_config)
            report.risk_flags = sorted(set(list(report.risk_flags or []) + ["llm_evaluation_failed"]))
            return report

    def _parse_result(self, result: dict) -> EvaluationReport:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return self._neutral_report()
        if not isinstance(result, dict):
            return self._neutral_report()
        return EvaluationReport(
            suitability_score=self._float(result.get("suitability_score"), 0),
            complexity_score=self._float(result.get("complexity_score"), 5),
            data_requirement=str(result.get("data_requirement", "unknown")),
            daily_adaptable=self._bool(result.get("daily_adaptable", False)),
            estimated_edge=self._float(result.get("estimated_edge"), 0),
            recommended_symbols=self._list(result.get("recommended_symbols", [])),
            strategy_type=str(result.get("strategy_type", "unknown")),
            summary=str(result.get("summary", "")),
            economic_rationale_score=self._float(result.get("economic_rationale_score"), 0),
            factor_uniqueness_score=self._float(result.get("factor_uniqueness_score"), 0),
            data_availability_score=self._float(result.get("data_availability_score"), 0),
            implementation_score=self._float(result.get("implementation_score"), 0),
            overfit_risk_score=self._float(result.get("overfit_risk_score"), 0),
            cost_capacity_score=self._float(result.get("cost_capacity_score"), 0),
            regime_robustness_score=self._float(result.get("regime_robustness_score"), 0),
            admission_score=self._float(result.get("admission_score"), 0),
            signal_quality_score=self._float(result.get("signal_quality_score"), 0),
            research_confidence_score=self._float(result.get("research_confidence_score"), 0),
            data_risk_score=self._float(result.get("data_risk_score"), 0),
            bias_risk_score=self._float(result.get("bias_risk_score"), 0),
            required_data_fields=self._list(result.get("required_data_fields", [])),
            validation_tests=self._list(result.get("validation_tests", [])),
            evidence_notes=self._list(result.get("evidence_notes", [])),
            score_breakdown=self._dict(result.get("score_breakdown", {})),
            risk_flags=self._list(result.get("risk_flags", [])),
            rejection_reason=str(result.get("rejection_reason", "")),
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
            risk_flags=["evaluation_failed"],
            rejection_reason="Evaluation failed",
        )

    @staticmethod
    def _float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return bool(value)

    @staticmethod
    def _list(value) -> list:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    @staticmethod
    def _dict(value) -> dict:
        return value if isinstance(value, dict) else {}
