import uuid
from datetime import datetime
from typing import List

from quant.research.models import RawIdea, ScoredIdea
from quant.research.templates.prompts.analyst_prompt import ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT
from quant.strategies.registry import StrategyRegistry


class AnalystAgent:
    def __init__(self, llm_adapter):
        self.llm = llm_adapter
        self.registry = StrategyRegistry

    def _get_registered_strategies_summary(self) -> str:
        strategies = self.registry.list_strategies()
        if not strategies:
            return "No registered strategies"
        return ", ".join(strategies)

    async def analyze(self, ideas: List[RawIdea], min_score: float = 60) -> List[ScoredIdea]:
        registered = self._get_registered_strategies_summary()
        scored = []

        for idea in ideas:
            user_prompt = ANALYST_USER_PROMPT.format(
                title=idea.title,
                description=idea.description,
                source=idea.source,
                url=idea.source_url,
                published_date=idea.published_date or "unknown",
                registered_strategies=registered,
            )

            try:
                result = self.llm.analyze(
                    prompt=user_prompt,
                    context={"system_prompt": ANALYST_SYSTEM_PROMPT}
                )
            except Exception:
                result = {
                    "feasibility_score": 0,
                    "academic_rigor": 0,
                    "backtestability": 0,
                    "compatibility": 0,
                    "novelty": 0,
                    "implementation_plan": "",
                    "suggested_factors": [],
                    "suggested_params": {},
                    "risk_assessment": "LLM analysis failed",
                }

            if result.get("feasibility_score", 0) >= min_score:
                scored.append(ScoredIdea(
                    id=str(uuid.uuid4()),
                    raw_idea=idea,
                    feasibility_score=float(result.get("feasibility_score", 0)),
                    academic_rigor=float(result.get("academic_rigor", 0)),
                    backtestability=float(result.get("backtestability", 0)),
                    compatibility=float(result.get("compatibility", 0)),
                    novelty=float(result.get("novelty", 0)),
                    implementation_plan=result.get("implementation_plan", ""),
                    suggested_factors=result.get("suggested_factors", []),
                    suggested_params=result.get("suggested_params", {}),
                    risk_assessment=result.get("risk_assessment", ""),
                    scored_at=datetime.now(),
                ))

        return scored
