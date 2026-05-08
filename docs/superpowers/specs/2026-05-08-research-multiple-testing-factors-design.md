# Research Multiple Testing & Factor Decomposition - Design Spec

**Date**: 2026-05-08
**Status**: Ready for implementation
**Scope**: Module B — Deflated Sharpe Ratio, Fama-French factor pipeline, factor decomposition in validation

## Problem Statement

Two critical gaps prevent the research system from making reliable alpha claims:

1. **Multiple testing defense**: `PurgedWalkForwardResult.deflated_sharpe_ratio` is always `None`. An auto-discovery system that tests many strategies needs mathematical correction for selection bias under multiple trials.
2. **Factor attribution**: `ValidationReport.ff_alpha_monthly`, `ff_alpha_tstat`, and `ff_r2` are hardcoded to `0.0`. Without decomposing returns into factor exposures, you cannot distinguish genuine alpha from loading on known risk premia (value, size, momentum, quality).

## Target State

1. Implement Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) with `n_trials` tracked across pipeline runs.
2. Build Fama-French factor data pipeline: US factors from Kenneth French Data Library, CN factors self-constructed from DuckDB market data.
3. Run OLS factor regression in validation, populating `ff_alpha_monthly`/`ff_alpha_tstat`/`ff_r2` with real values.

## Current Baseline

| File | Current role | Problem |
|------|-------------|---------|
| `features/research/rigor/backtest_hub.py` | Sets `deflated_sharpe_ratio=None` | No DSR computation |
| `features/research/validation/ff_decomposition.py` | Returns `{alpha_monthly:0, tstat:0, r2:0}` | Hardcoded zeros |
| `infrastructure/research/factors/ff_factor_store.py` | `get_factors()` returns `None` | Stub |
| `infrastructure/research/factors/chen_zimmermann_store.py` | Returns `None`/empty | Stub |
| `domain/ports/factor_data.py` | `FactorData` ABC with `get_factors()` and `list_factors()` | Port exists, no real impl |

## Architecture Rules

- `features/research/` calls injected domain ports only. No `infrastructure` or sibling-feature imports.
- `infrastructure/research/` implements domain ports. No `features` imports.
- Domain ports use `Any`/`dict`/`list` signatures. No pandas/numpy/duckdb in type signatures.
- Feature code may use pandas/numpy/scipy internally after receiving data through ports.
- No comments unless explicitly requested.

## Module Details

### B1: Deflated Sharpe Ratio

New file: `features/research/rigor/dsr.py`

The DSR formula from Bailey & Lopez de Prado (2014) corrects the observed Sharpe ratio for non-normality and multiple testing:

```
SR_adj = SR_hat * sqrt(T)
V_hat  = (1 + SR_hat^2 * 0.5) * n_trials / T

DSR_z  = (SR_adj - E[max_Z]) / sqrt(V(max_Z))
       ≈ (SR_adj * (1 - skew * SR_hat + (kurt - 1) * SR_hat^2 / 4) - E[max_SR_sum]) / sqrt(V_single_max)

E[max(SR_single)] ≈ sqrt(2 * log(n_trials))
Var[max(SR_single)] ≈ (1 - 0.5 * SR_hat^2) / T + SR_hat^2 * log(n_trials) / T

DSR = Φ(DSR_z)
```

Implementation:

```python
import numpy as np
from scipy.stats import norm


def compute_dsr(returns, n_trials=1, risk_free_rate=0.0):
    T = len(returns)
    if T < 30:
        return None
    excess = returns - risk_free_rate
    sr = np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252)
    if sr <= 0:
        return 0.0
    skew = (excess - excess.mean()) ** 3
    skew = skew.mean() / (excess.std() ** 3) if excess.std() > 1e-10 else 0
    kurt = (excess - excess.mean()) ** 4
    kurt = kurt.mean() / (excess.std() ** 4) if excess.std() > 1e-10 else 3
    excess_kurt = kurt - 3
    adj = 1 - skew * sr + excess_kurt * sr ** 2 / 4
    if adj <= 0:
        return 0.0
    sr_single = sr * np.sqrt(T / 252)
    if n_trials > 1:
        E_max = np.sqrt(2 * np.log(n_trials))
        sr_single = sr_single * adj - E_max
    else:
        sr_single = sr_single * adj
    V_single = (1 + sr ** 2 * 0.5) / T * 252
    std = np.sqrt(V_single) if V_single > 0 else 1.0
    z = sr_single / max(std, 1e-10)
    return float(norm.cdf(z))
```

