# Research Pipeline Upgrade - Design Spec

**Date**: 2026-05-06
**Author**: Quantitative Research
**Status**: Revised - ready for implementation planning
**Scope**: Architecture-safe design for upgrading `quant/features/research/`. No implementation in this spec.

## Problem Statement

The current research pipeline has a 4-phase workflow:

1. Scout
2. Evaluate
3. Integrate
4. Backtest

It captures the outline of a systematic research process, but it is still too light for professional strategy selection. The main gaps are:

- Source coverage is narrow.
- LLM evaluation is not enough to validate empirical edge.
- Strategy descriptions are integrated before they are converted into explicit testable specs.
- Backtests are single-window and too easy to overfit.
- Research runs are not reproducible or easily comparable.
- Candidate strategies are evaluated in isolation instead of as a portfolio of edges.

## Target State

Upgrade research from a simple automation pipeline into a staged, reproducible research system:

1. Discover more candidate ideas.
2. Convert text into explicit `StrategySpec` objects before empirical testing.
3. Validate supported specs statistically before code generation.
4. Run walk-forward backtests with leakage controls and realistic costs.
5. Track every run with config, data, code version, metrics, and artifacts.
6. Compare candidates at the strategy portfolio level.

The implementation must preserve the existing architecture:

- `domain/` remains dependency-free.
- `features/` depends on `domain/` ports and local feature models only.
- `infrastructure/` implements external I/O adapters and does not import `features/`.
- Cross-feature wiring stays in composition roots such as `quant/api/research_bp.py` and `quant/scripts/run_research.py`.

## Current Baseline

Relevant existing files:

| File | Current role |
|------|--------------|
| `quant/features/research/research_engine.py` | Orchestrates current Scout -> Evaluate -> Integrate -> Backtest flow |
| `quant/features/research/scout.py` | Contains `StrategyScout`, `ArxivAdapter`, and stub `SSRNAdapter` |
| `quant/features/research/evaluator.py` | LLM-driven semantic evaluation |
| `quant/features/research/integrator.py` | Generates strategy package and registers candidate |
| `quant/features/research/pool.py` | Candidate lifecycle management |
| `quant/features/research/models.py` | `RawStrategy`, `EvaluationReport`, `ResearchConfig`, `ResearchResult` |
| `quant/domain/ports/research_store.py` | Persistence port used by research feature |
| `quant/infrastructure/research/repository.py` | JSON-backed `FileResearchStore` implementation |
| `quant/api/research_bp.py` | Composition root for API, LLM adapter, research store, and backtest callback |

## Non-Negotiable Architecture Rules

### Feature layer

Allowed in `quant/features/research/`:

- Pipeline orchestration.
- Pure calculation code.
- Dataclasses and local feature models.
- Calls to injected domain ports.
- Calls to injected callables from the composition root.

Not allowed in `quant/features/research/`:

- Direct DuckDB connections.
- Direct filesystem persistence.
- Direct HTTP scraping or feed parsing.
- Imports from `quant.infrastructure.*`.
- Imports from sibling features such as `features/backtest`, `features/portfolio`, or `features/strategies`.

### Infrastructure layer

All external I/O belongs under `quant/infrastructure/research/`:

- DuckDB research store.
- DuckDB experiment store.
- File artifact store.
- HTTP/RSS source adapters.
- Fama-French and factor-zoo download/cache adapters.
- Optional JSON-to-DuckDB migration utilities.

Infrastructure adapters must implement domain ports and must not import `quant.features.*`.

### Domain ports

Domain ports use `Any`, `dict`, `list`, and scalar types. They must not expose `pd.DataFrame`, `pd.Series`, `np.ndarray`, DuckDB classes, requests responses, or feature dataclasses.

## Revised Module Summary

| # | Capability | Feature module | Infrastructure adapters |
|---|------------|----------------|--------------------------|
| 1 | Discovery hub | `features/research/discovery/` | `infrastructure/research/sources/` |
| 2 | Strategy specification and statistical validation | `features/research/validation/` | `infrastructure/research/factors/`, market data adapter |
| 3 | Backtest rigor | `features/research/rigor/` | Backtest callable supplied by API or script composition root |
| 4 | Experiment tracking | `features/research/tracking/` for hashes/comparison logic | `infrastructure/research/duckdb_experiment_store.py`, artifact store |
| 5 | Strategy ensemble | `features/research/ensemble/` | Reads run metrics and artifacts through `ExperimentStore` |

