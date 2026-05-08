# Research Rigor Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four 2026-05-08 research rigor specs in dependency order, turning the current MVP research pipeline into a cross-sectional, multiple-testing-aware, regime-aware, production-hardened validation system.

**Architecture:** Keep statistical computation inside `quant/features/research/`, external IO inside `quant/infrastructure/research/`, and all external contracts as `Any`-returning domain ports. Module A is the foundation; Modules B and C consume Module A outputs; Module D is opt-in production hardening and must degrade gracefully when PIT data or scipy features are unavailable.

**Tech Stack:** Python 3.10+, dataclasses, pandas, numpy, scipy, DuckDB adapters, stdlib `html.parser`, pytest.

---

## Reference Documents

- Spec A: `docs/superpowers/specs/2026-05-08-research-statistical-engine-design.md`
- Spec B: `docs/superpowers/specs/2026-05-08-research-multiple-testing-factors-design.md`
- Spec C: `docs/superpowers/specs/2026-05-08-research-regime-rigor-design.md`
- Spec D: `docs/superpowers/specs/2026-05-08-research-production-hardening-design.md`
- Parent plan: `docs/superpowers/plans/2026-05-06-research-pipeline-upgrade.md`
- Architecture rules: `AGENTS.md`, `ARCHITECTURE.md`, `quant/features/research/AGENTS.md`, `quant/domain/AGENTS.md`, `quant/infrastructure/AGENTS.md`

## Spec Alignment

| Spec | Current baseline | Dependency | Plan task |
|------|------------------|------------|-----------|
| A: Cross-sectional statistical engine | `FactorValidator` uses `spec.universe[:1]`, ICIR is `IC * sqrt(N)`, `ic_decay=[]` | Foundation | Task 1 |
| B: Multiple testing and factor decomposition | `deflated_sharpe_ratio=None`, factor stores return `None`, FF decomposition returns zeros | Uses A validation returns and experiment store | Task 2 |
| C: Regime rigor, capacity, SSRN, IC decay gate | Regime segments not consumed, capacity ignored, SSRN stub, IC decay ungated | IC decay gate requires Task 1 | Task 3 |
| D: Production hardening | No sensitivity sweep, no PIT port, inverse-vol ERC approximation | Sensitivity depends Task 1; PIT/ERC independent | Task 4 |

## Current Baseline Findings

- `quant/features/research/validation/factor_validator.py` still fetches only `spec.universe[:1]` and validates a single time series.
- `quant/features/research/validation/signal_library.py` supports only single-symbol input.
- `quant/features/research/models.py` lacks `ValidationReport.fama_macbeth_tstat`, `PurgedWalkForwardResult.regime_breakdown`, and `PurgedWalkForwardResult.bull_only_warning`.
- `quant/domain/ports/research_market_data.py` lacks `get_universe_symbols(market)`.
- `quant/infrastructure/research/market_data/duckdb_research_market_data.py` queries a generic `bars` table only.
- `quant/features/research/validation/ff_decomposition.py`, `quant/infrastructure/research/factors/ff_factor_store.py`, and `quant/infrastructure/research/sources/ssrn_source.py` are stubs.
- Current shell `python` is `C:\Python314\python.exe` and lacks `pytest`; execute this plan in a project Python environment with dependencies from `requirements.txt`.

## File Structure

### Create

- `quant/features/research/validation/cross_sectional.py`
- `quant/features/research/rigor/dsr.py`
- `quant/infrastructure/research/factors/ff_downloader.py`
- `quant/infrastructure/research/factors/cn_factor_builder.py`
- `quant/features/research/validation/sensitivity.py`
- `quant/domain/ports/pit_data.py`
- `quant/infrastructure/research/pit_duckdb.py`
- `quant/scripts/build_pit_universe.py`

### Modify

- `quant/features/research/models.py`
- `quant/domain/ports/research_market_data.py`
- `quant/domain/ports/__init__.py`
- `quant/infrastructure/research/market_data/duckdb_research_market_data.py`
- `quant/features/research/validation/signal_library.py`
- `quant/features/research/validation/factor_validator.py`
- `quant/features/research/validation/ff_decomposition.py`
- `quant/infrastructure/research/factors/ff_factor_store.py`
- `quant/features/research/rigor/backtest_hub.py`
- `quant/features/research/rigor/regime_detector.py`
- `quant/features/research/research_engine.py`
- `quant/infrastructure/research/sources/ssrn_source.py`
- `quant/features/research/ensemble/optimizer.py`
- `quant/features/research/config/research.yaml`
- `quant/features/research/AGENTS.md`
- `quant/domain/AGENTS.md`
- `quant/infrastructure/AGENTS.md`

### Tests