Key points:
- Uses scipy.stats.norm.cdf for Φ (standard normal CDF)
- `n_trials` comes from ExperimentStore: count of recent pipeline runs
- Returns DSR probability value between 0 and 1
- DSR > 0.95 indicates statistically significant Sharpe

Integration into `backtest_hub.py`:

```python
from quant.features.research.rigor.dsr import compute_dsr

# After walk-forward results computed:
test_returns = _extract_test_returns(split_results)
n_trials = self._experiment_store.list_runs(limit=100)  # count runs
dsr_value = compute_dsr(test_returns, n_trials=len(n_trials))
result.deflated_sharpe_ratio = dsr_value
```

DSR Gate (Gate 4c):

| Gate | Condition | Failure behavior |
|------|-----------|-----------------|
| Gate 4c | DSR > 0.95 | Mark candidate "needs_more_validation", do not auto-promote |

DSR is a warning gate, not a hard reject. Candidates with DSR < 0.95 are not rejected but are flagged for review.

### B2: Kenneth French Factor Data Pipeline (US)

New file: `infrastructure/research/factors/ff_downloader.py`

Source URLs:
- FF5 daily: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip`
- Momentum daily: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip`

Implementation:

```python
import logging
import zipfile
import pandas as pd
from pathlib import Path
from io import BytesIO
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_CACHE = Path("infrastructure/var/research/factor_zoo")
STALE_DAYS = 30


def download_ff5_daily(cache_dir=None):
    cache = Path(cache_dir or DEFAULT_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "ff5_daily.parquet"
    if path.exists() and _is_fresh(path, STALE_DAYS):
        return pd.read_parquet(path)
    url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
    return _download_and_parse_ff(url, path, cache)


def download_ff_momentum_daily(cache_dir=None):
    cache = Path(cache_dir or DEFAULT_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "ff_momentum_daily.parquet"
    if path.exists() and _is_fresh(path, STALE_DAYS):
        return pd.read_parquet(path)
    url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
    return _download_and_parse_ff(url, path, cache)


def _download_and_parse_ff(url, target_path, cache_dir):
    try:
        import requests
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with zipfile.ZipFile(BytesIO(resp.content)) as z:
            csv_file = [n for n in z.namelist() if n.endswith('.CSV') or n.endswith('.csv')][0]
            df = _parse_ff_csv(z.read(csv_file).decode('utf-8'))
        df.to_parquet(target_path)
        logger.info(f"Downloaded and cached FF factor data to {target_path}")
        return df
    except Exception as e:
        logger.warning(f"Failed to download FF data: {e}")
        if target_path.exists():
            return pd.read_parquet(target_path)
        return None


def _parse_ff_csv(content):
    lines = content.strip().split('\n')
    data_lines = []
    started = False
    for line in lines:
        if line.strip().startswith('Copyright'):
            break
        if started and line.strip():
            data_lines.append(line.strip())
        if line.strip().startswith('Mkt-RF') or line.strip().startswith('date'):
            started = True
    from io import StringIO
    df = pd.read_csv(StringIO('\n'.join(data_lines)), delim_whitespace=True)
    if 'date' in df.columns:
        df.columns = ['date'] + [c.strip() for c in df.columns[1:]]
    return df


def _is_fresh(path, stale_days):
    return (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)) < timedelta(days=stale_days)
```

### B3: Chinese Factor Construction

New file: `infrastructure/research/factors/cn_factor_builder.py`

Build simplified Chinese 3-factor model from DuckDB `daily_cn` table:

