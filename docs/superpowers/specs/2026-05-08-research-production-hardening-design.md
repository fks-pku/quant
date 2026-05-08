# Research Production Hardening - Design Spec

**Date**: 2026-05-08
**Status**: Ready for implementation
**Scope**: Module D — parameter sensitivity, point-in-time data, survival bias, ERC optimizer

## Problem Statement

Three production-readiness gaps:
1. Strategies may be overfit to specific parameter values with no sensitivity check
2. Validation may use future information (no point-in-time semantics)
3. Delisted stocks inflate backtest returns (survivorship bias)
4. Portfolio weights are inverse-vol approximations, not true equal risk contribution

## Target State

1. Parameter sensitivity grid sweep with stability gate
2. Point-in-time data access through new domain port
3. Historical universe construction excluding delisted stocks
4. True ERC optimizer using scipy.optimize

## Current Baseline

- `validation/factor_validator.py` — no parameter sweep
- `ensemble/optimizer.py` — `equal_risk()` is inverse-vol with iterative cap
- No PIT data port exists
- DuckDB bars tables may lack listing/delisting dates
- `infrastructure/research/market_data/duckdb_research_market_data.py` — returns all data regardless of as-of date

## Architecture Rules

- features/ depends on domain/ ports only
- infrastructure/ implements ports, never imports features
- Domain ports return Any
- No comments unless explicitly requested
- Graceful degradation: all new features optional, system works without them

## Module Details

### Module D1: Parameter Sensitivity Analysis

New file: `features/research/validation/sensitivity.py`

Function: `run_sensitivity_sweep(spec, market_data_port, base_params, config)`

Process:
1. Define parameter grid around base values:
   - lookback_days: [10, 15, 20, 25, 30] (default base: 20)
   - horizon_days: [1, 3, 5, 10, 21] (default base: 5)
2. For each combination, run cross-sectional IC computation (from Module A)
3. Record IC and ICIR for each parameter set
4. Compute degradation from optimal: `degradation = (optimal_ic - ic) / optimal_ic`
5. Stability gate: if max degradation > 30%, flag `unstable_params`

New dataclass:

```python
@dataclass(frozen=True)
class SensitivityReport:
    strategy_id: str
    base_ic: float
    base_params: Dict[str, Any]
    parameter_combinations: List[Dict[str, Any]]
    ic_surface: List[float]
    is_stable: bool
    max_degradation_pct: float
    optimal_params: Dict[str, Any]
```

Integration:
- Called from `FactorValidator.validate()` after base validation
- Controlled by config: `validation.sensitivity_enabled: bool = False` (off by default due to compute cost)
- Result stored in ValidationReport.errors as "sensitivity: stable/unstable (max_degradation=X%)"

### Module D2: Point-in-Time Data Port

New file: `domain/ports/pit_data.py`

```python
class PITData(ABC):
    @abstractmethod
    def get_universe(self, as_of_date: str, market: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_bars_pit(self, symbols: List[str], start: str, end: str, as_of_date: str) -> Any:
        raise NotImplementedError
```

New file: `infrastructure/research/pit_duckdb.py`

Implements PITData:
- `get_universe(as_of_date, market)`:
  - Query DuckDB for symbols with listing_date ≤ as_of_date
  - Exclude symbols with delisting_date ≤ as_of_date
  - If listing_date/delisting_date columns missing, return all symbols (graceful degradation)
- `get_bars_pit(symbols, start, end, as_of_date)`:
  - Return bars with adj_close as point-in-time close
  - Filter out any bars with timestamp > as_of_date (prevent look-ahead)

### Module D3: Survival Bias Handling

New script: `scripts/build_pit_universe.py`

Builds historical universe snapshots:
1. Scan DuckDB bars tables for all symbols and their first/last trading dates
2. Infer listing_date = first bar date, delisting_date = last bar date (if < today, stock delisted)
3. Save universe snapshots per month as Parquet in `infrastructure/var/research/universe_snapshots/`
4. Format: `{year}_{month}_universe.parquet` with columns: symbol, market, listing_date, delisting_date

Modify `duckdb_research_market_data.py`:
- Accept optional `pit_data` parameter
- When PIT data available, filter queries to exclude delisted symbols

### Module D4: ERC Optimizer Upgrade

Modify `features/research/ensemble/optimizer.py`

Replace current `equal_risk` with true ERC:

```python
def equal_risk_erc(cov_matrix, max_weight=0.25):
    """
    True Equal Risk Contribution using scipy.optimize.minimize.
    Each asset contributes equally to total portfolio risk.
    If scipy unavailable, fall back to inverse-vol with cap.
    """
    try:
        from scipy.optimize import minimize
        n = len(cov_matrix)
    except ImportError:
        return inverse_vol_with_cap(volatilities, max_weight)
```

Algorithm:
1. Compute covariance matrix from return series
2. Define risk contribution: RC_i = w_i * (Σw)_i / √(w'Σw)
3. Objective: minimize Σ(RC_i - RC_target)² where RC_target = 1/n
4. Constraints: w'1 = 1, 0 ≤ w_i ≤ max_weight
5. Use SLSQP solver

## Storage Design

```
infrastructure/var/research/
  universe_snapshots/
    2020_01_universe.parquet
    2020_02_universe.parquet
    ...
```

Universe snapshot schema:
- symbol (str), market (str), listing_date (str), delisting_date (str|None)

## Configuration Changes

```yaml
validation:
  sensitivity_enabled: false
  sensitivity_lookback_grid: [10, 15, 20, 25, 30]
  sensitivity_horizon_grid: [1, 3, 5, 10, 21]
  sensitivity_max_degradation_pct: 30

pit:
  enabled: false
  universe_snapshot_dir: infrastructure/var/research/universe_snapshots
```

## Migration Plan

1. Add PITData port and DuckDB adapter
2. Build universe snapshot script
3. Implement parameter sensitivity
4. Upgrade ERC optimizer
5. Wire PIT data into FactorValidator (optional, off by default)

## Acceptance Criteria

- Parameter sensitivity produces SensitivityReport with stability assessment
- PIT port returns correct universe for given as_of_date
- Universe snapshots build from existing DuckDB data
- ERC optimizer produces mathematically equal risk contributions
- System works identically when all new features are disabled
- Architecture boundary tests pass
- Existing tests pass

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| scipy unavailable for ERC | Fall back to inverse-vol with cap |
| No listing/delisting dates in DuckDB | Infer from first/last bar dates, flag as approximate |
| Sensitivity sweep too slow | Disabled by default, configurable grid |
| PIT universe incomplete | Graceful degradation: use full universe when PIT unavailable |

## Deferred Work

- Real point-in-time fundamental data (as-reported vs restated)
- Institutional holdings-based universe construction
- Risk parity with Black-Litterman views
