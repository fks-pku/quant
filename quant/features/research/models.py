from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from quant.domain.models.research_source_catalog import default_research_source_names


DEFAULT_A_SHARE_SYMBOLS = ["000300", "000905", "600519", "000001", "510300"]
DEFAULT_RESEARCH_INITIAL_CASH = 10_000.0


@dataclass
class RawStrategy:
    title: str
    description: str
    source: str
    source_url: str
    authors: Optional[str] = None
    published_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryQualityReport:
    score: float
    source_quality_score: float
    recency_score: float
    provenance_score: float
    detail_score: float
    implementability_score: float
    daily_data_score: float
    novelty_score: float
    source_type: str
    matched_terms: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "source_quality_score": self.source_quality_score,
            "recency_score": self.recency_score,
            "provenance_score": self.provenance_score,
            "detail_score": self.detail_score,
            "implementability_score": self.implementability_score,
            "daily_data_score": self.daily_data_score,
            "novelty_score": self.novelty_score,
            "source_type": self.source_type,
            "matched_terms": list(self.matched_terms),
            "risk_flags": list(self.risk_flags),
            "warnings": list(self.warnings),
        }


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
    admission_score: float = 0.0
    signal_quality_score: float = 0.0
    research_confidence_score: float = 0.0
    data_risk_score: float = 0.0
    bias_risk_score: float = 0.0
    required_data_fields: List[str] = field(default_factory=list)
    validation_tests: List[str] = field(default_factory=list)
    evidence_notes: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    risk_flags: List[str] = field(default_factory=list)
    rejection_reason: str = ""


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
    sources: List[str] = field(default_factory=default_research_source_names)
    max_results_per_source: int = 10
    evaluation_threshold: float = 0.0
    backtest_sharpe_threshold: float = 0.5
    auto_backtest: bool = True
    default_backtest_start: str = "2016-01-01"
    default_backtest_end: str = "2026-05-31"
    default_initial_cash: float = DEFAULT_RESEARCH_INITIAL_CASH
    default_symbols: List[str] = field(default_factory=lambda: list(DEFAULT_A_SHARE_SYMBOLS))
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
    evaluation_config: dict = field(default_factory=dict)
    pit_enabled: bool = False
    pit_universe_snapshot_dir: str = "quant/infrastructure/var/research/universe_snapshots"
    scout_config: dict = field(default_factory=dict)
    rigor_enabled: bool = True
    rigor_config: dict = field(default_factory=dict)
    production_gate_config: dict = field(default_factory=dict)
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
    full_report_allowed: bool = True
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
            "full_report_allowed": self.full_report_allowed,
            "run_id": self.run_id,
            "errors": self.errors,
            "log": [e.to_dict() for e in self.log],
        }


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    strategy_type: str
    signal_formula_key: str
    universe: List[str]
    horizon_days: int
    lookback_days: int
    execution_lag_days: int
    required_fields: List[str]
    status: str
    reason: str = ""


@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    status: str
    rank_ic: float
    rank_ic_ir: float
    ic_decay: List[Tuple[int, float]]
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
    rank_ic_tstat: float = 0.0
    rank_ic_p_value: float = 1.0
    fama_macbeth_tstat: float = 0.0
    portfolio_diagnostics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    universe_size: int = 0
    universe_sample: List[str] = field(default_factory=list)
    universe_source: str = ""
    data_rows: int = 0
    data_symbol_count: int = 0
    factor_decomposition: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PurgedWalkForwardResult:
    splits: List[Dict[str, Any]]
    aggregate_oos_sharpe: float
    worst_oos_sharpe: float
    deflated_sharpe_ratio: Optional[float]
    sharpe_degradation: float
    pct_profitable_splits: float
    is_viable: bool
    capacity_ok: bool = False
    regime_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)
    bull_only_warning: bool = False
    evaluated_splits: int = 0
    no_trade_splits: int = 0
    total_splits: int = 0


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
    strategy_ids: List[str]
    weights: List[float]
    portfolio_sharpe: float
    portfolio_max_dd: float
    portfolio_cagr: float
    diversification_ratio: float
    mean_correlation: float
    effective_n: float