```python
import logging
import pandas as pd
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)


def build_cn3_factors(db_path, start="2019-01-01", end="2024-12-31", cache_dir=None):
    """
    Build simplified CN 3-factor (MKT, SMB, HML) from DuckDB daily_cn data.
    - MKT: equal-weighted daily return of all valid stocks
    - SMB: top 50% by market_cap_proxy short, bottom 50% long
    - HML: top 30% by EP proxy short, bottom 30% long
    """
    import duckdb
    cache = Path(cache_dir or DEFAULT_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "cn3_daily.parquet"
    if path.exists():
        return pd.read_parquet(path)
    conn = duckdb.connect(db_path, read_only=True)
    df = conn.execute("""
        SELECT symbol, date, close, volume, turnover
        FROM daily_cn
        WHERE date >= ? AND date <= ?
        ORDER BY date
    """, [start, end]).fetchdf()
    conn.close()
    if df.empty:
        return None
    df['date'] = pd.to_datetime(df['date'])
    df['return'] = df.groupby('symbol')['close'].pct_change()
    # Market-cap proxy: turnover * close (rough approximation)
    df['mcap_proxy'] = df['turnover'] * df['close']
    # EP proxy: 1 / close (inverse price as rough value proxy)
    df['ep_proxy'] = 1 / df['close']
    factors = []
    for date, group in df.groupby('date'):
        if len(group) < 30:
            factors.append([date, 0.0, 0.0, 0.0])
            continue
        group = group.dropna(subset=['return', 'mcap_proxy'])
        if len(group) < 30:
            factors.append([date, 0.0, 0.0, 0.0])
            continue
        mkt = group['return'].mean()
        median_cap = group['mcap_proxy'].median()
        small = group[group['mcap_proxy'] <= median_cap]['return'].mean()
        big = group[group['mcap_proxy'] > median_cap]['return'].mean()
        smb = small - big
        ep_30 = group['ep_proxy'].quantile(0.3)
        ep_70 = group['ep_proxy'].quantile(0.7)
        low_val = group[group['ep_proxy'] <= ep_30]['return'].mean()
        high_val = group[group['ep_proxy'] >= ep_70]['return'].mean()
        hml = high_val - low_val
        factors.append([date, mkt, smb, hml])
    result = pd.DataFrame(factors, columns=['date', 'MKT', 'SMB', 'HML'])
    result.to_parquet(path)
    logger.info(f"Built CN 3-factor model: {len(result)} days")
    return result
```

### B4: Factor Data Store Implementation

Modify `infrastructure/research/factors/ff_factor_store.py`:

```python
import logging
from typing import Any, Dict, List, Optional
from pathlib import Path
import pandas as pd

from quant.domain.ports.factor_data import FactorData

logger = logging.getLogger(__name__)

_DEFAULT_CACHE = Path("infrastructure/var/research/factor_zoo")


class FFFactorStore(FactorData):
    def __init__(self, cache_dir=None):
        self._cache = Path(cache_dir or _DEFAULT_CACHE)
        self._loaded = {}

    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        available = {"MKT": "ff5_daily", "SMB": "ff5_daily", "HML": "ff5_daily",
                     "RMW": "ff5_daily", "CMA": "ff5_daily", "RF": "ff5_daily",
                     "Mom": "ff_momentum_daily"}
        for name in names:
            file_key = available.get(name)
            if file_key is None:
                logger.warning(f"Factor '{name}' not available")
                continue
            if file_key not in self._loaded:
                path = self._cache / f"{file_key}.parquet"
                if path.exists():
                    self._loaded[file_key] = pd.read_parquet(path)
            df = self._loaded.get(file_key)
            if df is not None and 'date' in df.columns:
                mask = (df['date'] >= start) & (df['date'] <= end)
                return df[mask]
        return None

    def list_factors(self) -> List[Dict[str, Any]]:
        return [
            {"name": "MKT", "description": "Market excess return", "source": "Kenneth French Data Library"},
            {"name": "SMB", "description": "Small minus Big", "source": "Kenneth French Data Library"},
            {"name": "HML", "description": "High minus Low (book-to-market)", "source": "Kenneth French Data Library"},
            {"name": "RMW", "description": "Robust minus Weak (profitability)", "source": "Kenneth French Data Library"},
            {"name": "CMA", "description": "Conservative minus Aggressive (investment)", "source": "Kenneth French Data Library"},
            {"name": "RF", "description": "Risk-free rate", "source": "Kenneth French Data Library"},
            {"name": "Mom", "description": "Momentum factor", "source": "Kenneth French Data Library"},
        ]
```

### B5: Factor Decomposition in Validation

Modify `features/research/validation/ff_decomposition.py`:

