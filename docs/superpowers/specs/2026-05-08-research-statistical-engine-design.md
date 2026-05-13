# Research Statistical Engine — Cross-Sectional Validation

**Date**: 2026-05-08
**Author**: Quantitative Research
**Status**: Design — ready for implementation
**Scope**: Module A of a 4-module upgrade. Upgrades `FactorValidator` from single-symbol to full-universe cross-sectional statistics.

## Problem Statement

The current `FactorValidator` in `quant/features/research/validation/factor_validator.py` has three fundamental statistical deficiencies:

1. **Single-symbol testing**: Line 35 uses `spec.universe[:1]`, computing all statistics on one symbol only. This is not cross-sectional — it tests whether a signal predicts *that stock's* returns over time, not whether the signal ranks stocks correctly across the universe on each date.

2. **Incorrect ICIR formula**: Line 63 computes `rank_ic * sqrt(N)`, which is a single-period test statistic (t-stat under the null of zero correlation for that one period). The proper ICIR is `mean(daily_IC) / std(daily_IC)` over the time series of cross-sectional ICs — a fundamentally different quantity.

3. **Empty IC decay**: Line 71 always returns `ic_decay=[]`. The field is defined but never populated.

These issues mean the current validation cannot distinguish a genuine cross-sectional alpha signal from noise.

## Target State

A statistically rigorous cross-sectional validation engine that:

1. Computes **Spearman rank IC across the full universe** on each trading date (cross-sectional, not time-series).
2. Reports the correct **ICIR = mean(IC) / std(IC)** over the daily IC time series.
3. Populates **IC decay** at four horizons (1d, 5d, 10d, 21d).
4. Computes the **Fama-MacBeth t-statistic** as an alternative significance test.
5. Detects the **market from symbol prefixes** and loads the appropriate universe automatically.

## Current Baseline

### Files to modify

| File | Current state | What changes |
|------|---------------|--------------|
| `quant/features/research/validation/factor_validator.py` | Tests `universe[:1]`; ICIR = `IC * sqrt(N)`; `ic_decay=[]` | Calls cross-sectional engine; passes full universe |
| `quant/features/research/validation/signal_library.py` | Expects single-symbol DataFrame | Accepts multi-symbol DataFrame; computes per-symbol with groupby |
| `quant/features/research/models.py` | `ValidationReport` has no `fama_macbeth_tstat` | Add `fama_macbeth_tstat: float = 0.0` field |
| `quant/infrastructure/research/market_data/duckdb_research_market_data.py` | Queries `bars` table with symbol list | Market-aware table selection; universe listing method |
| `quant/domain/ports/research_market_data.py` | Only `get_daily_bars` | Add `get_universe_symbols(market: str)` abstract method |

### Files to add

| File | Purpose |
|------|---------|
| `quant/features/research/validation/cross_sectional.py` | Cross-sectional IC, ICIR, IC decay, Fama-MacBeth computation |

### Key current code (for reference)

`factor_validator.py:35`:
```python
symbols = spec.universe[:1]
```

`factor_validator.py:63`:
```python
rank_ic_ir = rank_ic * np.sqrt(len(common_idx)) if rank_ic != 0 else 0.0
```

`factor_validator.py:71`:
```python
ic_decay=[],
```

## Non-Negotiable Architecture Rules

### Feature layer

Allowed in `quant/features/research/validation/`:

- Pure statistical computation using pandas/numpy/scipy.
- Dataclasses and local feature models.
- Calls to injected domain ports (`ResearchMarketData`).
- Internal use of DataFrames after receiving `Any` from ports.

Not allowed:

- Direct DuckDB connections.
- Direct filesystem access.
- Imports from `quant.infrastructure.*`.
- Imports from sibling features.

### Infrastructure layer

`quant/infrastructure/research/market_data/`:

- Implements `ResearchMarketData` port.
- Market-aware table routing (`daily_cn_ochl`, `daily_us`, `daily_hk`).
- Must not import `quant.features.*`.

