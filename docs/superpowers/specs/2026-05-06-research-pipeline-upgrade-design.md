# Research Pipeline Upgrade — Design Spec

**Date**: 2026-05-06
**Author**: Quantitative Research
**Status**: Draft — pending user review
**Scope**: Design spec only. No implementation.

## Problem Statement

The current research pipeline (`quant/features/research/`) has a 4-phase automated workflow (Scout → Evaluate → Integrate → Backtest) that models the shape of a professional quant research pipeline, but lacks the statistical depth, source breadth, backtest rigor, reproducibility, and portfolio-level thinking of a professional quant firm.

## Target State

Upgrade the pipeline from 4 phases to 6 phases across 5 new capability modules, while preserving the existing hexagonal/DI architecture, solo/local operating model, and DuckDB storage stack.

---

## Module Summary

| # | Module | Directory | What It Adds |
|---|--------|-----------|--------------|
| 1 | Statistical Factor Validation | `features/research/validation/` | Signal IC computation, Fama-French decomposition, FDR-adjusted t-stat, pre-computed factor zoo database |
| 2 | Multi-Source Discovery | `features/research/discovery/` | NBER, SSRN, quant blog adapters; unified source hub with rate limiting and dedup |
| 3 | Backtest Rigor | `features/research/rigor/` | Purged walk-forward CV, regime detection, dynamic cost/capacity model, deflated Sharpe |
| 4 | Experiment Tracking | `features/research/tracking/` | DuckDB experiment store, run recording with config/data/code hashes, comparison queries |
| 5 | Portfolio Construction | `features/research/portfolio/` | Strategy correlation matrix, ensemble weighting, diversification metrics |

---

## Upgraded Pipeline Flow (6 Phases)

```
Phase 1: Scout           Phase 2: Validate          Phase 3: Evaluate
 discovery/source_hub     validation/                evaluator.py
                          factor_validator
      │                        │                         │
      │ Multi-source           │ Statistical:            │ Combined score:
      │ 4+ adapters            │ Raw IC, Risk-Adj IC,    │ semantic (LLM)
      │ dedup + rate-limit     │ FF alpha, FDR t-stat    │ + statistical
      │                        │                         │ = composite gate
      ▼                        ▼                         ▼
┌────────────────────────────────────────────────────────────────┐
│                     Phase 4: Backtest (rigor/)                  │
│  Purged walk-forward CV + regime breakdown + dynamic cost       │
│  → Deflated Sharpe by regime, capacity-aware metrics            │
└────────────────────────────────────────────────────────────────┘
      │                        │                         │
      ▼                        ▼                         ▼
 Phase 5: Combine          Phase 6: Track
 portfolio/               tracking/
 ensemble.py              experiment_store
```

### Gate Changes

| Gate | What | Threshold | Source |
|------|------|-----------|--------|
| Gate 1 (existing) | LLM suitability score | ≥ 6.0 | `evaluator.py` |
| Gate 2 (NEW) | Statistical validation | Rank IC > 0.02, FDR-adjusted t > 2.0 | `validation/factor_validator.py` |
| Gate 3 (existing) | Simple backtest Sharpe | ≥ 0.5 | Injected `backtest_fn` |
| Gate 4 (NEW) | Purged walk-forward deflated Sharpe | ≥ 0.3 | `rigor/backtest_hub.py` |

A strategy must pass ALL gates to remain as a candidate. Failure at any gate logs the reason and rejects.

---

## Architecture & Boundaries

### Directory Tree (Target)