`features/research/portfolio/` is intentionally renamed to `features/research/ensemble/` to avoid confusion with the existing `quant/features/portfolio/` module.

## Revised Pipeline Flow

Tracking starts when a run begins and records outputs throughout the pipeline. It is not a hard gate.

```text
Run starts
  |
  |-- Phase 1: Scout
  |      SourceHub calls injected ResearchSource ports.
  |      Output: RawStrategy list and seen hashes.
  |
  |-- Phase 2: Semantic Evaluate
  |      StrategyEvaluator scores suitability and classifies strategy type.
  |      Gate 1: suitability >= evaluation_threshold and high-frequency ideas are daily-adaptable.
  |
  |-- Phase 3: Specify and Validate
  |      StrategySpecBuilder maps EvaluationReport + RawStrategy to an explicit StrategySpec.
  |      FactorValidator runs empirical tests only for supported specs.
  |      Gate 2: supported specs must pass minimum IC, sample size, and adjusted significance.
  |
  |-- Phase 4: Integrate
  |      StrategyIntegrator generates candidate code only after semantic and statistical gates.
  |
  |-- Phase 5: Rigorous Backtest
  |      RigorHub runs purged walk-forward using injected backtest runner.
  |      Gate 3: simple backtest threshold remains for backward compatibility.
  |      Gate 4: worst out-of-sample Sharpe and cost/capacity checks pass.
  |
  |-- Phase 6: Ensemble and Compare
         StrategyEnsemble compares active candidates and creates allocation recommendations.

Run completes
```

## Gate Policy

| Gate | Applies to | Default threshold | Failure behavior |
|------|------------|-------------------|------------------|
| Gate 1 | Semantic evaluation | `suitability_score >= 6.0` | Reject candidate |
| Gate 1b | Daily-bar suitability | high-frequency requires `daily_adaptable=true` | Reject candidate |
| Gate 2a | Spec support | `StrategySpec.status == "ready"` | Mark `needs_manual_spec`, do not reject by default |
| Gate 2b | Statistical validation | `abs(rank_ic) >= 0.02`, `fdr_significant=true`, `n_observations >= 252` | Reject supported but failed spec |
| Gate 3 | Legacy simple backtest | `sharpe >= 0.5` | Reject candidate |
| Gate 4a | Walk-forward robustness | `worst_oos_sharpe >= 0.3` and `pct_profitable_splits >= 0.5` | Reject candidate |
| Gate 4b | Cost and capacity | all trades within configured ADV cap | Reject candidate or mark capacity-limited |

Important naming rule:

- `worst_oos_sharpe` means the minimum test Sharpe across walk-forward splits.
- `deflated_sharpe_ratio` is reserved for a true Bailey/Lopez de Prado implementation that accounts for non-normality and multiple testing.
- The MVP must not call `min(test_sharpe)` a deflated Sharpe ratio.

## Target File Structure

### Domain ports

```text
quant/domain/ports/
  research_store.py              existing
  research_source.py             new
  research_market_data.py        new
  factor_data.py                 new
  experiment_store.py            new
  research_artifact_store.py     new
```

### Feature layer

```text
quant/features/research/
  __init__.py
  AGENTS.md
  models.py
  research_engine.py
  scout.py
  evaluator.py
  integrator.py
  pool.py
  scheduler.py
  config/
    research.yaml
  discovery/
    __init__.py
    source_hub.py
    dedup.py
  validation/
    __init__.py
    strategy_spec_builder.py
    signal_library.py
    factor_validator.py
    fdr.py
    ff_decomposition.py
  rigor/
    __init__.py
    purged_cv.py
    regime_detector.py
    cost_model.py
    backtest_hub.py
  tracking/
    __init__.py
    run_recorder.py
    comparison.py
  ensemble/
    __init__.py
    correlation_matrix.py
    optimizer.py
    ensemble.py
```

### Infrastructure layer

