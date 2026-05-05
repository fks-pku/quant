# Benchmark Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire existing benchmark infrastructure into the API → frontend flow so backtest results include CN market benchmark (510300) comparison.

**Architecture:** Two-file change. API layer auto-detects CN market and fetches 510300 data, creates `BenchmarkProvider`, passes to `Backtester`, serializes benchmark equity curve + metrics. Frontend overlays dashed benchmark line on equity chart and adds Alpha/Beta/IR metric cards.

**Tech Stack:** Python 3.10+, DuckDB, React, SVG inline charts

---

## File Structure

```
quant/
├── api/backtest_bp.py                     MODIFY (+35 lines)
│   ├── detect_benchmark_symbol()          NEW function (above _run)
│   ├── benchmark data fetch               NEW inside DuckDB session
│   ├── Backtester(benchmark_provider=...) MODIFY line 131
│   └── benchmark response fields          NEW in metrics + equity
├── frontend/src/BacktestDashboard.js      MODIFY (+60 lines)
│   ├── EquityChart(benchmarkCurve prop)   MODIFY dual-line chart
│   └── benchmark metrics row              NEW conditional row
```

**No new files.** No changes to benchmark.py, engine.py, analytics.py, entities.py.

---

### Task 1: API Layer — Detect benchmark symbol and fetch data

**Files:**
- Modify: `quant/api/backtest_bp.py:29-129` (inside `_run()` before Backtester creation)

- [ ] **Step 1: Add `detect_benchmark_symbol` helper inside `_run()`**

Insert after line 29 (`def _run():`) and before the `try:` block at line 30:

```python
            def detect_benchmark_symbol(symbols):
                from quant.domain.models.market import is_cn_symbol
                if any(is_cn_symbol(s) for s in symbols):
                    return "510300"
                return None
```

- [ ] **Step 2: Initialize `bench_provider = None` before the inner try block**

At line 31 (`import pandas as pd`), add right before it:

```python
            bench_provider = None
            benchmark_symbol = detect_benchmark_symbol(symbols)
```

- [ ] **Step 3: Fetch benchmark bars from DuckDB after strategy creation**

After line 108 (`strategy = strategy_class(**strategy_kwargs)`) and before line 110 (`config = {`), insert:

```python

                if benchmark_symbol:
                    bench_df = db.get_bars(
                        benchmark_symbol,
                        datetime.strptime(start_date, '%Y-%m-%d'),
                        datetime.strptime(end_date, '%Y-%m-%d'),
                        "1d",
                    )
                    if not bench_df.empty:
                        from quant.features.backtest.benchmark import BenchmarkProvider
                        bench_provider = BenchmarkProvider(bench_df)
```

- [ ] **Step 4: Pass `bench_provider` to Backtester constructor**

Change line 131 from:
```python
            backtester = Backtester(config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio, lot_sizes=lot_sizes)
```
To:
```python
            backtester = Backtester(config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio, lot_sizes=lot_sizes, benchmark_provider=bench_provider)
```

- [ ] **Step 5: Serialize benchmark equity curve**

After line 142 (`equity_serializable = [[str(r[0]), float(r[1])] for r in equity_list]`), insert:

```python

            benchmark_curve = []
            if bench_provider is not None:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                bench_eq = bench_provider.get_benchmark_equity(start_dt, end_dt, initial_cash)
                if not bench_eq.empty:
                    benchmark_curve = [[str(idx), float(v)] for idx, v in bench_eq.items()]
```

- [ ] **Step 6: Add benchmark metrics to response metrics dict**

After line 213 (`"avg_loss": float(sum(t.pnl for t in losing) / max(1, len(losing))),`) and before the closing `}` on line 214, insert:

```python
                "benchmark_return": float(result.metrics.benchmark_return) if result.metrics.benchmark_return is not None else None,
                "benchmark_return_pct": float(result.metrics.benchmark_return * 100) if result.metrics.benchmark_return is not None else None,
                "alpha": float(result.metrics.alpha) if result.metrics.alpha is not None else None,
                "alpha_pct": float(result.metrics.alpha * 100) if result.metrics.alpha is not None else None,
                "beta": float(result.metrics.beta) if result.metrics.beta is not None else None,
                "information_ratio": float(result.metrics.information_ratio) if result.metrics.information_ratio is not None else None,
```