- Modify: `quant/tests/test_research_validation.py`
- Modify: `quant/tests/test_research_rigor.py`
- Modify: `quant/tests/test_research_ensemble.py`
- Modify: `quant/tests/test_research_architecture_boundaries.py`
- Create or extend: `quant/tests/test_research_factors.py`
- Create or extend: `quant/tests/test_research_production_hardening.py`

---

### Task 1: Module A Cross-Sectional Validation Foundation

**Files:**
- Create: `quant/features/research/validation/cross_sectional.py`
- Modify: `quant/features/research/models.py`
- Modify: `quant/domain/ports/research_market_data.py`
- Modify: `quant/domain/ports/__init__.py`
- Modify: `quant/infrastructure/research/market_data/duckdb_research_market_data.py`
- Modify: `quant/features/research/validation/signal_library.py`
- Modify: `quant/features/research/validation/factor_validator.py`
- Test: `quant/tests/test_research_validation.py`
- Test: `quant/tests/test_research_architecture_boundaries.py`

- [ ] **Step 1: Write failing cross-sectional tests**

Add these tests to `quant/tests/test_research_validation.py`:

```python
def test_detect_market_patterns():
    from quant.features.research.validation.cross_sectional import detect_market

    assert detect_market("600519") == "cn"
    assert detect_market("00700") == "hk"
    assert detect_market("AAPL") == "us"


def test_cross_sectional_ic_uses_full_universe():
    from quant.features.research.validation.cross_sectional import compute_cross_sectional_ic

    dates = pd.date_range("2020-01-01", periods=120, freq="B")
    symbols = [f"S{i:03d}" for i in range(30)]
    signal = pd.DataFrame({s: np.arange(len(dates)) + i for i, s in enumerate(symbols)}, index=dates)
    fwd = signal.rank(axis=1, pct=True)

    ic = compute_cross_sectional_ic(signal, fwd, min_stocks=20)

    assert len(ic.dropna()) == 120
    assert ic.dropna().mean() > 0.99


def test_icir_is_mean_over_std():
    from quant.features.research.validation.cross_sectional import compute_icir

    daily_ic = pd.Series([0.01, 0.02, 0.03, 0.04])

    assert compute_icir(daily_ic) == pytest.approx(daily_ic.mean() / daily_ic.std())


def test_ic_decay_returns_four_horizons():
    from quant.features.research.validation.cross_sectional import compute_ic_decay

    dates = pd.date_range("2020-01-01", periods=160, freq="B")
    symbols = [f"S{i:03d}" for i in range(30)]
    prices = pd.DataFrame({s: 100 + np.arange(len(dates)) + i for i, s in enumerate(symbols)}, index=dates)
    signals = prices.pct_change(20)

    decay = compute_ic_decay(signals, prices, horizons=[1, 5, 10, 21], execution_lag=1, min_stocks=20)

    assert [h for h, _ in decay] == [1, 5, 10, 21]
    assert all(isinstance(v, float) for _, v in decay)


def test_fama_macbeth_tstat_positive_for_linear_relation():
    from quant.features.research.validation.cross_sectional import compute_fama_macbeth_tstat

    dates = pd.date_range("2020-01-01", periods=120, freq="B")
    symbols = [f"S{i:03d}" for i in range(30)]
    signals = pd.DataFrame({s: np.linspace(-1, 1, len(dates)) + i * 0.001 for i, s in enumerate(symbols)}, index=dates)
    returns = signals * 0.02

    assert compute_fama_macbeth_tstat(signals, returns, min_stocks=20) > 0
```

- [ ] **Step 2: Write failing FactorValidator integration test**

Add a fake market data class that records calls:

```python
class FakeCrossSectionalMarketData:
    def __init__(self, data):
        self.data = data
        self.universe_calls = []
        self.bar_calls = []

    def get_universe_symbols(self, market):
        self.universe_calls.append(market)
        return sorted(self.data["symbol"].unique())

    def get_daily_bars(self, symbols, start, end):
        self.bar_calls.append((symbols, start, end))
        return self.data[self.data["symbol"].isin(symbols)].copy()


def test_factor_validator_fetches_full_universe_and_populates_decay():
    from quant.features.research.validation.factor_validator import FactorValidator

    dates = pd.date_range("2020-01-01", periods=180, freq="B")
    rows = []
    for i in range(30):
        symbol = f"S{i:03d}"
        for j, date in enumerate(dates):
            price = 100 + j + i
            rows.append({"symbol": symbol, "date": date, "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1000000})
    data = pd.DataFrame(rows)
    market_data = FakeCrossSectionalMarketData(data)
    validator = FactorValidator(market_data, config={"min_observations": 100, "min_stocks": 20})
    spec = StrategySpec(
        strategy_id="test_cross_sectional",
        strategy_type="momentum",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )

    report = validator.validate(spec)

    assert report.status == "validated"
    assert market_data.universe_calls == ["us"]
    assert len(market_data.bar_calls[0][0]) == 30
    assert len(report.ic_decay) == 4
    assert isinstance(report.fama_macbeth_tstat, float)
```

