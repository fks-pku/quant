from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawStrategy:
    title: str
    description: str
    source: str
    source_url: str
    authors: Optional[str] = None
    published_date: Optional[str] = None


@dataclass
class EvaluationReport:
    suitability_score: float
    complexity_score: float
    data_requirement: str
    daily_adaptable: bool
    estimated_edge: float
    recommended_symbols: List[str]
    strategy_type: str
    summary: str


@dataclass
class ResearchConfig:
    auto_run: bool = False
    interval_days: int = 7
    sources: List[str] = field(default_factory=lambda: ["arxiv", "ssrn"])
    max_results_per_source: int = 10
    evaluation_threshold: float = 6.0
    backtest_sharpe_threshold: float = 0.5
    auto_backtest: bool = True
    default_backtest_start: str = "2020-01-01"
    default_backtest_end: str = "2024-12-31"
    default_symbols: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"])


@dataclass
class ResearchResult:
    discovered: int = 0
    evaluated: int = 0
    integrated: int = 0
    backtested: int = 0
    promoted_auto: int = 0
    rejected: int = 0
    errors: List[str] = field(default_factory=list)
