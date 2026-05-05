# Benchmark Reference Integration Design

## Context

The backtest system already has complete benchmark infrastructure (`BenchmarkProvider`, engine integration, `PerformanceMetrics` fields) but it is never wired into the API → frontend flow. Users have no way to compare strategy performance against a market benchmark.

## Goal

Automatically add a market-appropriate benchmark (510300 for CN markets) to every backtest, show benchmark equity curve overlaid on strategy equity, and display alpha/beta/information-ratio metrics in the frontend.

## Architecture

### Data Flow

```
API (backtest_bp.py)
  ├── detect_benchmark_symbol(symbols) → "510300" | None
  ├── db.get_bars("510300", start, end) → benchmark DataFrame
  ├── BenchmarkProvider(bench_df)
  ├── Backtester(benchmark_provider=...)
  │   └── engine.py → get_benchmark_returns() → calculate_performance_metrics()
  ├── bench_provider.get_benchmark_equity(start, end, initial_cash) → equity Series
  └── serialize to JSON response
        ├── metrics: +benchmark_return, +alpha, +beta, +information_ratio
        └── benchmark_equity_curve: [[date, value], ...]

Frontend (BacktestDashboard.js)
  ├── EquityChart receives optional benchmarkCurve prop
  │   └── second polyline (dashed gray) overlaid on same axes
  └── new benchmark metrics row (Alpha, Beta, IR)
```

### No Changes To

- `features/backtest/benchmark.py` — `get_benchmark_equity()` already exists and is correct
- `features/backtest/engine.py` — already accepts `benchmark_provider`, calls `get_benchmark_returns()`, passes to metrics
- `features/backtest/analytics.py` — `PerformanceMetrics` already stores all benchmark fields
- `features/backtest/entities.py` — no new fields needed

## Detailed Design

### 1. API Layer (`api/backtest_bp.py`)

**Market → benchmark symbol mapping:**

```python
def detect_benchmark_symbol(symbols):
    from quant.domain.models.market import is_cn_symbol
    if any(is_cn_symbol(s) for s in symbols):
        return "510300"
    return None
```

**Benchmark data fetch (inside existing DuckDB session, lines 70-128):**

After strategy symbol data is loaded and validated, before `Backtester` creation:

1. Call `detect_benchmark_symbol(symbols)`
2. If `benchmark_symbol` is not None:
   - `bench_df = db.get_bars(benchmark_symbol, start, end, "1d")`
   - If not empty: `bench_provider = BenchmarkProvider(bench_df)`
   - If empty: `bench_provider = None` (silent skip)
3. Pass to Backtester: `Backtester(..., benchmark_provider=bench_provider)`

**Response serialization (after line 213):**

Extend the `metrics` dict:
```python
"benchmark_return": float(result.metrics.benchmark_return) if result.metrics.benchmark_return is not None else None,
"benchmark_return_pct": float(result.metrics.benchmark_return * 100) if result.metrics.benchmark_return is not None else None,
"alpha": float(result.metrics.alpha) if result.metrics.alpha is not None else None,
"beta": float(result.metrics.beta) if result.metrics.beta is not None else None,
"information_ratio": float(result.metrics.information_ratio) if result.metrics.information_ratio is not None else None,
```

Add benchmark equity curve (top-level, parallel to `equity_curve`):
```python
if bench_provider is not None:
    bench_eq = bench_provider.get_benchmark_equity(start, end, initial_cash)
    benchmark_curve = [[str(idx), float(v)] for idx, v in bench_eq.items()]
else:
    benchmark_curve = []
```

### 2. Frontend (`BacktestDashboard.js`)

**`EquityChart` — dual-line rendering:**

Accept new prop `benchmarkCurve` (same format as `curve`). When present:
- Render second `<polyline>` with `stroke="#888899" strokeDasharray="4,3" strokeWidth="1"`
- Share the same Y-axis scale and grid — the benchmark starts at the same initial_cash
- Add a small legend: "Strategy" (cyan) / "Benchmark" (gray) in top-left

**Benchmark metrics row:**

After the existing 8 metrics, add a second row conditioned on `result.metrics.alpha != null`:

| Label | Value | Source |
|-------|-------|--------|
| Alpha | `(metrics.alpha * 100).toFixed(2) + '%'` | annualized excess |
| Beta | `metrics.beta.toFixed(2)` | OLS beta |
| Info Ratio | `metrics.information_ratio.toFixed(2)` | alpha / tracking error |
| Bench Return | `fmtPct(metrics.benchmark_return_pct)` | benchmark annualized |

**Edge cases:**
- If `benchmarkCurve` is empty/null → render single-line chart (existing behavior)
- If `alpha` is null → skip benchmark metrics row entirely
- Benchmark curve may have slightly different dates → align by index using the existing `scaleX` point count

## Scope

| File | Lines | Type |
|------|-------|------|
| `api/backtest_bp.py` | +40 | New logic |
| `frontend/src/BacktestDashboard.js` | +60 | Enhanced component |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| 510300 data not in DuckDB | Silent skip, no benchmark shown. Error not propagated. |
| Benchmark dates don't align with strategy | `calculate_performance_metrics` does `dropna()` on aligned data |
| `BenchmarkProvider.__init__` raises on empty data | API layer catches and sets `bench_provider = None` |
| Frontend renders garbage on null metrics | `alpha != null` guard before benchmark metrics row |