```text
quant/infrastructure/research/
  __init__.py
  repository.py                  existing FileResearchStore
  duckdb_research_store.py       new ResearchStore implementation
  duckdb_experiment_store.py     new ExperimentStore implementation
  file_artifact_store.py         new ResearchArtifactStore implementation
  migration.py                   new JSON -> DuckDB migration helpers
  sources/
    __init__.py
    arxiv_source.py
    ssrn_source.py
    nber_source.py
    blog_source.py
    rate_limiter.py
  factors/
    __init__.py
    ff_factor_store.py
    chen_zimmermann_store.py
  market_data/
    __init__.py
    duckdb_research_market_data.py
```

### Tests

```text
quant/tests/
  test_research_pipeline.py
  test_research_discovery.py
  test_research_validation.py
  test_research_rigor.py
  test_research_tracking.py
  test_research_ensemble.py
  test_research_duckdb_store.py
```

## Domain Port Contracts

### `ResearchSource`

```python
class ResearchSource(ABC):
    @abstractmethod
    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def source_name(self) -> str:
        raise NotImplementedError
```

Returned dictionaries must contain:

- `title`
- `description`
- `source`
- `source_url`

Optional keys:

- `authors`
- `published_date`
- `metadata`

`SourceHub` converts these dictionaries to `RawStrategy`.

### `ResearchMarketData`

```python
class ResearchMarketData(ABC):
    @abstractmethod
    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        raise NotImplementedError
```

The feature layer may convert the returned `Any` to pandas internally after receiving it. The port must not expose pandas in its type signature.

### `FactorData`

```python
class FactorData(ABC):
    @abstractmethod
    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def list_factors(self) -> List[Dict[str, Any]]:
        raise NotImplementedError
```

### `ExperimentStore`

```python
class ExperimentStore(ABC):
    @abstractmethod
    def start_run(self, strategy_id: str, metadata: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def record_metrics(self, run_id: str, metrics: Iterable[Dict[str, Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def complete_run(self, run_id: str, status: str, error: str = "") -> None:
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_runs(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_metrics(self, run_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError
```

### `ResearchArtifactStore`

```python
class ResearchArtifactStore(ABC):
    @abstractmethod
    def save_json(self, run_id: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save_table(self, run_id: str, name: str, table: Any) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def load_artifact(self, artifact_id: str) -> Any:
        raise NotImplementedError
```

`save_table` may persist Parquet in the infrastructure implementation, but the port type remains `Any`.

## Data Models

New models live in `quant/features/research/models.py` unless a later implementation decision moves shared transport models into `domain/models/`.

### `StrategySpec`

```python
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
```

Status values:

- `ready`
- `unsupported_type`
- `missing_formula`
- `missing_universe`
- `needs_manual_spec`

### `ValidationReport`

```python
@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    status: str
    rank_ic: float
    rank_ic_ir: float
    ic_decay: List[float]
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
    errors: List[str] = field(default_factory=list)
```

### `PurgedWalkForwardResult`

```python
@dataclass(frozen=True)
class PurgedWalkForwardResult:
    splits: List[Dict[str, Any]]
    aggregate_oos_sharpe: float
    worst_oos_sharpe: float
    deflated_sharpe_ratio: Optional[float]
    sharpe_degradation: float
    pct_profitable_splits: float
    is_viable: bool
```

### `RegimeLabel`

```python
@dataclass(frozen=True)
class RegimeLabel:
    regime: str
    start_date: str
    end_date: str
    confidence: float
```

### `CostEstimate`

```python
@dataclass(frozen=True)
class CostEstimate:
    commission: float
    spread_cost: float
    market_impact: float
    total_bps: float
    capacity_adv_pct: float
    capacity_ok: bool
```

### `RunMetadata`

```python
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
```

### `EnsembleResult`

```python
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
```

### `ResearchConfig` additions

Keep current flat fields for backward compatibility and add grouped config dictionaries for new modules:

```python
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
```

### `ResearchResult` additions

```python
specified: int = 0
needs_manual_spec: int = 0
validated: int = 0
validated_passed: int = 0
walkforward_passed: int = 0
ensemble_built: bool = False
run_id: Optional[str] = None
```

## Module Details

### Module 1: Discovery Hub

`SourceHub` replaces the current direct adapter registry inside `StrategyScout`, while `StrategyScout` remains as a backward-compatible wrapper.

Feature responsibilities:

- Fan out to injected `ResearchSource` ports.
- Normalize source dictionaries into `RawStrategy`.
- Deduplicate by normalized title and leading description text.
- Preserve source-specific metadata.
- Record skipped duplicates in run logs.