- [ ] **Step 3: Run tests and verify they fail for missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_validation.py -q
```

Expected: FAIL with missing `cross_sectional.py`, missing `get_universe_symbols`, and missing `fama_macbeth_tstat`.

- [ ] **Step 4: Add model and port fields**

Modify `ValidationReport` in `quant/features/research/models.py`:

```python
@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    status: str
    rank_ic: float
    rank_ic_ir: float
    ic_decay: List[tuple]
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

Modify `ResearchMarketData`:

```python
class ResearchMarketData(ABC):
    @abstractmethod
    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def get_universe_symbols(self, market: str) -> List[str]:
        raise NotImplementedError
```

- [ ] **Step 5: Implement `cross_sectional.py`**

Create `quant/features/research/validation/cross_sectional.py` with:

```python
import math
import re
from typing import List

import numpy as np
import pandas as pd
from scipy import stats


def detect_market(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if re.fullmatch(r"\d{6}", value):
        return "cn"
    if re.fullmatch(r"\d{5}", value):
        return "hk"
    if re.fullmatch(r"[A-Z]{1,5}", value):
        return "us"
    return "us"


def compute_cross_sectional_ic(signals: pd.DataFrame, forward_returns: pd.DataFrame, min_stocks: int = 20) -> pd.Series:
    rows = {}
    common_dates = signals.index.intersection(forward_returns.index)
    for date in common_dates:
        frame = pd.DataFrame({"signal": signals.loc[date], "return": forward_returns.loc[date]}).dropna()
        if len(frame) < min_stocks:
            rows[date] = np.nan
            continue
        ic, _ = stats.spearmanr(frame["signal"], frame["return"])
        rows[date] = float(ic) if not math.isnan(ic) else np.nan
    return pd.Series(rows).sort_index()


def compute_icir(daily_ic: pd.Series) -> float:
    clean = daily_ic.dropna()
    if clean.empty:
        return 0.0
    std = clean.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float(clean.mean() / std)


def compute_ic_decay(signals: pd.DataFrame, prices: pd.DataFrame, horizons: List[int], execution_lag: int = 1, min_stocks: int = 20) -> List[tuple]:
    decay = []
    for horizon in horizons:
        forward = prices.pct_change(horizon).shift(-horizon - execution_lag)
        daily_ic = compute_cross_sectional_ic(signals, forward, min_stocks=min_stocks)
        decay.append((horizon, float(daily_ic.dropna().mean()) if not daily_ic.dropna().empty else 0.0))
    return decay


def compute_fama_macbeth_tstat(signals: pd.DataFrame, forward_returns: pd.DataFrame, min_stocks: int = 20) -> float:
    betas = []
    common_dates = signals.index.intersection(forward_returns.index)
    for date in common_dates:
        frame = pd.DataFrame({"signal": signals.loc[date], "return": forward_returns.loc[date]}).dropna()
        if len(frame) < min_stocks:
            continue
        x = np.column_stack([np.ones(len(frame)), frame["signal"].values])
        beta, _, _, _ = np.linalg.lstsq(x, frame["return"].values, rcond=None)
        betas.append(beta[1])
    if len(betas) < 100:
        return 0.0
    beta_series = pd.Series(betas)
    se = beta_series.std() / np.sqrt(len(beta_series))
    if se == 0 or pd.isna(se):
        return 0.0
    return float(beta_series.mean() / se)
```

- [ ] **Step 6: Upgrade `signal_library.py` for multi-symbol frames**

Preserve the existing single-symbol behavior. Add a path for frames with `symbol` and `date` columns:

```python
def compute_signal(formula_key: str, data: Any, lookback: int = 20) -> Any:
    frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    if {"symbol", "date"}.issubset(frame.columns):
        pieces = []
        for symbol, group in frame.sort_values(["symbol", "date"]).groupby("symbol"):
            signal = _compute_single(formula_key, group.set_index("date"), lookback)
            if signal is not None:
                pieces.append(pd.DataFrame({"date": signal.index, "symbol": symbol, "signal": signal.values}))
        if not pieces:
            return None
        return pd.concat(pieces, ignore_index=True).pivot(index="date", columns="symbol", values="signal").sort_index()
    return _compute_single(formula_key, data, lookback)
```

Implement `_compute_single(formula_key, data, lookback)` using the existing formula bodies.

- [ ] **Step 7: Make DuckDB market data market-aware**

Modify `DuckDBResearchMarketData`:

