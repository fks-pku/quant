from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class RawIdea:
    id: str
    source: str
    source_url: str
    title: str
    description: str
    published_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=datetime.now)


@dataclass
class ScoredIdea:
    id: str
    raw_idea: RawIdea
    feasibility_score: float
    academic_rigor: float
    backtestability: float
    compatibility: float
    novelty: float
    implementation_plan: str
    suggested_factors: List[str] = field(default_factory=list)
    suggested_params: Dict[str, Any] = field(default_factory=dict)
    risk_assessment: str = ""
    scored_at: datetime = field(default_factory=datetime.now)


@dataclass
class StrategyCandidate:
    id: str
    scored_idea: ScoredIdea
    strategy_name: str
    code_path: str
    config_path: str
    registered: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ResearchReport:
    id: str
    candidate: StrategyCandidate
    backtest_metrics: Dict[str, Any]
    walkforward_result: Optional[Dict[str, Any]] = None
    llm_analysis: str = ""
    recommendation: str = "watchlist"
    recommendation_reasoning: str = ""
    comparison: Dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ResearchTask:
    id: str
    status: str
    topic: Optional[str] = None
    max_ideas: int = 5
    ideas: List[RawIdea] = field(default_factory=list)
    scored_ideas: List[ScoredIdea] = field(default_factory=list)
    candidates: List[StrategyCandidate] = field(default_factory=list)
    reports: List[ResearchReport] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error_log: List[str] = field(default_factory=list)