```
quant/features/research/
├── __init__.py
├── AGENTS.md
├── models.py              ← extended data models
├── research_engine.py     ← upgraded to 6-phase
├── pool.py                ← upgraded lifecycle
├── evaluator.py           ← upgraded (LLM + statistical composite)
├── integrator.py          ← upgraded code gen
├── scheduler.py           ← unchanged
│
├── validation/            ← Module 1 (NEW)
│   ├── __init__.py
│   ├── factor_validator.py
│   ├── factor_database.py
│   └── ff_decomposition.py
│
├── discovery/             ← Module 2 (NEW)
│   ├── __init__.py
│   ├── source_hub.py
│   └── adapters/
│       ├── __init__.py
│       ├── base.py
│       ├── arxiv_adapter.py     ← moved from scout.py
│       ├── ssrn_adapter.py      ← upgraded from stub
│       ├── nber_adapter.py
│       └── blog_adapter.py      ← Quantocracy, Alpha Architect
│
├── rigor/                 ← Module 3 (NEW)
│   ├── __init__.py
│   ├── backtest_hub.py
│   ├── purged_cv.py
│   ├── regime_detector.py
│   └── cost_model.py
│
├── tracking/              ← Module 4 (NEW)
│   ├── __init__.py
│   ├── experiment_store.py
│   ├── run_recorder.py
│   └── comparison.py
│
├── portfolio/             ← Module 5 (NEW)
│   ├── __init__.py
│   ├── correlation_matrix.py
│   ├── optimizer.py
│   └── ensemble.py
│
└── config/
    └── research.yaml      ← extended
```

### Dependency Rules

All modules follow existing hexagonal rules:
- Depend only on `domain/` ports and `features/research/models.py`
- External I/O (DuckDB, HTTP, filesystem) goes through injected ports or adapters
- No feature-to-feature imports — cross-feature wiring remains at the composition root (`api/research_bp.py`, `scripts/run_research.py`)
- `backtest_fn` remains injected — `rigor/backtest_hub.py` accepts it as a callable

### Storage

```
infrastructure/var/research/
├── research_state.duckdb     ← replaces research_state.json
│   Tables: candidates, seen_hashes, runs, metrics, factor_zoo
├── experiments/
│   └── {run_id}/
│       ├── metadata.json
│       ├── equity_curve.parquet
│       └── trades.parquet
├── factor_zoo/
│   └── chen_zimmermann.parquet
└── *.md                      ← keep human-readable summaries
```

DuckDB schema for `research_state.duckdb`:

```sql
-- Candidates and lifecycle
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    status TEXT DEFAULT 'candidate',  -- candidate, validating, paused, active, rejected, retired
    priority INTEGER DEFAULT 999,
    source TEXT,
    source_url TEXT,
    research_meta JSON,
    created_at TEXT,
    updated_at TEXT
);

-- Dedup hashes
CREATE TABLE seen_hashes (
    hash TEXT PRIMARY KEY,
    title TEXT,
    source TEXT,
    source_url TEXT,
    seen_at TEXT
);

-- Experiment runs
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    config_hash TEXT,
    data_hash TEXT,
    code_version TEXT,
    status TEXT,  -- running, completed, failed
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (strategy_id) REFERENCES candidates(id)
);

-- Backtest metrics per run
CREATE TABLE metrics (
    run_id TEXT,
    strategy_id TEXT,
    metric_name TEXT,   -- sharpe, sortino, max_dd, cagr, win_rate, etc.
    metric_value DOUBLE,
    window_type TEXT,   -- full, train, test, bull, bear, sideways
    window_label TEXT,  -- e.g. "2020-01-01_2020-06-30"
    PRIMARY KEY (run_id, strategy_id, metric_name, window_type, window_label),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

---

## Module 1: Statistical Factor Validation

### Purpose
Replace (or augment) the purely-semantic LLM evaluation with empirical signal testing. Before generating code, validate whether the paper-described signal actually has predictive power on available data.

### Components

#### `factor_validator.py`
Core validation class. Takes a `RawStrategy` (paper description), extracts the described signal, computes it against historical data, and returns a `ValidationReport`.

```python
@dataclass
class ValidationReport:
    strategy_id: str
    rank_ic: float              # Spearman rank correlation
    rank_ic_ir: float           # Information ratio of IC (mean/std)
    ic_decay: List[float]       # IC at horizon 1, 2, 5, 10, 21 days
    ff_alpha_monthly: float     # Fama-French 5-factor monthly alpha
    ff_alpha_tstat: float       # t-statistic of alpha
    ff_r2: float                # R-squared of FF regression
    fdr_adjusted_t: float       # Benjamini-Hochberg adjusted t-stat
    fdr_significant: bool       # Significant after multiplicity adjustment
    long_short_spread: float    # Annualized top-minus-bottom quintile
    hit_rate: float             # % of days IC > 0
    data_start: str
    data_end: str
    n_observations: int
    errors: List[str] = field(default_factory=list)
