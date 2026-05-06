from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple


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
    economic_rationale_score: float = 0.0
    factor_uniqueness_score: float = 0.0
    data_availability_score: float = 0.0
    implementation_score: float = 0.0
    overfit_risk_score: float = 0.0
    cost_capacity_score: float = 0.0
    regime_robustness_score: float = 0.0
    risk_flags: List[str] = field(default_factory=list)
    rejection_reason: str = ""


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    strategy_type: str
    signal_formula_key: str
    universe: Tuple[str, ...]
    horizon_days: int
    lookback_days: int
    execution_lag_days: int
    required_fields: Tuple[str, ...]
    status: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe", tuple(self.universe))
        object.__setattr__(self, "required_fields", tuple(self.required_fields))


@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    status: str
    rank_ic: float
    rank_ic_ir: float
    ic_decay: Tuple[float, ...]
    fdr_adjusted_p: float
    fdr_significant: bool
    ff_alpha_monthly: float
    ff_alpha_tstat: float
    ff_r2: float
    long_short_spread: float
    hit_rate: float
    data_start: str
    data_end: str
    n_observations: int
    errors: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ic_decay", tuple(self.ic_decay))
        object.__setattr__(self, "errors", tuple(self.errors))


@dataclass(frozen=True)
class PurgedWalkForwardResult:
    splits: Tuple[Mapping[str, Any], ...]
    aggregate_oos_sharpe: float
    worst_oos_sharpe: float
    deflated_sharpe_ratio: Optional[float]
    sharpe_degradation: float
    pct_profitable_splits: float
    is_viable: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "splits",
            tuple(MappingProxyType(dict(split)) for split in self.splits),
        )


@dataclass(frozen=True)
class RegimeLabel:
    regime: str
    start_date: str
    end_date: str
    confidence: float


@dataclass(frozen=True)
class CostEstimate:
    commission: float
    spread_cost: float
    market_impact: float
    total_bps: float
    capacity_adv_pct: float
    capacity_ok: bool


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    strategy_id: str
    config_hash: str
    data_hash: str
    code_version: str
    status: str
    started_at: str
    completed_at: Optional[str] = None


@dataclass(frozen=True)
class EnsembleResult:
    strategy_ids: Tuple[str, ...]
    weights: Tuple[float, ...]
    portfolio_sharpe: float
    portfolio_max_dd: float
    portfolio_cagr: float
    diversification_ratio: float
    mean_correlation: float
    effective_n: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_ids", tuple(self.strategy_ids))
        object.__setattr__(self, "weights", tuple(self.weights))


@dataclass
class ResearchLogEntry:
    phase: str
    title: str
    source: str
    source_url: str
    verdict: str
    reason: str
    scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "title": self.title,
            "source": self.source,
            "source_url": self.source_url,
            "verdict": self.verdict,
            "reason": self.reason,
            "scores": self.scores,
        }


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
    llm_provider: str = "minimax"
    llm_model: str = "MiniMax-M2.7"
    llm_api_key: Optional[str] = None
    llm_temperature: float = 0.3
    llm_base_url: Optional[str] = None
    llm_group_id: Optional[str] = None
    research_dir: Optional[str] = None
    validation_enabled: bool = True
    validation_min_obs: int = 252
    validation_config: dict = field(default_factory=dict)
    scout_config: dict = field(default_factory=dict)
    rigor_enabled: bool = True
    rigor_config: dict = field(default_factory=dict)
    tracking_enabled: bool = True
    tracking_db_path: str = ""
    ensemble_enabled: bool = True
    ensemble_config: dict = field(default_factory=dict)


@dataclass
class ResearchResult:
    discovered: int = 0
    evaluated: int = 0
    integrated: int = 0
    backtested: int = 0
    promoted_auto: int = 0
    rejected: int = 0
    specified: int = 0
    needs_manual_spec: int = 0
    validated: int = 0
    validated_passed: int = 0
    walkforward_passed: int = 0
    ensemble_built: bool = False
    run_id: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    log: List[ResearchLogEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "evaluated": self.evaluated,
            "integrated": self.integrated,
            "backtested": self.backtested,
            "promoted_auto": self.promoted_auto,
            "rejected": self.rejected,
            "specified": self.specified,
            "needs_manual_spec": self.needs_manual_spec,
            "validated": self.validated,
            "validated_passed": self.validated_passed,
            "walkforward_passed": self.walkforward_passed,
            "ensemble_built": self.ensemble_built,
            "run_id": self.run_id,
            "errors": self.errors,
            "log": [e.to_dict() for e in self.log],
        }