Infrastructure responsibilities:

- arXiv API calls.
- SSRN scraping or search API usage.
- NBER RSS parsing.
- Blog RSS parsing.
- Per-source rate limiting and jitter.

MVP source defaults:

- arXiv enabled.
- NBER enabled.
- SSRN enabled only if the adapter can operate politely and reliably.
- Blogs disabled by default.

### Module 2: Strategy Specification and Statistical Validation

`StrategySpecBuilder` creates an explicit spec before empirical testing.

Inputs:

- `RawStrategy`
- `EvaluationReport`
- validation config

Outputs:

- `StrategySpec(status="ready")` for supported formulas.
- `StrategySpec(status="needs_manual_spec")` when semantic classification is plausible but no formula mapping exists.
- `StrategySpec(status="unsupported_type")` for unsupported strategy families.

MVP formula keys:

| Formula key | Strategy type | Required fields |
|-------------|---------------|-----------------|
| `momentum_close_return` | `momentum` | `close` |
| `mean_reversion_close_to_ma` | `mean_reversion` | `close` |
| `volatility_breakout_atr` | `breakout` | `high`, `low`, `close` |

`FactorValidator` responsibilities:

- Load market data through `ResearchMarketData`.
- Compute signal values from approved formula keys only.
- Apply execution lag before forward return calculation.
- Compute Spearman rank IC by date.
- Compute IC decay for configured horizons.
- Compute FDR-adjusted significance across all validated specs in the run.
- Optionally run Fama-French decomposition when factor data is available.
- Return a `ValidationReport` with errors instead of raising for data insufficiency.

Important bias controls:

- Signals that use close prices must trade no earlier than the next bar.
- Forward returns must be shifted so future returns are labels only, never signal inputs.
- All tested specs are counted in FDR adjustment, including failures.

### Module 3: Backtest Rigor

`RigorHub` uses an injected backtest runner. The callback must be supplied by the API/script composition root.

Target callback shape:

```python
BacktestRunner = Callable[[str, Dict[str, Any]], Dict[str, Any]]
```

The request dictionary includes:

- `start`
- `end`
- `symbols`
- `initial_cash`
- `cost_config`
- `run_label`

The response dictionary includes:

- `metrics`
- `equity_curve`
- `trades`
- `errors`

`RigorHub` responsibilities:

- Build purged walk-forward splits.
- Call the injected runner once per train/test window.
- Compute aggregate out-of-sample metrics.
- Tag split windows with regimes.
- Apply dynamic cost and capacity checks when trade data is available.
- Reject or mark candidates based on configured thresholds.
- Record metrics through `ExperimentStore`.

`purged_cv.py` must guarantee:

- `train_end < test_start`
- `test_start - train_end >= purge_days`
- next split respects `embargo_days`
- no split is emitted when `min_train_observations` cannot be met

### Module 4: Experiment Tracking

Feature tracking code is pure:

- `RunRecorder.hash_config(config: dict) -> str`
- `RunRecorder.hash_data(data_summary: dict) -> str`
- `RunRecorder.get_code_version() -> str`
- `StrategyComparator` reads from `ExperimentStore` and returns dictionaries/lists.

DuckDB belongs to infrastructure:

- `DuckDBResearchStore` implements the existing `ResearchStore`.
- `DuckDBExperimentStore` implements new `ExperimentStore`.
- `FileArtifactStore` implements `ResearchArtifactStore`.

The JSON `FileResearchStore` remains available during migration and tests.

### Module 5: Strategy Ensemble

`features/research/ensemble/` builds research-time allocations only. It must not import or modify `quant/features/portfolio/`.

Inputs:

- Strategy IDs.
- Return series or equity curve artifacts loaded through `ExperimentStore` and `ResearchArtifactStore`.
- Metrics recorded by rigorous backtests.

Outputs:

- Correlation matrix data.
- Effective number of bets.
- Equal-risk weights.
- Optional max-Sharpe or min-variance weights if sample size is sufficient.
- Ensemble recommendation stored as a run artifact.

MVP methods:

- `equal_weight`
- `inverse_vol`
- `equal_risk`

`max_sharpe` should remain disabled by default because it is especially overfit-prone with few strategies and short history.

## Storage Design

Default root:

```text
quant/infrastructure/var/research/
  research_state.json
  research_state.duckdb
  experiments/
    {run_id}/
      metadata.json
      equity_curve.parquet
      trades.parquet
      ensemble.json
  factor_zoo/
    ff5_daily.parquet
    chen_zimmermann.parquet
  discovered_strategies.md
  strategy_evaluation.md
  runs/
```

`research_state.json` is not deleted automatically. Migration creates `research_state.duckdb` and leaves JSON as rollback input.

### DuckDB tables

```sql
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    status TEXT DEFAULT 'candidate',
    priority INTEGER DEFAULT 999,
    source TEXT,
    source_url TEXT,
    research_meta JSON,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_hashes (
    hash TEXT PRIMARY KEY,
    title TEXT,
    source TEXT,
    source_url TEXT,
    seen_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    config_hash TEXT,
    data_hash TEXT,
    code_version TEXT,
    status TEXT,
    started_at TEXT,
    completed_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT,
    strategy_id TEXT,
    metric_name TEXT,
    metric_value DOUBLE,
    window_type TEXT,
    window_label TEXT,
    metadata JSON,
    PRIMARY KEY (run_id, strategy_id, metric_name, window_type, window_label)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT,
    strategy_id TEXT,
    artifact_type TEXT,
    name TEXT,
    path TEXT,
    metadata JSON,
    created_at TEXT
);
```

## Configuration

`quant/features/research/config/research.yaml` keeps current keys and adds grouped sections.

```yaml
research:
  auto_run: false
  interval_days: 7
  sources:
    - arxiv
    - ssrn
  max_results_per_source: 10
  evaluation_threshold: 6.0
  backtest_sharpe_threshold: 0.5
  auto_backtest: true
  default_backtest_start: "2020-01-01"
  default_backtest_end: "2024-12-31"
  default_symbols:
    - AAPL
    - MSFT
    - GOOGL
    - SPY
    - QQQ
  validation_enabled: true
  rigor_enabled: true
  tracking_enabled: true
  ensemble_enabled: true

scout:
  arxiv:
    enabled: true
    categories:
      - q-fin.TR
      - q-fin.PM
      - q-fin.ST
    max_results_per_query: 5
  ssrn:
    enabled: false
    max_results: 10
  nber:
    enabled: true
    max_results: 10
  blogs:
    enabled: false
    max_results: 5
  rate_limit:
    arxiv_seconds: 5
    default_seconds: 2
    jitter_seconds: 2

validation:
  min_observations: 252
  default_lookback_years: 5
  execution_lag_days: 1
  formulas:
    momentum_close_return:
      strategy_type: momentum
      lookback_days: 20
      horizon_days: 5
    mean_reversion_close_to_ma:
      strategy_type: mean_reversion
      lookback_days: 20
      horizon_days: 5
    volatility_breakout_atr:
      strategy_type: breakout
      lookback_days: 20
      horizon_days: 5
  thresholds:
    min_abs_rank_ic: 0.02
    max_fdr_p: 0.05
    min_hit_rate: 0.52

rigor:
  purged_walkforward:
    train_window_days: 252
    test_window_days: 63
    step_days: 63
    purge_days: 5
    embargo_days: 21
    min_train_observations: 126
  regime_detection:
    benchmark: SPY
    sma_lookback: 200
  cost_model:
    spread_bps: 2
    max_adv_pct: 0.05
  thresholds:
    min_worst_oos_sharpe: 0.3
    min_profitable_splits_pct: 0.5

tracking:
  db_path: infrastructure/var/research/research_state.duckdb
  artifact_dir: infrastructure/var/research/experiments
  auto_save_equity_curve: true
  auto_save_trades: true

ensemble:
  min_strategies: 2
  default_method: equal_risk
  max_weight_per_strategy: 0.25
  benchmark: SPY
```

## API Changes

### New endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/research/experiments/<strategy_id>` | List runs for a strategy |
| GET | `/api/research/experiments/<strategy_id>/<run_id>` | Get run details and metrics |
| GET | `/api/research/compare?ids=strategy_a,strategy_b&metric=sharpe` | Compare strategy metrics |
| GET | `/api/research/ensemble` | Get latest ensemble recommendation |
| POST | `/api/research/ensemble/rebuild` | Rebuild ensemble from current candidates |

### Modified endpoints