```

**Signal extraction**: For MVP, signals are manually specified via config (a mapping of strategy name → signal formula). The validator computes the signal column and runs the battery of tests. Full NLP-to-formula extraction is deferred to a future version.

**Validation flow**:
1. Look up signal formula for this strategy type from config
2. Load OHLCV data from DuckDB for default symbols × default date range
3. Compute signal values
4. Compute forward returns (1d, 5d, 21d)
5. Rank IC (Spearman) at each horizon
6. Fama-French 5-factor regression (requires FF factor data)
7. FDR adjustment (Benjamini-Hochberg across all strategies tested in this run)
8. Return ValidationReport

#### `factor_database.py`
Pre-computed factor benchmark library. Stores factor return series and metadata.

- Downloads Chen-Zimmermann open-source factor data (200+ documented factors) as baseline
- Provides `get_factor_returns(factor_name, start, end)` lookup
- Provides `list_factors()` for discovery
- Caches locally as Parquet in `var/research/factor_zoo/`

#### `ff_decomposition.py`
Fama-French 5-factor regression utility:
- Downloads FF5 daily factors from Kenneth French Data Library
- Runs `returns ~ Mkt-RF + SMB + HML + RMW + CMA` regression
- Returns alpha, t-stat, R², factor loadings
- Caches factor data locally

### Integration Point
`evaluator.py` is upgraded to produce a composite score:
- `composite_score = 0.4 * normalized_suitability + 0.6 * normalized_statistical`
- The statistical score is: `(rank_ic / 0.05) * 0.4 + (fdr_significant ? 0.3 : 0) + (ff_alpha_tstat / 3.0) * 0.3` (capped at 1.0)

### Configuration (research.yaml)
```yaml
validation:
  enabled: true
  default_lookback_years: 5
  min_observations: 252
  signal_formulas:
    momentum: "close.shift(1) / close.shift(lookback+1) - 1"
    mean_reversion: "-(close / close.rolling(lookback).mean() - 1)"
    # ... extensible per strategy type
  factor_db:
    chen_zimmermann: true
    auto_update: false
  ff_factors:
    source: "kenneth_french"  # or local parquet path
    cache_path: "var/research/factor_zoo/ff5_daily.parquet"
```

---

## Module 2: Multi-Source Discovery

### Purpose
Expand discovery beyond a single arXiv category to cover the full quant research ecosystem: academic papers (multiple sources), working papers, and practitioner content.

### Components

#### `discovery/adapters/base.py`
```python
class SourceAdapter(ABC):
    @abstractmethod
    def search(self, max_results: int = 10) -> List[RawStrategy]:
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        ...
```

#### Adapter Implementations

| Adapter | Source | Implementation |
|---------|--------|---------------|
| `ArxivAdapter` | arXiv (moved from scout.py) | Multi-category: q-fin.TR, q-fin.PM, q-fin.ST, q-fin.CP. Multi-query: 6 keyword searches + category browse |
| `SSRNAdapter` | SSRN | HTTP scrape SSRN search results (upgraded from stub). Search queries: "trading strategy", "equity factor", "alpha", "anomaly" |
| `NBERAdapter` | NBER Working Papers | RSS feed from `https://www.nber.org/rss/new.xml`, filter by category=AP (Asset Pricing) or keyword match |
| `BlogAdapter` | Quantocracy, Alpha Architect | RSS feed parsing. Filter for strategy-description posts. |

#### `discovery/source_hub.py`
Unified scout orchestrator — replaces the current `StrategyScout`:

```python
class SourceHub:
    def __init__(self, adapters: Dict[str, SourceAdapter], rate_limiter: RateLimiter):
        ...

    def search(self, sources: List[str] = None, max_results: int = 10) -> List[RawStrategy]:
        # Parallel dispatch across adapters via ThreadPoolExecutor
        # Per-source rate limiting (arXiv: 1 req/5s, others: 1 req/2s)
        # Cross-source dedup (MD5 hash)
        ...
```