```python
_TABLE_BY_MARKET = {"cn": "daily_cn", "hk": "daily_hk", "us": "daily_us"}


def get_universe_symbols(self, market: str) -> List[str]:
    table = _TABLE_BY_MARKET.get(str(market).lower())
    if table is None:
        return []
    try:
        import duckdb
        conn = duckdb.connect(self._db_path, read_only=True)
        rows = conn.execute(f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol").fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception as e:
        logger.warning(f"Universe fetch failed: {e}")
        return []
```

Update `get_daily_bars` to infer the table from the first requested symbol and query that table. Return `None` on failures, preserving the current graceful degradation.

- [ ] **Step 8: Rewrite `FactorValidator.validate()` around Module A flow**

Use `detect_market(spec.universe[0])`, `get_universe_symbols(market)`, multi-symbol `compute_signal()`, and a date by symbol close matrix:

```python
market = detect_market(spec.universe[0])
symbols = self._market_data.get_universe_symbols(market) or spec.universe
raw_data = self._market_data.get_daily_bars(symbols=symbols, start="2019-01-01", end="2024-12-31")
data = raw_data if isinstance(raw_data, pd.DataFrame) else pd.DataFrame(raw_data)
prices = data.pivot(index="date", columns="symbol", values="close").sort_index()
signals = compute_signal(spec.signal_formula_key, data, spec.lookback_days).shift(self._exec_lag)
forward = prices.pct_change(spec.horizon_days).shift(-spec.horizon_days - self._exec_lag)
daily_ic = compute_cross_sectional_ic(signals, forward, min_stocks=self._min_stocks)
valid_ic = daily_ic.dropna()
```

Return an error report when `len(valid_ic) < 100`. Populate:

- `rank_ic = valid_ic.mean()`
- `rank_ic_ir = compute_icir(valid_ic)`
- `ic_decay = compute_ic_decay(signals, prices, [1, 5, 10, 21], self._exec_lag, self._min_stocks)`
- `fama_macbeth_tstat = compute_fama_macbeth_tstat(signals, forward, self._min_stocks)`

- [ ] **Step 9: Verify Task 1**

Run:

```bash
python -m pytest quant/tests/test_research_validation.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 1**

```bash
git add quant/features/research/validation quant/features/research/models.py quant/domain/ports quant/infrastructure/research/market_data quant/tests/test_research_validation.py quant/tests/test_research_architecture_boundaries.py
git commit -m "feat(research): add cross-sectional validation engine"
```

---

### Task 2: Module B Multiple Testing and Factor Attribution

**Files:**
- Create: `quant/features/research/rigor/dsr.py`
- Create: `quant/infrastructure/research/factors/ff_downloader.py`
- Create: `quant/infrastructure/research/factors/cn_factor_builder.py`
- Modify: `quant/features/research/rigor/backtest_hub.py`
- Modify: `quant/infrastructure/research/factors/ff_factor_store.py`
- Modify: `quant/features/research/validation/ff_decomposition.py`
- Modify: `quant/features/research/validation/factor_validator.py`
- Test: `quant/tests/test_research_rigor.py`
- Test: `quant/tests/test_research_factors.py`

- [ ] **Step 1: Write failing DSR tests**

Add to `quant/tests/test_research_rigor.py`:

```python
def test_compute_dsr_returns_probability_for_positive_returns():
    from quant.features.research.rigor.dsr import compute_dsr

    returns = pd.Series([0.001] * 80 + [-0.0002] * 20)

    value = compute_dsr(returns, n_trials=10)

    assert value is not None
    assert 0.0 <= value <= 1.0


def test_walkforward_populates_deflated_sharpe_ratio_when_returns_exist():
    def fake_runner(strategy_id, request):
        return {"metrics": {"sharpe": 0.7}, "returns": [0.001] * 80 + [-0.0002] * 20}

    class FakeExperimentStore:
        def list_runs(self, strategy_id=None, limit=100):
            return [{"run_id": str(i)} for i in range(10)]

    hub = RigorHub(
        fake_runner,
        config={"purged_walkforward": {"train_window_days": 100, "test_window_days": 30, "step_days": 30, "purge_days": 5, "embargo_days": 10, "min_train_observations": 50}},
        experiment_store=FakeExperimentStore(),
    )

    result = hub.run_walkforward("s", ["SPY"], "2020-01-01", "2021-01-01")

    assert result.deflated_sharpe_ratio is not None
```

- [ ] **Step 2: Write failing factor tests**

Create `quant/tests/test_research_factors.py`:

```python
import pandas as pd
import pytest


def test_decompose_alpha_populates_regression_values():
    from quant.features.research.validation.ff_decomposition import decompose_alpha

    idx = pd.date_range("2020-01-01", periods=160, freq="B")
    factors = pd.DataFrame({"MKT": 0.001, "SMB": 0.0001, "HML": -0.0001, "RF": 0.0}, index=idx)
    returns = pd.Series(0.002, index=idx)

    result = decompose_alpha(returns, factors)

    assert result["alpha_monthly"] != 0.0
    assert result["tstat"] != 0.0
    assert 0.0 <= result["r2"] <= 1.0