- [ ] **Step 7: Add benchmark_equity_curve to response body**

Change the response dict at lines 217-226. After `"equity_curve": equity_serializable,` (line 222), add:

```python
                    "benchmark_equity_curve": benchmark_curve,
```

- [ ] **Step 8: Commit Task 1**

```
git add quant/api/backtest_bp.py
git commit -m "feat: wire benchmark provider into API backtest endpoint"
```

---

### Task 2: Frontend — Dual-line equity chart with benchmark

**Files:**
- Modify: `quant/frontend/src/BacktestDashboard.js:29-89` (EquityChart component)

- [ ] **Step 1: Update `EquityChart` to accept `benchmarkCurve` prop and render second line**

Replace the entire `EquityChart` function (lines 29-89) with:

```jsx
function EquityChart({ curve, benchmarkCurve, currency = false }) {
  if (!curve || curve.length < 2) return null;

  const W = 700;
  const H = 200;
  const padL = 60;
  const padR = 20;
  const padT = 20;
  const padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const values = curve.map(([, v]) => v);
  let allValues = [...values];
  if (benchmarkCurve && benchmarkCurve.length >= 2) {
    allValues = allValues.concat(benchmarkCurve.map(([, v]) => v));
  }
  const minV = Math.min(...allValues);
  const maxV = Math.max(...allValues);
  const rangeV = maxV - minV || 1;

  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (v) => padT + plotH - ((v - minV) / rangeV) * plotH;

  const points = curve.map(([d, v], i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const areaPoints = `${scaleX(0)},${padT + plotH} ${points} ${scaleX(curve.length - 1)},${padT + plotH}`;

  let benchPoints = '';
  if (benchmarkCurve && benchmarkCurve.length >= 2) {
    benchPoints = benchmarkCurve.map(([d, v], i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  }

  const gridLines = 5;
  const gridYVals = Array.from({ length: gridLines }, (_, i) => minV + (rangeV * i) / (gridLines - 1));

  const dateLabels = [];
  const mid = Math.floor(curve.length / 2);
  [0, mid, curve.length - 1].forEach((idx) => {
    dateLabels.push({ x: scaleX(idx), label: curve[idx][0].slice(0, 10) });
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '200px' }}>
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00d4ff" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {gridYVals.map((v, i) => {
        const y = scaleY(v);
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#333355" strokeWidth="0.5" />
            <text x={padL - 6} y={y + 3} textAnchor="end" fill="#666680" fontSize="9">
              {fmtCurrency(v, currency)}
            </text>
          </g>
        );
      })}
      <polygon points={areaPoints} fill="url(#eqGrad)" />
      <polyline points={points} fill="none" stroke="#00d4ff" strokeWidth="1.5" />
      {benchPoints && (
        <polyline points={benchPoints} fill="none" stroke="#888899" strokeWidth="1.2" strokeDasharray="5,3" />
      )}
      {dateLabels.map((dl, i) => (
        <text key={i} x={dl.x} y={H - 6} textAnchor="middle" fill="#666680" fontSize="9">
          {dl.label}
        </text>
      ))}
      {benchPoints && (
        <g transform={`translate(${padL + 4}, ${padT + 12})`}>
          <line x1={0} y1={0} x2={12} y2={0} stroke="#00d4ff" strokeWidth="1.5" />
          <text x={16} y={3} fill="#00d4ff" fontSize="9">Strategy</text>
          <line x1={64} y1={0} x2={76} y2={0} stroke="#888899" strokeWidth="1.2" strokeDasharray="5,3" />
          <text x={80} y={3} fill="#888899" fontSize="9">Benchmark</text>
        </g>
      )}
    </svg>
  );
}
```

- [ ] **Step 2: Update the EquityChart call site to pass benchmark curve**

Find line 434 where `<EquityChart curve={result.equity_curve} currency={currency} />` is rendered, and change to:

```jsx
            <EquityChart curve={result.equity_curve} benchmarkCurve={result.benchmark_equity_curve} currency={currency} />
```

- [ ] **Step 3: Commit Task 2**