#### `RateLimiter` (new utility)
```python
@dataclass
class RateLimiter:
    min_interval_seconds: float = 3.0
    jitter_seconds: float = 2.0
    # thread-safe, per-source rate tracking
```

### Dedup Strategy
Cross-source dedup via normalized title + description hash (existing `hash_strategy`). When a paper appears on both arXiv and SSRN, the first-seen source is kept and the duplicate is logged.

### Configuration (research.yaml)
```yaml
scout:
  sources:
    arxiv:
      enabled: true
      categories: [q-fin.TR, q-fin.PM, q-fin.ST]
      max_results_per_query: 5
      queries:
        - "ti:momentum AND ti:strategy"
        - "ti:mean reversion trading"
        - "ti:factor AND ti:equity AND ti:alpha"
        - "ti:volatility AND ti:trading"
        - "ti:trend following"
        - "ti:trading strategy daily"
    ssrn:
      enabled: true
      max_results: 10
    nber:
      enabled: true
      max_results: 10
    blogs:
      enabled: false  # off by default
      sources: [quantocracy, alpha_architect]
      max_results: 5

  rate_limit:
    arxiv_seconds: 5
    default_seconds: 2
    jitter_seconds: 2
```

---

## Module 3: Backtest Rigor

### Purpose
Upgrade backtests from a single train/test split with fixed costs to purged walk-forward with regime decomposition, dynamic cost modeling, and deflated performance metrics.

### Components

#### `rigor/purged_cv.py`
Implements purged walk-forward cross-validation per Marcos Lopez de Prado ("Advances in Financial Machine Learning"):

```python
@dataclass
class PurgedWalkForwardConfig:
    train_window_days: int = 252      # 1 year train
    test_window_days: int = 63        # 3 months test
    step_days: int = 63               # quarterly step
    purge_days: int = 5               # purge period between train/test
    embargo_days: int = 0             # embargo after test before next train
    min_train_observations: int = 126
    n_splits: Optional[int] = None    # None = auto-compute from data range

@dataclass
class PurgedWalkForwardResult:
    splits: List[SplitMetrics]
    aggregate_sharpe: float
    deflated_sharpe: float            # min across splits
    sharpe_degradation: float         # (train - test) / train
    pct_profitable_splits: float
    is_viable: bool                   # deflated_sharpe >= threshold

@dataclass
class SplitMetrics:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_sharpe: float
    test_sharpe: float
    test_max_dd: float
    test_cagr: float
    test_win_rate: float
    n_trades: int
```

Key constraint: the purge period ensures no data leakage between train and test. The embargo period prevents overlapping test windows from leaking information.

#### `rigor/regime_detector.py`
Tags backtest periods with market regime labels for conditional performance breakdown:

```python
@dataclass
class RegimeLabel:
    regime: str         # "bull", "bear", "sideways", "crash", "recovery"
    start_date: str
    end_date: str
    confidence: float   # 0-1

class RegimeDetector:
    def detect(self, benchmark_prices: pd.Series) -> List[RegimeLabel]:
        # Uses SPY as benchmark
        # Bull: price > 200-day SMA by > 5% for > 21 days
        # Bear: price < 200-day SMA by > 5% for > 21 days
        # Sideways: within 5% band around 200-day SMA
        # Crash: drawdown > 20% from peak within 60 days
        # Recovery: 30%+ rally from crash low within 120 days
        ...

    def tag_date(self, date, labels) -> str:
        ...
```

Backtest result splits are then tagged and metrics are computed per-regime:
- `sharpe_bull`, `sharpe_bear`, `sharpe_sideways`, `sharpe_crash`
- `max_dd_bull`, `max_dd_bear`, etc.

#### `rigor/cost_model.py`
Replaces fixed 5bps slippage with dynamic cost estimation:

```python
@dataclass
class CostEstimate:
    commission: float           # per-market fixed commission
    spread_cost: float          # half-spread as fraction of price
    market_impact: float        # Almgren-Chriss simplified: sigma * sqrt(trade_size / daily_volume)
    total_bps: float            # sum of above in basis points
    capacity_adv_pct: float     # trade size as % of ADV

class DynamicCostModel:
    def estimate(self, symbol, quantity, price, daily_volume, volatility) -> CostEstimate:
        ...

    def capacity_check(self, symbol, quantity, daily_volume) -> bool:
        # Reject trades exceeding 5% of ADV
        return (quantity * price) / (daily_volume * price) <= 0.05
```

#### `rigor/backtest_hub.py`
Orchestrates the rigor upgrades. Accepts the existing `backtest_fn` (injected), wraps it with purged CV, regime detection, and dynamic costs.

```python
class RigorHub:
    def __init__(self, backtest_fn, purged_cv: PurgedWalkForward,
                 regime_detector: RegimeDetector, cost_model: DynamicCostModel):
        ...

    def run_rigorous_backtest(self, strategy_id, config, integrator, pool, result):
        # 1. Run purged walk-forward
        # 2. Tag each split window with regime
        # 3. Compute per-regime metrics
        # 4. Compute deflated Sharpe = min(test_sharpe across splits)
        # 5. Record all metrics to experiment store
        # 6. Gate: deflated_sharpe >= 0.3
        ...
```

### Configuration (research.yaml)
```yaml
rigor:
  enabled: true
  purged_walkforward:
    train_window_days: 252
    test_window_days: 63
    step_days: 63
    purge_days: 5
    embargo_days: 21
  regime_detection:
    benchmark: "SPY"
    sma_lookback: 200
  cost_model:
    spread_bps: 2           # default half-spread
    impact_model: "simple"  # simple = sqrt(T/V), ac = Almgren-Chriss
    max_adv_pct: 0.05
  deflated_sharpe_threshold: 0.3
```

---

## Module 4: Experiment Tracking

### Purpose
Replace in-memory/JSON logging with a durable, queryable experiment database. Every run is recorded with config hash, data hash, code version, and all metrics — enabling comparison, ranking, and reproducibility.

### Components

#### `tracking/experiment_store.py`
DuckDB-backed experiment database. Implements a new domain port `ExperimentStore` (or extends `ResearchStore`):

```python
class ExperimentStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def start_run(self, strategy_id: str, config: dict, data_hash: str) -> str:
        # Insert into runs table, return run_id (UUID7)
        ...

    def record_metrics(self, run_id: str, strategy_id: str, metrics: Dict[str, Any]):
        # Insert into metrics table, one row per metric × window
        ...

    def complete_run(self, run_id: str, status: str = "completed"):
        ...

    def get_run(self, run_id: str) -> dict:
        ...

    def list_runs(self, strategy_id: str = None, limit: int = 50) -> List[dict]:
        ...

    def compare_strategies(self, strategy_ids: List[str], metric: str = "sharpe") -> pd.DataFrame:
        ...

    def get_best_run(self, strategy_id: str, metric: str = "sharpe") -> dict:
        ...

    def get_metric_timeseries(self, strategy_id: str, metric: str) -> pd.Series:
        ...
```

#### `tracking/run_recorder.py`
Generates config hash, data hash, and tracks code version:

```python
class RunRecorder:
    @staticmethod
    def hash_config(config: dict) -> str:
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]

    @staticmethod
    def hash_data(data_df) -> str:
        # Hash of (symbols, start, end, row count, checksum of close prices)
        ...

    @staticmethod
    def get_code_version() -> str:
        # git rev-parse HEAD
        ...
```

#### `tracking/comparison.py`
Cross-strategy comparison and ranking:

```python
class StrategyComparator:
    def rank_by(self, metric: str = "sharpe", window: str = "full", n_top: int = 10) -> List[dict]:
        ...

    def correlation_heatmap(self, strategy_ids: List[str]) -> pd.DataFrame:
        # Pairwise correlation of strategy returns
        ...

    def efficiency_frontier(self, strategy_ids: List[str]) -> dict:
        # Sharpe vs max_dd plot data
        ...
```

### Integration
- `RigorHub` writes all backtest metrics to `ExperimentStore` after each run
- `ResearchEngine.run_full_pipeline()` records a run in `ExperimentStore`
- API exposes `/api/research/experiments/<strategy_id>` and `/api/research/compare`