def test_ff_factor_store_lists_known_factors(tmp_path):
    from quant.infrastructure.research.factors.ff_factor_store import FFFactorStore

    store = FFFactorStore(cache_dir=tmp_path)

    factors = store.list_factors()

    assert {f["name"] for f in factors} >= {"MKT", "SMB", "HML", "RF", "Mom"}
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
python -m pytest quant/tests/test_research_rigor.py quant/tests/test_research_factors.py -q
```

Expected: FAIL because DSR and factor implementations do not exist.

- [ ] **Step 4: Implement `dsr.py`**

Create `compute_dsr(returns, n_trials=1, risk_free_rate=0.0)`. Use `scipy.stats.norm.cdf` when available and `math.erf` fallback:

```python
def _normal_cdf(z: float) -> float:
    try:
        from scipy.stats import norm
        return float(norm.cdf(z))
    except Exception:
        import math
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
```

Return `None` for fewer than 30 observations, `0.0` for non-positive Sharpe, and a float in `[0.0, 1.0]` otherwise.

- [ ] **Step 5: Integrate DSR into `RigorHub`**

Store each runner response in the split:

```python
response = self._runner(strategy_id, request)
split["response"] = response
```

Extract returns:

```python
test_returns = []
for split in split_results:
    response = split.get("response", {})
    if isinstance(response, dict):
        test_returns.extend(response.get("returns", []))
n_trials = 1
if self._experiment_store is not None:
    n_trials = max(1, len(self._experiment_store.list_runs(limit=100)))
dsr_value = compute_dsr(test_returns, n_trials=n_trials) if test_returns else None
```

Pass `dsr_value` into `PurgedWalkForwardResult.deflated_sharpe_ratio`.

- [ ] **Step 6: Implement FF downloader and factor store**

Create `ff_downloader.py` with cached download functions for FF5 daily and momentum daily. `FFFactorStore.get_factors()` should read available cached Parquet files and return a date-indexed frame filtered by `start` and `end`. If cache is absent and download fails, return `None` and log a warning.

- [ ] **Step 7: Implement CN factor builder**

Create `cn_factor_builder.py` with `build_cn3_factors(db_path, start, end, cache_dir=None)`. Use DuckDB read-only, build `MKT`, `SMB`, and `HML` from `daily_cn`, cache to `cn3_daily.parquet`, and return `None` when the table is unavailable.

- [ ] **Step 8: Rewrite `decompose_alpha()`**

Accept `strategy_returns`, `factor_data`, and `risk_free=0.0`. Convert returns to a pandas Series, align with factor index, require at least 126 observations, run `np.linalg.lstsq`, and return:

```python
{
    "alpha_monthly": float(alpha_daily * 21),
    "tstat": float(alpha_tstat),
    "r2": float(r2),
}
```

On any failure, return zeros.

- [ ] **Step 9: Wire factor data into `FactorValidator`**

Extend constructor:

```python
def __init__(self, market_data_port: Any, config: Optional[Dict[str, Any]] = None, factor_data_port: Any = None):
    self._factor_data = factor_data_port
```

When `factor_data_port` is present, fetch factors for `["MKT", "SMB", "HML", "RMW", "CMA", "RF", "Mom"]`, derive a simple long-short strategy return series from signal top/bottom quantiles, call `decompose_alpha()`, and populate `ff_alpha_monthly`, `ff_alpha_tstat`, `ff_r2`. When absent, keep zeros and append `"factor_data_unavailable"` only if factor validation is enabled in config.

- [ ] **Step 10: Verify Task 2**

Run:

```bash
python -m pytest quant/tests/test_research_rigor.py quant/tests/test_research_factors.py quant/tests/test_research_validation.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 11: Commit Task 2**

```bash
git add quant/features/research/rigor/dsr.py quant/features/research/rigor/backtest_hub.py quant/infrastructure/research/factors quant/features/research/validation quant/tests/test_research_rigor.py quant/tests/test_research_factors.py quant/tests/test_research_validation.py
git commit -m "feat(research): add dsr and factor attribution"
```

---

### Task 3: Module C Regime Rigor, Capacity Gate, SSRN, IC Decay Warning

**Files:**
- Modify: `quant/features/research/rigor/regime_detector.py`
- Modify: `quant/features/research/rigor/backtest_hub.py`
- Modify: `quant/features/research/models.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/infrastructure/research/sources/ssrn_source.py`
- Modify: `quant/api/research_bp.py`
- Test: `quant/tests/test_research_rigor.py`
- Test: `quant/tests/test_research_discovery.py`
- Test: `quant/tests/test_research_pipeline.py`