```
git add quant/frontend/src/BacktestDashboard.js
git commit -m "feat: dual-line equity chart with benchmark curve"
```

---

### Task 3: Frontend — Benchmark metrics card row

**Files:**
- Modify: `quant/frontend/src/BacktestDashboard.js:412-430` (metrics row)

- [ ] **Step 1: Add benchmark metrics row after existing 8 metrics**

After line 430 (the `</div>` closing the metrics row), insert a second metrics row:

```jsx
          {result.metrics.alpha != null && (
            <div className="bt-metrics" style={{ marginTop: 12, borderTop: '1px solid #252540', paddingTop: 12 }}>
              <div className="bt-metric">
                <div className="bt-metric-value" style={{ color: colorPnl(result.metrics.alpha_pct) }}>{fmtPct(result.metrics.alpha_pct)}</div>
                <div className="bt-metric-label">Alpha</div>
              </div>
              <div className="bt-metric">
                <div className="bt-metric-value">{result.metrics.beta != null ? result.metrics.beta.toFixed(2) : '\u2014'}</div>
                <div className="bt-metric-label">Beta</div>
              </div>
              <div className="bt-metric">
                <div className="bt-metric-value">{result.metrics.information_ratio != null ? result.metrics.information_ratio.toFixed(2) : '\u2014'}</div>
                <div className="bt-metric-label">Info Ratio</div>
              </div>
              <div className="bt-metric">
                <div className="bt-metric-value" style={{ color: colorPnl(result.metrics.benchmark_return_pct) }}>{fmtPct(result.metrics.benchmark_return_pct)}</div>
                <div className="bt-metric-label">Bench Return</div>
              </div>
            </div>
          )}
```

- [ ] **Step 2: Commit Task 3**

```
git add quant/frontend/src/BacktestDashboard.js
git commit -m "feat: add benchmark alpha/beta/IR metrics to backtest results"
```

---

### Task 4: Verification — Run backtest and inspect

- [ ] **Step 1: Start API server**

```bash
python quant/api_server.py
```

Wait for "Server started on port 5000".

- [ ] **Step 2: Run a CN-market backtest via curl**

In a second terminal:

```bash
curl -X POST http://localhost:5000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"strategy_id":"SimpleMomentum","start_date":"2024-01-01","end_date":"2024-12-31","symbols":["600519","000858"],"initial_cash":100000}'
```

Note the `backtest_id` returned.

- [ ] **Step 3: Poll the result and check benchmark fields**

```bash
curl http://localhost:5000/api/backtest/result/<backtest_id> | python -m json.tool
```

Verify the response contains:
- `"benchmark_equity_curve"` — non-empty array of `[date, value]` pairs
- `"metrics"` has `"alpha"`, `"beta"`, `"information_ratio"`, `"benchmark_return"`, `"benchmark_return_pct"` with non-null numeric values

- [ ] **Step 4: Open frontend in browser and verify visually**

Navigate to `http://localhost:3000` (or wherever the frontend is served). Run a backtest with CN symbols. Verify:
- Equity chart shows two lines: cyan (Strategy) and gray dashed (Benchmark) with legend
- Benchmark metrics row appears below main metrics with Alpha, Beta, Info Ratio, Bench Return

- [ ] **Step 5: Test US-market backtest (no benchmark)**

```bash
curl -X POST http://localhost:5000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"strategy_id":"SimpleMomentum","start_date":"2024-01-01","end_date":"2024-12-31","symbols":["AAPL"],"initial_cash":100000}'
```

Poll result and verify:
- `"benchmark_equity_curve"` is `[]`
- `"alpha"`, `"beta"` etc are `null`
- Frontend does NOT show benchmark line or metrics row

- [ ] **Step 6: Commit verification notes (if any)**

No code changes expected in this step.

---

### Task 5: Final Integration Test

- [ ] **Step 1: Run existing backtest tests to ensure no regressions**

```bash
python -m pytest quant/tests/test_backtest_fuzz.py -q --timeout=120
```

All tests must pass.

- [ ] **Step 2: Run all tests**

```bash
python -m pytest quant/tests/ -q --timeout=120
```

All tests must pass. Any failures must be addressed before declaring completion.