### Configuration (research.yaml)
```yaml
tracking:
  enabled: true
  db_path: "infrastructure/var/research/research_state.duckdb"
  artifact_dir: "infrastructure/var/research/experiments"
  auto_save_equity_curve: true
  auto_save_trades: true
```

---

## Module 5: Strategy Portfolio Construction

### Purpose
Move beyond testing strategies in isolation. Build a portfolio view: pairwise correlations, ensemble weighting, and diversification metrics.

### Components

#### `portfolio/correlation_matrix.py`
Computes pairwise return correlations between active and candidate strategies:

```python
@dataclass
class CorrelationMatrix:
    strategy_ids: List[str]
    matrix: np.ndarray          # N×N correlation matrix
    labels: List[str]
    mean_correlation: float     # Average pairwise correlation
    effective_n: float          # Diversification ratio: (sum(σ_i) / σ_portfolio)²

class CorrelationBuilder:
    def __init__(self, experiment_store: ExperimentStore):
        ...

    def build(self, strategy_ids: List[str], start: str = None, end: str = None) -> CorrelationMatrix:
        # Load equity curves from experiment store
        # Compute daily returns
        # Compute pairwise Pearson correlation
        # Compute effective N
        ...
```

#### `portfolio/optimizer.py`
Simple portfolio construction methods — no quadratic optimization needed for MVP:

```python
class PortfolioOptimizer:
    @staticmethod
    def equal_risk_weights(correlation: CorrelationMatrix, vols: np.ndarray) -> np.ndarray:
        # 1/σ_i normalized to sum 1
        ...

    @staticmethod
    def max_sharpe_weights(returns: pd.DataFrame, max_weight: float = 0.25) -> np.ndarray:
        # Tangency portfolio (unconstrained mean-variance)
        ...

    @staticmethod
    def min_variance_weights(correlation: CorrelationMatrix, vols: np.ndarray) -> np.ndarray:
        # Global minimum variance
        ...
```

#### `portfolio/ensemble.py`
Strategy ensemble management:

```python
@dataclass
class EnsembleResult:
    strategy_ids: List[str]
    weights: np.ndarray
    portfolio_return: float
    portfolio_vol: float
    portfolio_sharpe: float
    portfolio_max_dd: float
    diversification_ratio: float
    correlation_matrix: CorrelationMatrix

class StrategyEnsemble:
    def __init__(self, experiment_store: ExperimentStore):
        ...

    def build_ensemble(self, strategy_ids: List[str], method: str = "equal_risk") -> EnsembleResult:
        ...

    def marginal_contribution(self, strategy_id: str, ensemble: List[str]) -> float:
        # Sharpe improvement from adding this strategy
        ...

    def recommend_top_n(self, n: int = 5, method: str = "sharpe") -> List[str]:
        ...
```

### Integration
- Added as **Phase 5** in `ResearchEngine.run_full_pipeline()`, after backtests complete
- Runs only when `auto_backtest` is true and ≥ 2 strategies passed backtest
- Outputs: ensemble weights CSV, correlation heatmap data (JSON for frontend)
- Frontend gets a new endpoint: `/api/research/ensemble`

### Configuration (research.yaml)
```yaml
portfolio:
  enabled: true
  min_strategies_for_ensemble: 2
  default_method: "equal_risk"    # equal_risk, max_sharpe, min_variance
  rebalance_frequency_days: 21    # monthly rebalance
  max_weight_per_strategy: 0.25
  benchmark: "SPY"
```

---

## Data Models (Extensions)

### New Models Added to `models.py`