- [ ] **Step 1: Write failing regime and capacity tests**

Add to `quant/tests/test_research_rigor.py`:

```python
def test_label_split_regime_bull_with_sma_data():
    from quant.features.research.rigor.regime_detector import label_split_regime

    idx = pd.date_range("2020-01-01", periods=260, freq="B")
    data = pd.DataFrame({"close": range(100, 360)}, index=idx)
    split = {"test_start": 220, "test_end": 250}

    assert label_split_regime(split, data) == "bull"


def test_compute_regime_breakdown_sets_bull_only_warning():
    from quant.features.research.rigor.regime_detector import compute_regime_breakdown

    splits = [
        {"regime": "bull", "test_sharpe": 1.2},
        {"regime": "bear", "test_sharpe": -0.8},
    ]

    breakdown = compute_regime_breakdown(splits)

    assert breakdown["bull"]["n_splits"] == 1
    assert breakdown["bear"]["sharpe"] == pytest.approx(-0.8)


def test_capacity_gate_fails_when_trade_exceeds_adv():
    def fake_runner(strategy_id, request):
        return {
            "metrics": {"sharpe": 1.0},
            "trades": [{"trade_value": 1_000_000, "avg_daily_volume": 100_000, "price": 100.0}],
        }

    hub = RigorHub(fake_runner, config={
        "purged_walkforward": {"train_window_days": 100, "test_window_days": 30, "step_days": 30, "purge_days": 5, "embargo_days": 10, "min_train_observations": 50},
        "thresholds": {"min_worst_oos_sharpe": 0.3, "min_profitable_splits_pct": 0.5},
        "cost_model": {"max_adv_pct": 0.05},
    })

    result = hub.run_walkforward("s", ["SPY"], "2020-01-01", "2021-01-01")

    assert result.is_viable is False
```

- [ ] **Step 2: Write failing IC decay warning test**

Add a pipeline test with a fake validator:

```python
def test_ic_decay_warning_is_logged_without_rejecting(tmp_path):
    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id, status="validated",
                rank_ic=0.05, rank_ic_ir=1.0, ic_decay=[(1, 0.05), (5, 0.03), (10, 0.02), (21, 0.01)],
                fdr_adjusted_p=0.01, fdr_significant=True,
                ff_alpha_monthly=0.0, ff_alpha_tstat=0.0, ff_r2=0.0,
                long_short_spread=0.0, hit_rate=0.55, data_start="2020-01-01", data_end="2020-12-31",
                n_observations=120,
            )
```

Assert the resulting log has `phase="validation"`, `verdict="warn"`, and `"high_ic_decay"` in the warning reason or scores.

- [ ] **Step 3: Implement regime helpers**

Add to `regime_detector.py`:

```python
def benchmark_symbol_for_universe(symbols: List[str]) -> str:
    first = str(symbols[0]).upper() if symbols else ""
    if first.endswith(".HK") or first == "HSI":
        return "HSI"
    if first.isdigit():
        return "000300"
    return "SPY"
```

Add `label_split_regime(split, benchmark_data, sma_lookback=200)` and `compute_regime_breakdown(splits)` as specified. Use numeric split offsets when the split uses integer indices and date slicing when it uses date strings.

- [ ] **Step 4: Add regime fields to `PurgedWalkForwardResult`**

Modify `models.py`:

```python
regime_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)
bull_only_warning: bool = False
```

- [ ] **Step 5: Integrate capacity and regime labels in `RigorHub`**

Add `benchmark_data: Any = None` to `run_walkforward()`. Store runner response in each split. Add `_check_capacity(split_results)`:

```python
def _check_capacity(self, split_results: List[Dict[str, Any]]) -> bool:
    saw_trades = False
    for split in split_results:
        response = split.get("response", {})
        trades = response.get("trades", []) if isinstance(response, dict) else []
        for trade in trades:
            saw_trades = True
            cost = estimate_costs(
                trade_value=float(trade.get("trade_value", 0.0)),
                avg_daily_volume=float(trade.get("avg_daily_volume", 0.0)),
                price=float(trade.get("price", 100.0)),
                volatility=float(trade.get("volatility", 0.2)),
                config=self._config.get("cost_model", {}),
            )
            if not cost.capacity_ok:
                return False
    return True if saw_trades else True
```

Include `_check_capacity(split_results)` in `is_viable`.

- [ ] **Step 6: Implement SSRN source adapter**

Modify `SSRNSource` with constructor args `rate_limiter=None`, `_base_url`, `_timeout`, `_max_retries`, and `_retry_backoff`. Use `requests.get()` with User-Agent `QuantResearchBot/1.0 (academic use)`. Parse result HTML with stdlib `html.parser`. Return dicts with keys:

```python
{"title": title, "description": description[:500], "source": "ssrn", "source_url": url, "authors": authors, "published_date": published_date}
```

On any exception or blocked page, log warning and return `[]`.

- [ ] **Step 7: Add IC decay warning gate in `ResearchEngine`**

After validation passes but before integration:

```python
decay_values = [v for _, v in vreport.ic_decay] if vreport.ic_decay and isinstance(vreport.ic_decay[0], tuple) else list(vreport.ic_decay)
if len(decay_values) >= 4 and decay_values[0] != 0:
    remaining = abs(decay_values[3] / decay_values[0])
    if remaining < 0.5:
        from dataclasses import replace
        vreport = replace(vreport, errors=list(vreport.errors) + ["high_ic_decay"])
        result.log.append(ResearchLogEntry(
            phase="validation", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="warn",
            reason=f"IC decay: {decay_values[0]:.4f} -> {decay_values[3]:.4f} ({remaining:.0%} remaining)",
            scores={"ic_1d": decay_values[0], "ic_21d": decay_values[3]},
        ))
```

This warning must not reject or skip the strategy.

- [ ] **Step 8: Wire optional benchmark data through API composition**

Add optional `benchmark_data=None` argument to `_run_backtests()`. Keep default `None` for backward compatibility. In API composition, load benchmark bars only when a research market data adapter is configured and pass them to `RigorHub.run_walkforward()`.

- [ ] **Step 9: Verify Task 3**

Run:

```bash
python -m pytest quant/tests/test_research_rigor.py quant/tests/test_research_discovery.py quant/tests/test_research_pipeline.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 3**

```bash
git add quant/features/research/rigor quant/features/research/models.py quant/features/research/research_engine.py quant/infrastructure/research/sources/ssrn_source.py quant/api/research_bp.py quant/tests/test_research_rigor.py quant/tests/test_research_discovery.py quant/tests/test_research_pipeline.py
git commit -m "feat(research): add regime and capacity rigor gates"
```

---

### Task 4: Module D Production Hardening, Opt-In

**Files:**
- Create: `quant/features/research/validation/sensitivity.py`
- Create: `quant/domain/ports/pit_data.py`
- Create: `quant/infrastructure/research/pit_duckdb.py`
- Create: `quant/scripts/build_pit_universe.py`
- Modify: `quant/features/research/validation/factor_validator.py`
- Modify: `quant/features/research/ensemble/optimizer.py`
- Modify: `quant/features/research/config/research.yaml`
- Test: `quant/tests/test_research_production_hardening.py`
- Test: `quant/tests/test_research_ensemble.py`

- [ ] **Step 1: Write failing sensitivity and PIT tests**

Create `quant/tests/test_research_production_hardening.py`:

```python
def test_sensitivity_report_flags_unstable_params():
    from quant.features.research.validation.sensitivity import SensitivityReport

    report = SensitivityReport(
        strategy_id="s",
        base_ic=0.04,
        base_params={"lookback_days": 20, "horizon_days": 5},
        parameter_combinations=[{"lookback_days": 10, "horizon_days": 1}, {"lookback_days": 30, "horizon_days": 21}],
        ic_surface=[0.04, 0.01],
        is_stable=False,
        max_degradation_pct=75.0,
        optimal_params={"lookback_days": 10, "horizon_days": 1},
    )

    assert report.is_stable is False


def test_pit_port_signature_imports_without_external_deps():
    from quant.domain.ports.pit_data import PITData

    assert PITData is not None
```

- [ ] **Step 2: Write failing ERC tests**

Extend `quant/tests/test_research_ensemble.py`:

```python
def test_equal_risk_contributions_are_close_for_identity_covariance():
    from quant.features.research.ensemble.optimizer import equal_risk

    weights = equal_risk([[1.0, 0.0], [0.0, 1.0]], [0.2, 0.2], max_weight=0.8)

    assert weights[0] == pytest.approx(weights[1], abs=0.02)
    assert sum(weights) == pytest.approx(1.0)