```python
import logging
import numpy as np

logger = logging.getLogger(__name__)


def decompose_alpha(strategy_returns, factor_data, risk_free=0.0):
    if strategy_returns is None or factor_data is None:
        return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
    try:
        import pandas as pd
        if not isinstance(strategy_returns, pd.Series):
            strategy_returns = pd.Series(strategy_returns)
        excess = strategy_returns - risk_free
        common_idx = excess.index.intersection(factor_data.index)
        if len(common_idx) < 126:
            return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
        y = excess.loc[common_idx]
        factor_cols = [c for c in factor_data.columns if c not in ('date', 'RF')]
        X = factor_data.loc[common_idx, factor_cols]
        X = X.dropna(axis=1, how='any').fillna(0.0)
        y = y[y.index.isin(X.index)]
        X = X.loc[y.index]
        if len(y) < 126 or X.shape[1] == 0:
            return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
        X_with_const = np.column_stack([np.ones(len(X)), X.values])
        beta, residuals, rank, s = np.linalg.lstsq(X_with_const, y.values, rcond=None)
        alpha_daily = beta[0]
        residuals = y.values - X_with_const @ beta
        resid_std = np.std(residuals, ddof=X_with_const.shape[1])
        alpha_se = resid_std / np.sqrt(len(y)) if len(y) > 0 else 1.0
        alpha_tstat = alpha_daily / max(alpha_se, 1e-10) if alpha_se > 1e-10 else 0.0
        ss_total = np.sum((y.values - y.mean()) ** 2)
        ss_resid = np.sum(residuals ** 2)
        r2 = 1 - ss_resid / ss_total if ss_total > 0 else 0.0
        return {
            "alpha_monthly": float(alpha_daily * 21),
            "tstat": float(alpha_tstat),
            "r2": float(r2),
        }
    except Exception as e:
        logger.warning(f"FF decomposition failed: {e}")
        return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
```

Integration into `factor_validator.py`:
- After IC validation passes, call `decompose_alpha` if `self._factor_data` is available
- Populate `ValidationReport.ff_alpha_monthly`/`ff_alpha_tstat`/`ff_r2`
- On failure/absence: keep zeros, add "factor_data_unavailable" to errors

## Storage Design

```
infrastructure/var/research/factor_zoo/
  ff5_daily.parquet          # US Fama-French 5-factor daily (auto-downloaded)
  ff_momentum_daily.parquet  # US Momentum factor daily (auto-downloaded)
  cn3_daily.parquet          # CN simplified 3-factor daily (self-constructed)
```

Factor Parquet schema:
- US FF5: date, Mkt-RF (float), SMB (float), HML (float), RMW (float), CMA (float), RF (float)
- US Mom: date, Mom (float)
- CN3: date, MKT (float), SMB (float), HML (float)

All files cached with 30-day staleness. Re-download/rebuild only if missing or stale.

## Data Model Changes

No new fields needed. Existing fields populated for the first time:
- `PurgedWalkForwardResult.deflated_sharpe_ratio: Optional[float]` — DSR probability
- `ValidationReport.ff_alpha_monthly: float` — monthly alpha from factor regression
- `ValidationReport.ff_alpha_tstat: float` — t-statistic of alpha
- `ValidationReport.ff_r2: float` — R-squared of factor model

## Migration Plan

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Implement `dsr.py` with DSR formula | Unit tests for DSR with known inputs |
| 2 | Integrate DSR into `backtest_hub.py` | `deflated_sharpe_ratio` not None after walk-forward |
| 3 | Implement `ff_downloader.py` | Parquet files exist in factor_zoo |
| 4 | Implement `cn_factor_builder.py` | CN3 Parquet built from DuckDB |
| 5 | Rewrite `ff_factor_store.py` | `get_factors()` returns real DataFrames |
| 6 | Rewrite `ff_decomposition.py` | `ff_alpha_monthly` populated with regression results |
| 7 | Wire factor data into `factor_validator.py` | Factor-augmented ValidationReports |

## Acceptance Criteria

- DSR computed with Bailey/Lopez de Prado formula (not just `min(test_sharpe)`)
- DSR gate enforced (DSR < 0.95 flagged, not hard-rejected)
- US FF5 factor data downloaded from Kenneth French Data Library and cached as Parquet
- CN 3-factor model self-constructed from existing DuckDB daily_cn table
- `ff_alpha_monthly` populated from OLS regression when factor data available
- Graceful degradation: all zeros with error message when factor data unavailable
- `n_trials` tracked from ExperimentStore for DSR computation
- Architecture boundary tests pass
- Existing research pipeline tests pass

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| FF download blocked (China firewall) | Cache Parquet locally, set long staleness, allow manual placement |
| CN factor data quality (mcap proxy approximate) | Flag CN factors as "simplified" in metadata |
| scipy not available for DSR CDF | Fall back to approximate normal CDF using erf; or return None |
| Factor regression on short history | Require min 126 observations for regression |
| n_trials inaccurate (multiple pipeline runs) | Use ExperimentStore count as approximation; config override available |

## Deferred Work

- Chen-Zimmermann Open Source Asset Pricing full integration (206 factors)
- True Fama-MacBeth cross-sectional factor regression (handled in Module A)
- Macro factor augmentation (inflation, industrial production, credit spreads)