### Domain port

`ResearchMarketData` returns `Any`. No pandas types in the port signature.

## Module Details

### 1. Market-Aware Universe Selection

Detection logic lives in `cross_sectional.py` as a pure function:

```python
def detect_market(symbol: str) -> str:
    ...
```

Rules (from `docs/reference/symbol-registry.md`):

| Pattern | Market | DuckDB table |
|---------|--------|-------------|
| 6-digit numeric | `cn` | `daily_cn_ochl` |
| 5-digit numeric | `hk` | `daily_hk` |
| 1-5 char alphabetic | `us` | `daily_us` |

The infrastructure adapter (`DuckDBResearchMarketData`) gains a new method implementing a new port method:

```python
def get_universe_symbols(self, market: str) -> List[str]:
```

This queries the appropriate table (`SELECT DISTINCT symbol FROM {table}`) and returns the full universe.

The `FactorValidator` detects the market from the first symbol in `spec.universe`, fetches the full universe, and passes it to the cross-sectional engine.

### 2. Cross-Sectional IC Computation

Lives in `cross_sectional.py`:

```python
def compute_cross_sectional_ic(
    signals: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_stocks: int = 20,
) -> pd.Series:
```

Inputs:

- `signals`: DataFrame indexed by date, columns by symbol. Values are the signal for each symbol on each date.
- `forward_returns`: DataFrame indexed by date, columns by symbol. Values are the forward return for each symbol on each date.
- `min_stocks`: minimum number of non-NaN cross-section pairs required for a valid IC date.

Process for each date:

1. Extract the cross-section: all `(signal_i, return_i)` pairs where both are non-NaN.
2. If fewer than `min_stocks` valid pairs, skip that date (return NaN).
3. Compute Spearman rank correlation between the two vectors.
4. Result is a `pd.Series` of daily ICs indexed by date.

Minimum 100 valid cross-sectional dates required. If fewer, the validation report returns an error.

### 3. Correct ICIR

```python
def compute_icir(daily_ic: pd.Series) -> float:
```

Formula:

```
ICIR = mean(daily_ic) / std(daily_ic)
```

Where `daily_ic` is the output of `compute_cross_sectional_ic` with NaN dates dropped.

Edge case: if `std(daily_ic) == 0`, return `0.0` (flat IC means no information ratio).

### 4. IC Decay Curve

```python
def compute_ic_decay(
    signals: pd.DataFrame,
    returns: pd.DataFrame,
    horizons: List[int],
    execution_lag: int = 1,
    min_stocks: int = 20,
) -> List[tuple]:
```

For each horizon `h` in `[1, 5, 10, 21]`:

1. Compute forward returns: `returns.pct_change(h).shift(-h - execution_lag)`.
2. Compute cross-sectional IC time series using `compute_cross_sectional_ic(signals, forward_returns_h)`.
3. Take `mean(daily_ic_h)` as the IC for that horizon.

Return format: `[(1, ic_1d), (5, ic_5d), (10, ic_10d), (21, ic_21d)]`

This populates `ValidationReport.ic_decay` which is currently always `[]`.

### 5. Fama-MacBeth t-statistic

```python
def compute_fama_macbeth_tstat(
    signals: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_stocks: int = 20,
) -> float:
```

Two-step procedure:

1. **Cross-sectional regression (Step 1)**: For each date `t`, run OLS regression `return_i = alpha_t + beta_t * signal_i + epsilon_i` across all stocks `i`. Collect the time series of `beta_t` coefficients.

2. **Time-series aggregation (Step 2)**:
   - `beta_bar = mean(beta_t)`
   - `se_beta = std(beta_t) / sqrt(T)` where `T` is the number of valid dates
   - `t_stat = beta_bar / se_beta`

Edge cases:

- If fewer than 100 valid dates, return `0.0`.
- If `se_beta == 0`, return `0.0`.