```

- [ ] **Step 3: Add `SensitivityReport` and `run_sensitivity_sweep()`**

Create `sensitivity.py` with:

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

`run_sensitivity_sweep(spec, market_data_port, base_params, config)` should use `lookback_grid`, `horizon_grid`, and Module A cross-sectional functions. Return a stable report when `max_degradation_pct <= config.get("sensitivity_max_degradation_pct", 30)`.

- [ ] **Step 4: Add PIT port and DuckDB adapter**

Create `quant/domain/ports/pit_data.py`:

```python
class PITData(ABC):
    @abstractmethod
    def get_universe(self, as_of_date: str, market: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_bars_pit(self, symbols: List[str], start: str, end: str, as_of_date: str) -> Any:
        raise NotImplementedError
```

Create `quant/infrastructure/research/pit_duckdb.py` implementing `PITData`. If listing or delisting columns do not exist, return the all-symbol universe and log a warning.

- [ ] **Step 5: Add universe snapshot script**

Create `quant/scripts/build_pit_universe.py`. It should read DuckDB tables `daily_cn`, `daily_us`, and `daily_hk` when present, infer `listing_date` and `delisting_date` from first and last bar dates, and write monthly Parquet snapshots under `quant/infrastructure/var/research/universe_snapshots/`.

- [ ] **Step 6: Upgrade ERC optimizer**

Modify `equal_risk()` to build a covariance matrix from `corr_matrix` and `volatilities`, then solve ERC with `scipy.optimize.minimize`. If scipy is unavailable or the optimizer fails, fall back to current inverse-vol-with-cap behavior.

- [ ] **Step 7: Wire opt-in config**

Modify `ResearchConfig` and `quant/features/research/config/research.yaml`:

```yaml
validation:
  sensitivity_enabled: false
  sensitivity_lookback_grid: [10, 15, 20, 25, 30]
  sensitivity_horizon_grid: [1, 3, 5, 10, 21]
  sensitivity_max_degradation_pct: 30

pit:
  enabled: false
  universe_snapshot_dir: quant/infrastructure/var/research/universe_snapshots
```

Default behavior must remain identical when both sections are disabled.

- [ ] **Step 8: Verify Task 4**

Run:

```bash
python -m pytest quant/tests/test_research_production_hardening.py quant/tests/test_research_ensemble.py quant/tests/test_research_validation.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add quant/features/research/validation/sensitivity.py quant/domain/ports/pit_data.py quant/domain/ports/__init__.py quant/infrastructure/research/pit_duckdb.py quant/scripts/build_pit_universe.py quant/features/research/ensemble/optimizer.py quant/features/research/config/research.yaml quant/tests/test_research_production_hardening.py quant/tests/test_research_ensemble.py
git commit -m "feat(research): add production hardening controls"
```

---

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `quant/features/research/AGENTS.md`
- Modify: `quant/domain/AGENTS.md`
- Modify: `quant/infrastructure/AGENTS.md`
- Modify: `docs/AGENTS.md`

- [ ] **Step 1: Update module contracts**

Update only affected sections:

- Research L1: add cross-sectional validation, DSR, factor attribution, regime labels, sensitivity, and ERC responsibilities.
- Domain L1: add `ResearchMarketData.get_universe_symbols()` and `PITData`.
- Infrastructure L1: add market-aware DuckDB data, factor cache, SSRN source, PIT DuckDB adapter.
- Docs index: add the four 2026-05-08 specs and this plan to active specs/plans.

- [ ] **Step 2: Run focused research tests**

Run:

```bash
python -m pytest quant/tests/test_research_validation.py quant/tests/test_research_factors.py quant/tests/test_research_rigor.py quant/tests/test_research_discovery.py quant/tests/test_research_ensemble.py quant/tests/test_research_production_hardening.py quant/tests/test_research_pipeline.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
python -m pytest quant/tests/ -q
```

Expected: PASS. If there are pre-existing unrelated failures, capture the exact failing test names and output.

- [ ] **Step 4: Run encoding check**

Run:

```bash
python scripts/check_text_encoding.py
```

Expected: PASS.

- [ ] **Step 5: Commit documentation**

```bash
git add quant/features/research/AGENTS.md quant/domain/AGENTS.md quant/infrastructure/AGENTS.md docs/AGENTS.md
git commit -m "docs(research): document rigor hardening modules"
```

## Execution Notes

- Execute Task 1 first. It unlocks IC decay, Fama-MacBeth t-stat, sensitivity sweep, and the IC decay warning gate.
- Task 2 and the SSRN portion of Task 3 can be split across workers after Task 1 starts, because they touch disjoint infrastructure files.
- Task 4 must remain opt-in. Production behavior should be unchanged when `sensitivity_enabled=false` and `pit.enabled=false`.
- Do not add new dependencies unless an existing requirement is already present. `scipy` is already imported in the current validation code; if an environment lacks it, use fallback behavior where specified.
- Keep all new report construction keyword-based because `ValidationReport` is frozen and now has an additive field before `errors`.

## Self-Review Checklist

- [ ] Each 2026-05-08 spec has at least one implementation task.
- [ ] Module A lands before B/C/D consumers rely on it.
- [ ] Feature code imports no `quant.infrastructure.*`.
- [ ] Infrastructure code imports no `quant.features.*`.
- [ ] Domain ports use `Any`, `List`, and `Dict`, never pandas/numpy/DuckDB/requests types.
- [ ] All new production-hardening behavior is disabled or graceful by default.
- [ ] SSRN errors never break discovery.
- [ ] Full research focused tests, full test suite, and encoding check have been run before final completion.
