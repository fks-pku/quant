# Backtest Open Positions Display — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show open (unsold) positions alongside closed trades in the frontend backtest dashboard.

**Architecture:** Engine extracts open positions from Portfolio at end of backtest run, API merges them with SELL trades into a unified list with a `status` field, frontend renders both in a single table with visual distinction.

**Tech Stack:** Python dataclass (engine), Flask serialization (API), React table rendering (frontend)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `quant/features/backtest/engine.py` | Modify | Add `open_positions` to BacktestResult; extract at end of `run()` |
| `quant/api/backtest_bp.py` | Modify | Serialize open positions with status field; merge into trades list |
| `quant/frontend/src/BacktestDashboard.js` | Modify | Add Status column; style open position rows |
| `quant/frontend/src/App.css` | Modify | Add CSS for open position row styling |
| `quant/tests/test_backtester_fixed.py` | Modify | Add test for open_positions extraction |

---

### Task 1: Engine — Add open_positions to BacktestResult

**Files:**
- Modify: `quant/features/backtest/engine.py:55-69` (BacktestResult dataclass)
- Modify: `quant/features/backtest/engine.py:283-302` (run() method return block)
- Modify: `quant/tests/test_backtester_fixed.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `quant/tests/test_backtester_fixed.py`:

```python
class TestOpenPositionsExtracted:
    def test_open_positions_in_result(self):
        bt = Backtester(config={"backtest": {"slippage_bps": 0}, "execution": {}})

        dates = [datetime(2024, 1, d) for d in range(1, 11)]
        prices = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        df = pd.DataFrame({
            "symbol": ["AAPL"] * 10,
            "date": dates,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000000] * 10,
        })

        from quant.features.backtest.engine import DataFrameProvider
        dp = DataFrameProvider(df)

        from quant.features.strategies.momentum.strategy import SimpleMomentum
        strat = SimpleMomentum(lookback_days=3)

        result = bt.run(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 10),
            strategies=[strat],
            initial_cash=100000,
            data_provider=dp,
            symbols=["AAPL"],
        )

        assert hasattr(result, 'open_positions')
        assert isinstance(result.open_positions, list)
        for pos in result.open_positions:
            assert "symbol" in pos
            assert "quantity" in pos
            assert "entry_price" in pos
            assert "entry_time" in pos
            assert "current_price" in pos
            assert "unrealized_pnl" in pos
            assert "market_value" in pos
            assert pos["quantity"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest quant/tests/test_backtester_fixed.py::TestOpenPositionsExtracted -v`
Expected: FAIL — `open_positions` attribute does not exist

- [ ] **Step 3: Add open_positions field to BacktestResult**

In `quant/features/backtest/engine.py`, add `open_positions` field to BacktestResult (line 69, after diagnostics):

```python
    diagnostics: BacktestDiagnostics = field(default_factory=BacktestDiagnostics)
    open_positions: List[Dict] = field(default_factory=list)
```

- [ ] **Step 4: Extract open positions at end of run()**

In `quant/features/backtest/engine.py`, insert open positions extraction between line 286 and 288 (after `diag.total_gross_pnl` assignment, before `return BacktestResult`):

```python
        diag.total_gross_pnl = sum(t.pnl for t in all_trades) + diag.total_commission

        open_positions = []
        for sym, pos in portfolio.positions.items():
            if pos.quantity > 0:
                last_price = last_prices.get(sym, pos.avg_cost)
                open_positions.append({
                    "symbol": sym,
                    "quantity": pos.quantity,
                    "entry_price": pos.avg_cost,
                    "entry_time": entry_times.get(sym),
                    "current_price": last_price,
                    "unrealized_pnl": (last_price - pos.avg_cost) * pos.quantity,
                    "market_value": pos.quantity * last_price,
                })

        return BacktestResult(
```

And add `open_positions=open_positions,` to the BacktestResult constructor call (after `diagnostics=diag`):

```python
            diagnostics=diag,
            open_positions=open_positions,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest quant/tests/test_backtester_fixed.py::TestOpenPositionsExtracted -v`
Expected: PASS

- [ ] **Step 6: Run all existing backtest tests**

Run: `python -m pytest quant/tests/test_backtester_fixed.py quant/tests/test_backtester_buy_trade.py -v`
Expected: ALL PASS (existing tests unaffected — `open_positions` defaults to empty list)

- [ ] **Step 7: Commit**

```bash
git add quant/features/backtest/engine.py quant/tests/test_backtester_fixed.py
git commit -m "feat(backtest): extract open positions from portfolio at end of run"
```

---

### Task 2: API — Serialize open positions into trades list

**Files:**
- Modify: `quant/api/backtest_bp.py:123-171` (trades serialization + result storage)

- [ ] **Step 1: Rewrite trades serialization to include open positions**

Replace lines 123-136 in `quant/api/backtest_bp.py`:

Old:
```python
            trades_list = []
            for t in result.trades:
                if t.side != "SELL":
                    continue
                trades_list.append({
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "quantity": int(t.quantity),
                    "pnl": float(t.pnl),
                })
```

New:
```python
            trades_list = []
            for t in result.trades:
                if t.side != "SELL":
                    continue
                trades_list.append({
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "symbol": t.symbol,
                    "side": "BUY",
                    "status": "closed",
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "quantity": int(t.quantity),
                    "pnl": float(t.pnl),
                })

            for pos in result.open_positions:
                trades_list.append({
                    "entry_time": str(pos["entry_time"]) if pos.get("entry_time") else "",
                    "exit_time": None,
                    "symbol": pos["symbol"],
                    "side": "BUY",
                    "status": "open",
                    "entry_price": float(pos["entry_price"]),
                    "exit_price": float(pos["current_price"]),
                    "quantity": int(pos["quantity"]),
                    "pnl": float(pos["unrealized_pnl"]),
                })
```

Note: SELL trades now have `"side": "BUY"` (because they represent round-trip long positions, the entry side is BUY) and `"status": "closed"`. Open positions have `"status": "open"` and `exit_time: null`.

- [ ] **Step 2: Verify API server starts**

Run: `python -c "from quant.api.backtest_bp import backtest_bp; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add quant/api/backtest_bp.py
git commit -m "feat(api): serialize open positions with status field in backtest results"
```

---

### Task 3: Frontend — Render trades table with open position support

**Files:**
- Modify: `quant/frontend/src/BacktestDashboard.js:390-434` (trades table)
- Modify: `quant/frontend/src/App.css:719-722` (add open position styles)

- [ ] **Step 1: Add CSS for open position rows**

In `quant/frontend/src/App.css`, after `.bt-trades-scroll` (line 722), add:

```css
.bt-trades-table tr.trade-open {
  background: rgba(59, 130, 246, 0.06);
  border-left: 3px solid #3b82f6;
}

.bt-trades-table tr.trade-open td:first-child {
  padding-left: 9px;
}

.bt-status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.bt-status-closed {
  background: rgba(34, 197, 94, 0.12);
  color: var(--accent-green);
}

.bt-status-open {
  background: rgba(59, 130, 246, 0.12);
  color: #3b82f6;
}

.bt-pnl-unrealized {
  font-style: italic;
}
```

- [ ] **Step 2: Rewrite trades table in BacktestDashboard.js**

Replace lines 390-434 in `quant/frontend/src/BacktestDashboard.js`:

Old:
```jsx
          {result.trades && result.trades.length > 0 && (
            <div className="bt-trades">
              <div className="bt-trades-title">Trades ({result.trades.length})</div>
              <div className="bt-trades-scroll">
                <table className="bt-trades-table">
                  <thead>
                    <tr>
                      <th>Entry Date</th>
                      <th>Exit Date</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Entry Price</th>
                      <th>Exit Price</th>
                      <th>P&L</th>
                      <th>Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => {
                      const entryD = new Date(t.entry_time);
                      const exitD = new Date(t.exit_time);
                      const durMs = exitD - entryD;
                      const durDays = Math.round(durMs / 86400000);
                      return (
                        <tr key={i}>
                          <td>{entryD.toLocaleDateString()}</td>
                          <td>{exitD.toLocaleDateString()}</td>
                          <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                          <td style={{ color: t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.side}</td>
                          <td>{t.quantity}</td>
                          <td>{fmtCurrency(t.entry_price, isHK)}</td>
                          <td>{fmtCurrency(t.exit_price, isHK)}</td>
                          <td style={{ color: colorPnl(t.pnl), fontWeight: 600 }}>
                            {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, isHK)}
                          </td>
                          <td>{durDays}d</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
```

New:
```jsx
          {result.trades && result.trades.length > 0 && (
            <div className="bt-trades">
              <div className="bt-trades-title">Trades ({result.trades.length})</div>
              <div className="bt-trades-scroll">
                <table className="bt-trades-table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Entry Date</th>
                      <th>Exit Date</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Entry Price</th>
                      <th>Exit Price</th>
                      <th>P&L</th>
                      <th>Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => {
                      const isOpen = t.status === 'open';
                      const entryD = new Date(t.entry_time);
                      const exitD = t.exit_time ? new Date(t.exit_time) : null;
                      const durMs = exitD ? exitD - entryD : 0;
                      const durDays = Math.round(durMs / 86400000);
                      return (
                        <tr key={i} className={isOpen ? 'trade-open' : ''}>
                          <td>
                            <span className={`bt-status-badge ${isOpen ? 'bt-status-open' : 'bt-status-closed'}`}>
                              {isOpen ? '持仓中' : '已完成'}
                            </span>
                          </td>
                          <td>{entryD.toLocaleDateString()}</td>
                          <td>{exitD ? exitD.toLocaleDateString() : '—'}</td>
                          <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                          <td style={{ color: 'var(--accent-green)', fontWeight: 600 }}>{t.side}</td>
                          <td>{t.quantity}</td>
                          <td>{fmtCurrency(t.entry_price, isHK)}</td>
                          <td style={isOpen ? { color: '#3b82f6' } : {}}>
                            {fmtCurrency(t.exit_price, isHK)}
                          </td>
                          <td className={isOpen ? 'bt-pnl-unrealized' : ''} style={{ color: isOpen ? '#f59e0b' : colorPnl(t.pnl), fontWeight: 600 }}>
                            {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, isHK)}
                          </td>
                          <td>{isOpen ? '—' : `${durDays}d`}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
```

- [ ] **Step 3: Verify frontend builds**

Run: `cd quant/frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add quant/frontend/src/BacktestDashboard.js quant/frontend/src/App.css
git commit -m "feat(frontend): display open positions with status badge in backtest trades table"
```

---

### Task 4: End-to-end verification

- [ ] **Step 1: Run all backtest tests**

Run: `python -m pytest quant/tests/test_backtester_fixed.py quant/tests/test_backtester_buy_trade.py -v`
Expected: ALL PASS

- [ ] **Step 2: Start API server and run a backtest manually**

Run: `python quant/api_server.py`

Then in a browser or curl, POST a backtest:
```bash
curl -X POST http://localhost:5000/api/backtest/run -H "Content-Type: application/json" -d "{\"strategy_id\": \"SimpleMomentum\", \"start_date\": \"2023-01-01\", \"end_date\": \"2024-01-01\", \"symbols\": [\"HK.00700\"], \"initial_cash\": 100000}"
```

Poll the result:
```bash
curl http://localhost:5000/api/backtest/result/<backtest_id>
```

Verify:
- `trades` array contains objects with `status` field ("closed" or "open")
- Open position trades have `exit_time: null`
- P&L for open positions is the unrealized amount

- [ ] **Step 3: Verify frontend renders correctly**

Open the dashboard in a browser, run a backtest, and verify:
- Closed trades show green "已完成" badge
- Open positions show blue "持仓中" badge with blue-tinted row background
- Open positions show "—" for Exit Date and Duration
- Open position Exit Price is shown in blue
- Open position P&L is shown in amber/orange with italic style