Reported in the new `ValidationReport.fama_macbeth_tstat` field.

### 6. Signal Library Multi-Symbol Support

`compute_signal` in `signal_library.py` currently expects single-symbol data. It must be upgraded:

```python
def compute_signal(formula_key: str, data: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
```

When `data` has a `symbol` column (multi-symbol):

1. Group by `symbol`.
2. Apply the existing per-symbol formula within each group.
3. Return a DataFrame indexed by `(date, symbol)` or pivoted to date × symbol.

The function must also work when called with single-symbol data (no `symbol` column) for backward compatibility.

### 7. Infrastructure: Market-Aware Data Loading

`DuckDBResearchMarketData` changes:

1. **New port method**: `get_universe_symbols(market: str) -> List[str]`
   - CN: `SELECT DISTINCT symbol FROM daily_cn_ochl`
   - US: `SELECT DISTINCT symbol FROM daily_us`
   - HK: `SELECT DISTINCT symbol FROM daily_hk`

2. **`get_daily_bars` enhancement**: Accept a `market` parameter or detect table from symbol patterns. Query the appropriate market table instead of a generic `bars` table.

The port (`ResearchMarketData`) gains the new abstract method:

```python
@abstractmethod
def get_universe_symbols(self, market: str) -> List[str]:
    raise NotImplementedError
```

## Data Model Changes

### ValidationReport

Add one field. Position it before `errors` to maintain logical grouping:

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
    fama_macbeth_tstat: float = 0.0
    errors: List[str] = field(default_factory=list)
```

Since `ValidationReport` is frozen, existing code that constructs it positionally may break. All construction sites must be audited and updated to use keyword arguments. Known construction sites:

1. `factor_validator.py` — `validate()` and `_error_report()`
2. `test_research_validation.py` — any test fixtures

### ResearchMarketData port

```python
class ResearchMarketData(ABC):
    @abstractmethod
    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def get_universe_symbols(self, market: str) -> List[str]:
        raise NotImplementedError
```

## Revised FactorValidator Flow

```text
FactorValidator.validate(spec)
  |
  |-- 1. detect_market(spec.universe[0])
  |      Returns "cn", "us", or "hk"
  |
  |-- 2. market_data.get_universe_symbols(market)
  |      Returns full universe symbol list
  |
  |-- 3. market_data.get_daily_bars(universe, start, end)
  |      Returns multi-symbol DataFrame with columns [symbol, date, open, high, low, close, volume]
  |
  |-- 4. compute_signal(formula_key, data, lookback)
  |      Returns signal DataFrame (date × symbol)
  |
  |-- 5. compute_cross_sectional_ic(signals, forward_returns)
  |      Returns daily IC time series
  |
  |-- 6. compute_icir(daily_ic)
  |      Returns ICIR = mean/std
  |
  |-- 7. compute_ic_decay(signals, returns, [1,5,10,21], exec_lag)
  |      Returns [(horizon, IC), ...]
  |
  |-- 8. compute_fama_macbeth_tstat(signals, forward_returns)
  |      Returns t-stat
  |
  |-- 9. Build ValidationReport with all computed fields