```python
@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    rank_ic: float
    rank_ic_ir: float
    ic_decay: List[float]
    ff_alpha_monthly: float
    ff_alpha_tstat: float
    ff_r2: float
    fdr_adjusted_t: float
    fdr_significant: bool
    long_short_spread: float
    hit_rate: float
    data_start: str
    data_end: str
    n_observations: int
    errors: List[str] = field(default_factory=list)

@dataclass
class PurgedWalkForwardResult:
    splits: List[dict]
    aggregate_sharpe: float
    deflated_sharpe: float
    sharpe_degradation: float
    pct_profitable_splits: float
    is_viable: bool

@dataclass
class RegimeLabel:
    regime: str         # bull, bear, sideways, crash, recovery
    start_date: str
    end_date: str
    confidence: float

@dataclass
class CostEstimate:
    commission: float
    spread_cost: float
    market_impact: float
    total_bps: float
    capacity_adv_pct: float

@dataclass
class RunMetadata:
    run_id: str
    strategy_id: str
    config_hash: str
    data_hash: str
    code_version: str
    status: str
    started_at: str
    completed_at: Optional[str] = None

@dataclass
class EnsembleResult:
    strategy_ids: List[str]
    weights: List[float]
    portfolio_sharpe: float
    portfolio_max_dd: float
    portfolio_cagr: float
    diversification_ratio: float
    mean_correlation: float
    effective_n: float
```

### Existing Models Extended

`ResearchConfig` adds new fields:
```python
validation_enabled: bool = True
validation_min_obs: int = 252
scout_sources: dict = field(default_factory=dict)
rigor_enabled: bool = True
rigor_purge_days: int = 5
rigor_embargo_days: int = 21
rigor_deflated_sharpe_threshold: float = 0.3
tracking_enabled: bool = True
tracking_db_path: str = ""
portfolio_enabled: bool = True
portfolio_method: str = "equal_risk"
```

`ResearchResult` adds new fields:
```python
validated: int = 0
validated_passed: int = 0
deflated_passed: int = 0
ensemble_built: bool = False
```

---

## API Endpoint Changes

### New Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/research/experiments/<strategy_id>` | List all runs for a strategy |
| GET | `/api/research/experiments/<strategy_id>/<run_id>` | Get run details + metrics |
| GET | `/api/research/compare?ids=...&metric=sharpe` | Compare strategies side-by-side |
| GET | `/api/research/ensemble` | Get ensemble weights + metrics |
| POST | `/api/research/ensemble/rebuild` | Rebuild ensemble with current candidates |

### Modified Endpoints
| Method | Path | Change |
|--------|------|--------|
| POST | `/api/research/run` | Returns `validated`, `deflated_passed` counts |
| GET | `/api/research/schedule` | Returns new config fields |
| POST | `/api/research/schedule` | Accepts new config fields |

---

## Migration Plan

### Phase 1: Infrastructure (Module 4)
- Set up DuckDB experiment store + schema
- Migrate `FileResearchStore` JSON data to DuckDB tables
- No behavior change — just storage backend swap

### Phase 2: Statistical Validation (Module 1)
- Implement `factor_validator.py` + `ff_decomposition.py`
- Add Gate 2 to pipeline
- Keep existing LLM evaluator as fallback (composite scoring)

### Phase 3: Discovery Expansion (Module 2)
- Build NBER + SSRN adapters
- Add `SourceHub` orchestrator
- Wire into upgraded `scout.py`

### Phase 4: Backtest Rigor (Module 3)
- Implement `purged_cv.py` + `regime_detector.py` + `cost_model.py`
- Add `RigorHub` wrapping existing backtest
- Add Gate 4 (deflated Sharpe)

### Phase 5: Portfolio Construction (Module 5)
- Implement correlation + ensemble
- Add Phase 5 to pipeline
- Expose ensemble endpoint

Each phase is independently shippable and testable. Order prioritizes the highest-ROI improvements first (better evaluation → better discovery → better backtesting → portfolio view).

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Fama-French factor data unavailable (Kenneth French site) | Cache locally on first download; fallback to simple CAPM regression |
| SSRN blocks scraping | Use polite delays (3-5s), respect robots.txt, notify user if blocked |
| Purged CV can't find enough data for many splits | Require minimum 5 years of data; gracefully degrade to fewer splits |
| DuckDB write conflicts (multiple processes) | Single-process design for solo researcher; use WAL mode if needed |
| Heuristic evaluator too generous (false positives) | Statistical Gate 2 and Gate 4 provide hard empirical filters |
| All 5 modules increase config complexity | Sensible defaults for all new fields; all modules individually toggleable |