| Method | Path | Change |
|--------|------|--------|
| POST | `/api/research/run` | Returns `run_id`, `specified`, `needs_manual_spec`, `validated`, `walkforward_passed`, `ensemble_built` |
| GET | `/api/research/schedule` | Returns new grouped config fields |
| POST | `/api/research/schedule` | Accepts new grouped config fields |

API endpoints must remain composition roots. They may import infrastructure adapters and sibling features for wiring, but `features/research/` must not.

## Migration Plan

### Phase 0: Contracts and compatibility tests

- Add new domain ports with `Any`/dict/list signatures.
- Add feature models.
- Keep existing tests passing.
- Add tests proving infrastructure adapters do not import `quant.features`.

### Phase 1: DuckDB research store

- Implement `DuckDBResearchStore` against existing `ResearchStore`.
- Add migration helper from `research_state.json`.
- Keep `FileResearchStore` as fallback.
- Wire store selection in API/script composition root.
- No pipeline behavior change.

### Phase 2: Experiment tracking

- Add `ExperimentStore` and `ResearchArtifactStore` ports.
- Implement `DuckDBExperimentStore` and `FileArtifactStore`.
- Add `RunRecorder`.
- Record run start/completion and basic metrics without changing candidate decisions.

### Phase 3: Discovery expansion

- Move HTTP source implementations to infrastructure.
- Add `SourceHub` in feature layer.
- Keep `StrategyScout` wrapper for compatibility.
- Enable arXiv and NBER first; keep SSRN/blog toggled off unless stable.

### Phase 4: StrategySpec and validation

- Add `StrategySpecBuilder`.
- Add MVP signal formulas.
- Add `FactorValidator`.
- Add FDR adjustment.
- Add Gate 2 with `needs_manual_spec` non-reject behavior for unsupported mappings.

### Phase 5: Backtest rigor

- Introduce new backtest runner callback shape.
- Add purged walk-forward split generation.
- Add regime labels.
- Add cost/capacity model.
- Gate on `worst_oos_sharpe`, not mislabeled deflated Sharpe.

### Phase 6: Ensemble and API

- Add ensemble module.
- Add comparison endpoints.
- Add ensemble endpoint.
- Store ensemble output as an artifact.

Each phase must be independently shippable and testable.

## Acceptance Criteria

### Architecture

- No `quant.features.research` module imports `quant.infrastructure`.
- No `quant.features.research` module imports sibling feature modules.
- No `quant.infrastructure.research` module imports `quant.features`.
- Domain ports do not import pandas, numpy, duckdb, requests, or feature models.

### Functional

- Existing `test_research_pipeline.py` still passes.
- `FileResearchStore` remains usable.
- `DuckDBResearchStore` returns the same candidate/seen/status behavior as `FileResearchStore`.
- Pipeline can run with validation disabled and match current behavior.
- Pipeline can run with validation enabled and mark unsupported specs as `needs_manual_spec`.
- Purged CV never emits leaking train/test splits.
- Experiment store can start, complete, list, and retrieve run metrics.
- Ensemble builder handles fewer than two strategies by returning a clear no-op result.

### Quant research rigor

- Signal validation uses execution lag for close-based signals.
- FDR adjustment counts all specs tested in the run.
- Walk-forward metrics separate train and out-of-sample windows.
- Capacity checks are recorded when trade volume data is available.
- Reported `deflated_sharpe_ratio` is `None` until a true DSR implementation exists.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Unsupported paper ideas get rejected too early | Use `needs_manual_spec` instead of rejection when formula mapping is missing |
| DuckDB write conflicts | Keep solo local single-writer assumption; keep JSON fallback during migration |
| SSRN blocks scraping | Keep SSRN disabled by default and implement polite rate limits |
| Factor data unavailable | Return validation report with factor errors and run IC-only validation |
| Backtest callback shape disrupts API wiring | Add an adapter in `quant/api/research_bp.py` and keep legacy callback until migration completes |
| Ensemble optimization overfits | Enable equal-risk methods first; keep max-Sharpe disabled by default |
| More config creates operator burden | Preserve old flat config keys and add grouped config with defaults |

## Deferred Work

- Full NLP formula extraction from papers.
- True Deflated Sharpe Ratio.
- Point-in-time universe membership and delisting-aware equity data.
- Advanced constrained portfolio optimization.
- Frontend visualizations beyond API payloads.