```

## Storage Design

No new storage is required. This module reads existing DuckDB market data tables (`daily_cn_ochl`, `daily_us`, `daily_hk`) through the existing port.

The validation results are stored in the existing `ValidationReport` dataclass, which is serialized through whichever persistence mechanism the research engine uses.

## Acceptance Criteria

### Architecture

- `cross_sectional.py` does not import `quant.infrastructure.*`.
- `cross_sectional.py` does not import sibling features.
- `DuckDBResearchMarketData` does not import `quant.features.*`.
- `ResearchMarketData` port does not expose pandas/numpy types.
- Existing architecture boundary tests pass.

### Statistical correctness

- **Cross-sectional IC**: computed across full universe on each date, not on `universe[:1]`.
- **ICIR**: `mean(daily_IC) / std(daily_IC)` over the time series, not `IC * sqrt(N)`.
- **IC decay**: populated with 4 (horizon, IC) pairs, not `[]`.
- **Fama-MacBeth**: two-step regression t-stat, reported in `fama_macbeth_tstat`.
- **Minimum dates**: at least 100 valid cross-sectional dates required; error otherwise.
- **Missing data**: symbols with NaN signal or return on a given date are excluded from that date's cross-section.

### Functional

- All existing tests pass without modification to test logic (test fixtures may need updating for the new field).
- `compute_signal` works with both single-symbol and multi-symbol DataFrames.
- `detect_market` correctly identifies CN (6-digit), US (alphabetic), HK (5-digit).
- `get_universe_symbols` returns all symbols from the appropriate market table.
- `ValidationReport` construction uses keyword arguments everywhere.
- IC decay values are the mean IC at each horizon, not a list of raw daily ICs.

### Backward compatibility

- `compute_signal` backward-compatible with single-symbol input.
- `ValidationReport.fama_macbeth_tstat` defaults to `0.0` so existing serialized reports are not broken.
- `FactorValidator._error_report` returns the new field as `0.0`.
- Gate 2b thresholds (`abs(rank_ic) >= 0.02`) remain unchanged.

## Migration Plan

### Step 1: Data model and port changes

- Add `fama_macbeth_tstat: float = 0.0` to `ValidationReport`.
- Add `get_universe_symbols(market: str) -> List[str]` to `ResearchMarketData` port.
- Audit all `ValidationReport` construction sites to use keyword arguments.
- Run existing tests to verify nothing breaks.

### Step 2: Cross-sectional engine

- Create `quant/features/research/validation/cross_sectional.py`.
- Implement `detect_market`, `compute_cross_sectional_ic`, `compute_icir`, `compute_ic_decay`, `compute_fama_macbeth_tstat`.
- Add unit tests with synthetic data (known IC = 0.05, verify output matches).

### Step 3: Signal library upgrade

- Modify `compute_signal` to handle multi-symbol DataFrames via groupby.
- Maintain backward compatibility for single-symbol calls.
- Add tests for both code paths.

### Step 4: Infrastructure upgrade

- Implement `get_universe_symbols` in `DuckDBResearchMarketData`.
- Update `get_daily_bars` to query market-specific tables.
- Add integration tests against DuckDB with test data.

### Step 5: FactorValidator rewrite

- Replace single-symbol logic with cross-sectional flow.
- Wire up `cross_sectional.py` functions.
- Populate `ic_decay` and `fama_macbeth_tstat` in the report.
- Run full test suite.

Each step must be independently shippable. No step may break existing tests.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| DuckDB tables `daily_cn_ochl`/`daily_us`/`daily_hk` may not exist in all deployments | `get_universe_symbols` returns empty list and `FactorValidator` produces an error report |
| Large universe (3000+ CN stocks) causes slow queries | Infrastructure adapter can limit to top-N by volume or market cap; validation config controls max universe size |
| Spearman rank correlation is O(N log N) per date | Acceptable for typical universe sizes (< 5000); profile before optimizing |
| Fama-MacBeth OLS per date requires `numpy.linalg.lstsq` or `scipy` | Use `numpy.linalg.lstsq` which is already available through existing imports |
| `ValidationReport` is frozen — positional construction breaks | Keyword-only audit in Step 1 catches all sites |
| Signal NaN propagation differs across formulas | `compute_cross_sectional_ic` handles NaN via pairwise dropna per date |

## Deferred Work

- Newey-West adjusted standard errors for ICIR (accounts for autocorrelation in daily ICs).
- Sector-neutral cross-sectional IC (subtract sector means before ranking).
- Bootstrap confidence intervals for IC and ICIR.
- Asymmetric IC decay (up vs down markets).
- Point-in-time universe membership (delisting survivorship bias).
