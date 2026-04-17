# Frontend Two-Module Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the 3-tab frontend (backtest/live/strategy pool) into 2 tabs (backtest/live) with a redesigned live trading module and enhanced backtest parameter inputs.

**Architecture:** Extract all inline live trading JSX from App.js into new components (LiveTradingPage, PositionOverview, StrategyManagement). Rewrite StrategyDetailModal with 3 internal tabs. Add dynamic strategy parameter form to BacktestDashboard. Small backend change to accept `strategy_params` in the backtest run endpoint.

**Tech Stack:** React 18, Axios, react-markdown, Flask backend, custom CSS (dark theme)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `frontend/src/App.js` | 2 tabs only, import LiveTradingPage, remove StrategyPoolPage import |
| Modify | `frontend/src/BacktestDashboard.js` | Dynamic strategy list + strategy-specific parameter inputs |
| Modify | `frontend/src/App.css` | New live module styles, remove unused styles |
| Modify | `api_server.py` | Accept `strategy_params` in backtest run, pass to strategy constructor |
| Create | `frontend/src/LiveTradingPage.js` | Live trading scroll layout, composes PositionOverview + StrategyManagement + orders |
| Create | `frontend/src/PositionOverview.js` | Asset summary cards + tabbed holdings/strategy NAV tables |
| Create | `frontend/src/StrategyManagement.js` | CIO panel + activation dropdown + strategy table with row-click modal |
| Rewrite | `frontend/src/StrategyDetailModal.js` | 3-tab modal: README / Backtest Performance / Live Performance |
| Delete | `frontend/src/StrategyPoolPage.js` | No longer used |
| Delete | `frontend/src/StrategyCard.js` | No longer used |
| Delete | `frontend/src/StrategyReadmeModal.js` | Merged into StrategyDetailModal README tab |

---

### Task 1: Backend — Accept strategy_params in backtest run

**Files:**
- Modify: `api_server.py:498-557`

- [ ] **Step 1: Modify the `run_backtest` function to read and apply `strategy_params`**

In `api_server.py`, after line 506 (`slippage_bps = ...`), add reading `strategy_params` from request. Then at line 557 where the strategy is constructed, pass the params.

Find this block (lines 500-506):
```python
    data = request.json
    strategy_id = data.get('strategy_id', 'SimpleMomentum')
    start_date = data.get('start_date', '2020-01-01')
    end_date = data.get('end_date', '2024-12-31')
    symbols = data.get('symbols', ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY'])
    initial_cash = data.get('initial_cash', 100000)
    slippage_bps = data.get('slippage_bps', 5)
```

Add after it:
```python
    strategy_params = data.get('strategy_params', {})
```

Then find line 557:
```python
            strategy = strategy_class(symbols=symbols)
```

Replace with:
```python
            strategy_kwargs = {"symbols": symbols}
            if strategy_params:
                strategy_kwargs.update(strategy_params)
            strategy = strategy_class(**strategy_kwargs)
```

- [ ] **Step 2: Verify by starting the API server**

Run: `cd D:\vk\quant && python -c "import api_server; print('OK')"` (or just check no syntax errors)

Expected: No import errors

- [ ] **Step 3: Commit**

```bash
git add api_server.py
git commit -m "feat: accept strategy_params in backtest run endpoint"
```

---

### Task 2: Modify BacktestDashboard — Dynamic strategy list + strategy params form

**Files:**
- Modify: `frontend/src/BacktestDashboard.js`

- [ ] **Step 1: Replace hardcoded STRATEGIES with dynamic fetch + add strategy params state**

Replace lines 1-11 (imports and STRATEGIES constant) with:

```jsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';
```

Then inside the `BacktestDashboard` component, after the existing state declarations (after line 137 `const pollRef = useRef(null);`), add:

```jsx
  const [strategies, setStrategies] = useState([]);
  const [strategyParams, setStrategyParams] = useState({});
  const [paramValues, setParamValues] = useState({});
```

- [ ] **Step 2: Add fetch functions for strategies and parameters**

After the `fetchHistory` function (after line 144), add:

```jsx
  const fetchStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      const strats = res.data.strategies || [];
      setStrategies(strats);
      if (strats.length > 0 && !strats.find(s => s.id === strategy)) {
        setStrategy(strats[0].id);
      }
    } catch (e) { console.error('Failed to fetch strategies:', e); }
  }, [strategy]);

  const fetchParams = useCallback(async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/${strategyId}/parameters`);
      const params = res.data.parameters || {};
      setStrategyParams(params);
      const defaults = {};
      Object.entries(params).forEach(([key, def]) => {
        defaults[key] = def.default;
      });
      setParamValues(defaults);
    } catch (e) {
      setStrategyParams({});
      setParamValues({});
    }
  }, []);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  useEffect(() => {
    if (strategy) fetchParams(strategy);
  }, [strategy, fetchParams]);
```

- [ ] **Step 3: Update the strategy selector to use dynamic data**

Find lines 227-230 (the strategy select):
```jsx
          <select className="bt-select" value={strategy} onChange={e => setStrategy(e.target.value)}>
            {STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
```

Replace with:
```jsx
          <select className="bt-select" value={strategy} onChange={e => setStrategy(e.target.value)}>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
```

- [ ] **Step 4: Update `runBacktest` to send `strategy_params`**

Find lines 185-192 (the POST body):
```jsx
      const res = await axios.post(`${API_BASE}/backtest/run`, {
        strategy_id: strategy,
        start_date: startDate,
        end_date: endDate,
        symbols: symbols.split(',').map(s => s.trim()).filter(Boolean),
        initial_cash: Number(initialCash),
        slippage_bps: Number(slippageBps),
      });
```

Replace with:
```jsx
      const res = await axios.post(`${API_BASE}/backtest/run`, {
        strategy_id: strategy,
        start_date: startDate,
        end_date: endDate,
        symbols: symbols.split(',').map(s => s.trim()).filter(Boolean),
        initial_cash: Number(initialCash),
        slippage_bps: Number(slippageBps),
        strategy_params: paramValues,
      });
```

- [ ] **Step 5: Add strategy params form section after the controls bar**

Find the closing `</div>` of the bt-controls div (line 256), and after the error display block (lines 258-262), add:

```jsx
      {Object.keys(strategyParams).length > 0 && (
        <div className="bt-controls bt-params-section">
          <div style={{ width: '100%', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Strategy-Specific Parameters
          </div>
          {Object.entries(strategyParams).map(([key, def]) => (
            <div key={key} className="bt-control-group">
              <label>{key.replace(/_/g, ' ')} <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>({def.type})</span></label>
              {def.type === 'bool' ? (
                <label className="toggle-switch" style={{ marginTop: 4 }}>
                  <input type="checkbox" checked={!!paramValues[key]} onChange={e => setParamValues(prev => ({ ...prev, [key]: e.target.checked }))} />
                  <span className="toggle-slider"></span>
                </label>
              ) : def.options ? (
                <select className="bt-input" value={paramValues[key] ?? def.default} onChange={e => setParamValues(prev => ({ ...prev, [key]: e.target.value }))}>
                  {def.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input type={def.type === 'int' || def.type === 'float' ? 'number' : 'text'} className="bt-input"
                  value={paramValues[key] ?? def.default}
                  step={def.type === 'float' ? '0.1' : undefined}
                  onChange={e => setParamValues(prev => ({ ...prev, [key]: def.type === 'int' ? parseInt(e.target.value) : def.type === 'float' ? parseFloat(e.target.value) : e.target.value }))}
                />
              )}
              <span style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{def.description}</span>
            </div>
          ))}
        </div>
      )}
```

- [ ] **Step 6: Add CSS for the params section**

In `App.css`, after the `.bt-status` rule (line 620), add:

```css
.bt-params-section {
  flex-wrap: wrap;
  align-items: start;
}

.bt-params-section .bt-control-group {
  min-width: 160px;
}
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/BacktestDashboard.js frontend/src/App.css
git commit -m "feat: dynamic strategy list + strategy-specific params in backtest"
```

---

### Task 3: Create PositionOverview component

**Files:**
- Create: `frontend/src/PositionOverview.js`

- [ ] **Step 1: Create the component**

Create `frontend/src/PositionOverview.js`:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtCurrency = (v) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
};
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

export default function PositionOverview() {
  const [portfolio, setPortfolio] = useState(null);
  const [strategyPool, setStrategyPool] = useState(null);
  const [activeTab, setActiveTab] = useState('holdings');

  const fetchData = useCallback(async () => {
    try {
      const [portRes, poolRes] = await Promise.all([
        axios.get(`${API_BASE}/portfolio`),
        axios.get(`${API_BASE}/strategy-pool`),
      ]);
      setPortfolio(portRes.data);
      setStrategyPool(poolRes.data);
    } catch (e) { console.error('PositionOverview fetch error', e); }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const nav = portfolio?.nav || 0;
  const unrealized = portfolio?.total_unrealized_pnl || 0;
  const realized = portfolio?.total_realized_pnl || 0;
  const totalPnl = unrealized + realized;
  const holdings = portfolio?.holdings || [];
  const strategies = strategyPool?.strategies || [];

  return (
    <div className="position-overview">
      <div className="po-summary">
        <div className="po-summary-card">
          <div className="po-summary-label">Total NAV</div>
          <div className="po-summary-value">{fmtCurrency(nav)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Unrealized P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(unrealized) }}>{fmtCurrency(unrealized)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Realized P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(realized) }}>{fmtCurrency(realized)}</div>
        </div>
        <div className="po-summary-card">
          <div className="po-summary-label">Total P&L</div>
          <div className="po-summary-value" style={{ color: pnlColor(totalPnl) }}>{fmtCurrency(totalPnl)}</div>
        </div>
      </div>

      <div className="po-tabs">
        <button className={`po-tab ${activeTab === 'holdings' ? 'active' : ''}`} onClick={() => setActiveTab('holdings')}>
          Securities Holdings
        </button>
        <button className={`po-tab ${activeTab === 'strategy_nav' ? 'active' : ''}`} onClick={() => setActiveTab('strategy_nav')}>
          Strategy NAV Distribution
        </button>
      </div>

      <div className="po-table-wrap">
        {activeTab === 'holdings' ? (
          holdings.length === 0 ? (
            <div className="empty-text">No positions</div>
          ) : (
            <table className="po-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Qty</th>
                  <th>Avg Cost</th>
                  <th>Market Value</th>
                  <th>Unrealized P&L</th>
                  <th>P&L %</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{h.symbol}</td>
                    <td>{h.quantity}</td>
                    <td>{fmtCurrency(h.avg_cost || h.cost_basis / h.quantity || 0)}</td>
                    <td>{fmtCurrency(h.market_value || 0)}</td>
                    <td style={{ color: pnlColor(h.pnl || 0), fontWeight: 600 }}>{fmtCurrency(h.pnl || 0)}</td>
                    <td style={{ color: pnlColor(h.pnl || 0) }}>
                      {h.market_value ? ((h.pnl / (h.market_value - (h.pnl || 0))) * 100).toFixed(2) + '%' : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          strategies.length === 0 ? (
            <div className="empty-text">No strategy data</div>
          ) : (
            <table className="po-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th>Weight</th>
                  <th>Allocated Capital</th>
                  <th>Strategy NAV</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.id}>
                    <td style={{ fontWeight: 600, color: 'var(--accent-cyan)' }}>{s.name}</td>
                    <td>
                      <span className={`status-badge ${s.enabled ? 'active' : 'inactive'}`}>
                        {s.enabled ? '● Active' : '○ Inactive'}
                      </span>
                    </td>
                    <td>{(s.weight * 100).toFixed(1)}%</td>
                    <td className="capital-cell">${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}</td>
                    <td>${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}</td>
                    <td className={`pnl-cell ${s.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                      {s.current_pnl >= 0 ? '+' : ''}${s.current_pnl?.toFixed(2) || '0.00'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for PositionOverview**

In `App.css`, add after the `.bt-params-section` styles:

```css
/* Position Overview */
.position-overview {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 20px;
}

.po-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.po-summary-card {
  background: var(--bg-tertiary);
  padding: 14px;
  border-radius: 8px;
}

.po-summary-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}

.po-summary-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.po-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 12px;
}

.po-tab {
  padding: 10px 20px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  letter-spacing: 0.05em;
}

.po-tab.active {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

.po-tab:hover {
  color: var(--text-primary);
}

.po-table-wrap {
  overflow-x: auto;
}

.po-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.po-table th {
  text-align: left;
  padding: 10px 14px;
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-tertiary);
}

.po-table td {
  padding: 10px 14px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
}

.po-table tr:hover td {
  background: rgba(0, 212, 255, 0.03);
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/PositionOverview.js frontend/src/App.css
git commit -m "feat: add PositionOverview component with holdings/strategy NAV tabs"
```

---

### Task 4: Create StrategyManagement component

**Files:**
- Create: `frontend/src/StrategyManagement.js`

- [ ] **Step 1: Create the component**

Create `frontend/src/StrategyManagement.js`:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyWeightBar from './StrategyWeightBar';
import StrategyDetailModal from './StrategyDetailModal';

const API_BASE = 'http://localhost:5000/api';

export default function StrategyManagement({ onStrategySelect }) {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [allStrategies, setAllStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activationLoading, setActivationLoading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const fetchCIO = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/cio/assessment`);
      setCioAssessment(res.data);
    } catch (e) { console.error('CIO fetch error', e); }
  }, []);

  const fetchStrategyPool = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy-pool`);
      setStrategyPool(res.data);
    } catch (e) { console.error('Strategy pool fetch error', e); }
  }, []);

  const fetchAllStrategies = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategies`);
      setAllStrategies(res.data.strategies || []);
    } catch (e) { console.error('All strategies fetch error', e); }
  }, []);

  useEffect(() => {
    fetchCIO();
    fetchStrategyPool();
    fetchAllStrategies();
    const cioInterval = setInterval(fetchCIO, 60000);
    const poolInterval = setInterval(fetchStrategyPool, 5000);
    return () => {
      clearInterval(cioInterval);
      clearInterval(poolInterval);
    };
  }, [fetchCIO, fetchStrategyPool, fetchAllStrategies]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/cio/refresh`);
      await fetchCIO();
    } catch (e) { console.error('CIO refresh error', e); }
    setLoading(false);
  };

  const handleToggleStrategy = async (strategyId, currentEnabled) => {
    setActivationLoading(true);
    try {
      await axios.post(`${API_BASE}/strategies/${strategyId}/toggle`, { enabled: !currentEnabled });
      await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
    } catch (e) {
      console.error('Strategy toggle error', e);
    }
    setActivationLoading(false);
  };

  const handleSelectStrategy = async (strategyId) => {
    try {
      await axios.post(`${API_BASE}/strategies/select`, { strategy_id: strategyId });
      if (onStrategySelect) onStrategySelect(strategyId);
      await Promise.all([fetchStrategyPool(), fetchAllStrategies()]);
    } catch (e) { console.error('Strategy select error', e); }
  };

  const handleRowClick = (strategy) => {
    setSelectedStrategy(strategy);
    setDetailOpen(true);
  };

  const weights = cioAssessment?.weights || {};
  const activatedStrategies = strategyPool.strategies
    .filter(s => s.enabled)
    .sort((a, b) => (b.allocated_capital || 0) - (a.allocated_capital || 0));

  return (
    <div className="strategy-management">
      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      {Object.keys(weights).length > 0 && (
        <div className="sm-weights-section">
          <div className="sp-section-title">CIO Weight Allocation</div>
          <StrategyWeightBar weights={weights} />
        </div>
      )}

      <div className="sm-strategy-section">
        <div className="sm-section-header">
          <div className="sp-section-title">Strategy Activation</div>
          <select className="sm-activate-select" onChange={e => handleSelectStrategy(e.target.value)} defaultValue="">
            <option value="" disabled>Select strategy to activate...</option>
            {allStrategies.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>

        <div className="strategy-table-container">
          <table className="strategy-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Status</th>
                <th>Weight</th>
                <th>Allocated</th>
                <th>P&L</th>
                <th>Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {activatedStrategies.length === 0 ? (
                <tr>
                  <td colSpan="6" className="empty-row">
                    No active strategies. Select a strategy above to activate.
                  </td>
                </tr>
              ) : (
                activatedStrategies.map((s) => (
                  <tr key={s.id} className="strategy-row" onClick={() => handleRowClick(s)}>
                    <td className="strategy-name-cell">{s.name}</td>
                    <td>
                      <label className="toggle-switch" onClick={e => e.stopPropagation()}>
                        <input type="checkbox" checked={s.enabled}
                          onChange={() => handleToggleStrategy(s.id, s.enabled)}
                          disabled={activationLoading}
                        />
                        <span className="toggle-slider"></span>
                      </label>
                    </td>
                    <td>
                      <div className="weight-cell">
                        <div className="weight-bar-mini">
                          <div className="weight-bar-mini-fill" style={{ width: `${(s.weight * 100).toFixed(1)}%` }} />
                        </div>
                        <span>{(s.weight * 100).toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className="capital-cell">
                      ${s.allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
                    </td>
                    <td className={`pnl-cell ${s.current_pnl >= 0 ? 'positive' : 'negative'}`}>
                      {s.current_pnl >= 0 ? '+' : ''}${s.current_pnl?.toFixed(2) || '0.00'}
                    </td>
                    <td className="sharpe-cell">{s.backtest_sharpe?.toFixed(2) || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <StrategyDetailModal
        isOpen={detailOpen}
        onClose={() => { setDetailOpen(false); setSelectedStrategy(null); }}
        strategy={selectedStrategy}
      />
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for StrategyManagement**

In `App.css`, add:

```css
/* Strategy Management */
.strategy-management {
  margin-bottom: 20px;
}

.sm-weights-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.sm-strategy-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
}

.sm-section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.sm-activate-select {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  color: var(--text-primary);
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}

.sm-activate-select:focus {
  outline: none;
  border-color: var(--accent-cyan);
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/StrategyManagement.js frontend/src/App.css
git commit -m "feat: add StrategyManagement component with CIO panel and strategy table"
```

---

### Task 5: Rewrite StrategyDetailModal with 3 tabs

**Files:**
- Rewrite: `frontend/src/StrategyDetailModal.js`

- [ ] **Step 1: Rewrite the entire file**

Replace the full content of `frontend/src/StrategyDetailModal.js` with:

```jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:5000/api';

const fmtCurrency = (v) => {
  const n = parseFloat(v) || 0;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
};
const fmtPct = (v) => (parseFloat(v) || 0).toFixed(2) + '%';
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function ModalEquityChart({ curve }) {
  if (!curve || curve.length < 2) return null;
  const W = 600, H = 160, padL = 50, padR = 15, padT = 10, padB = 25;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const values = curve.map(([, v]) => v);
  const minV = Math.min(...values), maxV = Math.max(...values), rangeV = maxV - minV || 1;
  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (v) => padT + plotH - ((v - minV) / rangeV) * plotH;
  const points = curve.map(([, v], i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const areaPoints = `${scaleX(0)},${padT + plotH} ${points} ${scaleX(curve.length - 1)},${padT + plotH}`;
  const dateLabels = [0, Math.floor(curve.length / 2), curve.length - 1].map(idx => ({
    x: scaleX(idx), label: curve[idx][0].slice(0, 10)
  }));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '160px' }}>
      <defs>
        <linearGradient id="modalEqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00d4ff" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((frac, i) => {
        const v = minV + rangeV * frac;
        const y = scaleY(v);
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#333355" strokeWidth="0.5" />
            <text x={padL - 6} y={y + 3} textAnchor="end" fill="#666680" fontSize="8">{fmtCurrency(v)}</text>
          </g>
        );
      })}
      <polygon points={areaPoints} fill="url(#modalEqGrad)" />
      <polyline points={points} fill="none" stroke="#00d4ff" strokeWidth="1.5" />
      {dateLabels.map((dl, i) => (
        <text key={i} x={dl.x} y={H - 5} textAnchor="middle" fill="#666680" fontSize="8">{dl.label}</text>
      ))}
    </svg>
  );
}

function ModalPnlChart({ curve }) {
  if (!curve || curve.length < 2) return null;
  const W = 600, H = 120, padL = 50, padR = 15, padT = 10, padB = 20;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxAbs = Math.max(...curve.map(Math.abs), 1);
  const scaleX = (i) => padL + (i / (curve.length - 1)) * plotW;
  const scaleY = (v) => padT + plotH / 2 - (v / maxAbs) * (plotH / 2 - 4);
  const points = curve.map((v, i) => `${scaleX(i)},${scaleY(v)}`).join(' ');
  const zeroY = padT + plotH / 2;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: '120px' }}>
      <line x1={padL} y1={zeroY} x2={W - padR} y2={zeroY} stroke="#333355" strokeWidth="0.5" />
      <polyline points={points} fill="none" stroke="#00ff88" strokeWidth="1.2" />
      {[maxAbs, 0, -maxAbs].map((v, i) => (
        <text key={i} x={padL - 6} y={scaleY(v) + 3} textAnchor="end" fill="#666680" fontSize="8">
          {v === 0 ? '$0' : fmtCurrency(v)}
        </text>
      ))}
    </svg>
  );
}

export default function StrategyDetailModal({ isOpen, onClose, strategy }) {
  const [activeTab, setActiveTab] = useState('readme');
  const [readme, setReadme] = useState(null);
  const [backtestData, setBacktestData] = useState(null);
  const [liveData, setLiveData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isOpen || !strategy) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose, strategy]);

  useEffect(() => {
    if (!isOpen || !strategy) return;
    setActiveTab('readme');
    setReadme(null);
    setBacktestData(null);
    setLiveData(null);

    setLoading(true);
    const fetchAll = async () => {
      const promises = [];
      if (strategy.has_readme || true) {
        promises.push(
          axios.get(`${API_BASE}/strategies/${strategy.id}/readme`)
            .then(res => setReadme(res.data)).catch(() => setReadme(null))
        );
      }
      promises.push(
        axios.get(`${API_BASE}/strategies/backtest/${strategy.id}`)
          .then(res => setBacktestData(res.data)).catch(() => setBacktestData(null))
      );
      promises.push(
        axios.get(`${API_BASE}/strategies/performance/${strategy.id}`)
          .then(res => setLiveData(res.data)).catch(() => setLiveData(null))
      );
      await Promise.all(promises);
      setLoading(false);
    };
    fetchAll();
  }, [isOpen, strategy]);

  if (!isOpen || !strategy) return null;

  const tabs = [
    { id: 'readme', label: 'README' },
    { id: 'backtest', label: 'Backtest' },
    { id: 'live', label: 'Live Performance' },
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content strategy-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{strategy.name}</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="sdm-tabs">
          {tabs.map(t => (
            <button key={t.id} className={`sdm-tab ${activeTab === t.id ? 'active' : ''}`}
              onClick={() => setActiveTab(t.id)}>{t.label}</button>
          ))}
        </div>
        <div className="modal-body">
          {loading ? (
            <div className="empty-text">Loading...</div>
          ) : activeTab === 'readme' ? (
            readme && readme.content ? (
              <div className="readme-content">
                <ReactMarkdown>{readme.content}</ReactMarkdown>
              </div>
            ) : (
              <div className="empty-text">README not available</div>
            )
          ) : activeTab === 'backtest' ? (
            backtestData && backtestData.metrics ? (
              <div className="sdm-performance">
                <div className="sdm-metrics-grid">
                  {[
                    { label: 'Total Return', value: fmtPct(backtestData.metrics.total_return_pct), color: pnlColor(backtestData.metrics.total_return_pct) },
                    { label: 'Sharpe Ratio', value: (backtestData.metrics.sharpe_ratio || 0).toFixed(2) },
                    { label: 'Max Drawdown', value: fmtPct(backtestData.metrics.max_drawdown_pct), color: 'var(--accent-red)' },
                    { label: 'Win Rate', value: fmtPct(backtestData.metrics.win_rate) },
                    { label: 'Profit Factor', value: (backtestData.metrics.profit_factor || 0).toFixed(2) },
                    { label: 'Total Trades', value: backtestData.metrics.total_trades },
                  ].map((m, i) => (
                    <div key={i} className="bt-metric">
                      <div className="bt-metric-value" style={m.color ? { color: m.color } : {}}>{m.value}</div>
                      <div className="bt-metric-label">{m.label}</div>
                    </div>
                  ))}
                </div>
                {backtestData.equity_curve && (
                  <div className="sdm-chart-section">
                    <div className="bt-chart-title">Equity Curve</div>
                    <ModalEquityChart curve={backtestData.equity_curve} />
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-text">暂无回测记录，请先在回测模块运行</div>
            )
          ) : (
            liveData && liveData.performance ? (
              <div className="sdm-performance">
                <div className="sdm-metrics-grid">
                  {[
                    { label: 'Total P&L', value: fmtCurrency(liveData.performance.total_pnl), color: pnlColor(liveData.performance.total_pnl) },
                    { label: 'Sharpe Ratio', value: (liveData.performance.sharpe_ratio || 0).toFixed(2) },
                    { label: 'Max Drawdown', value: fmtPct(liveData.performance.max_drawdown), color: 'var(--accent-red)' },
                    { label: 'Win Rate', value: fmtPct(liveData.performance.win_rate) },
                    { label: 'Total Trades', value: liveData.performance.total_trades },
                    { label: 'Profit Factor', value: (liveData.performance.profit_factor || 0).toFixed(2) },
                  ].map((m, i) => (
                    <div key={i} className="bt-metric">
                      <div className="bt-metric-value" style={m.color ? { color: m.color } : {}}>{m.value}</div>
                      <div className="bt-metric-label">{m.label}</div>
                    </div>
                  ))}
                </div>
                {liveData.pnl_curve && liveData.pnl_curve.length > 1 && (
                  <div className="sdm-chart-section">
                    <div className="bt-chart-title">P&L Curve</div>
                    <ModalPnlChart curve={liveData.pnl_curve} />
                  </div>
                )}
                {liveData.recent_trades && liveData.recent_trades.length > 0 && (
                  <div className="sdm-trades-section">
                    <div className="bt-chart-title">Recent Trades</div>
                    <div className="bt-trades-scroll">
                      <table className="bt-trades-table">
                        <thead>
                          <tr>
                            <th>Time</th>
                            <th>Side</th>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Price</th>
                            <th>P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {liveData.recent_trades.map((t, i) => (
                            <tr key={i}>
                              <td>{new Date(t.time).toLocaleDateString()}</td>
                              <td style={{ color: t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.side}</td>
                              <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                              <td>{t.quantity}</td>
                              <td>${t.price}</td>
                              <td style={{ color: pnlColor(t.pnl), fontWeight: 600 }}>
                                {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-text">该策略尚未在实盘中运行</div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for modal tabs and internal sections**

In `App.css`, add:

```css
/* Strategy Detail Modal Tabs */
.sdm-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  padding: 0 20px;
}

.sdm-tab {
  padding: 12px 20px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  letter-spacing: 0.05em;
}

.sdm-tab.active {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

.sdm-tab:hover {
  color: var(--text-primary);
}

.sdm-performance {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sdm-metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.sdm-chart-section {
  background: var(--bg-tertiary);
  border-radius: 8px;
  padding: 12px;
}

.sdm-trades-section {
  background: var(--bg-tertiary);
  border-radius: 8px;
  overflow: hidden;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/StrategyDetailModal.js frontend/src/App.css
git commit -m "feat: rewrite StrategyDetailModal with 3-tab layout (README/backtest/live)"
```

---

### Task 6: Create LiveTradingPage component

**Files:**
- Create: `frontend/src/LiveTradingPage.js`

- [ ] **Step 1: Create the component**

Create `frontend/src/LiveTradingPage.js`:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import PositionOverview from './PositionOverview';
import StrategyManagement from './StrategyManagement';

const API_BASE = 'http://localhost:5000/api';

export default function LiveTradingPage() {
  const [orders, setOrders] = useState([]);

  const fetchOrders = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/orders`);
      setOrders(res.data.orders || res.data || []);
    } catch (e) { console.error('Orders fetch error', e); }
  }, []);

  useEffect(() => {
    fetchOrders();
    const interval = setInterval(fetchOrders, 5000);
    return () => clearInterval(interval);
  }, [fetchOrders]);

  return (
    <div className="live-trading-page">
      <PositionOverview />
      <StrategyManagement />
      <div className="lt-orders-section">
        <div className="sp-section-title">Recent Orders</div>
        <div className="strategy-table-container">
          <table className="po-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan="6" className="empty-row">No recent orders</td>
                </tr>
              ) : (
                orders.slice(0, 10).map((o, i) => (
                  <tr key={i}>
                    <td>{o.time ? new Date(o.time).toLocaleString() : '-'}</td>
                    <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{o.symbol}</td>
                    <td style={{ color: o.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{o.side}</td>
                    <td>{o.quantity}</td>
                    <td>${o.price || '-'}</td>
                    <td style={{ color: 'var(--accent-green)' }}>{o.status || '-'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for LiveTradingPage**

In `App.css`, add:

```css
/* Live Trading Page */
.live-trading-page {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.lt-orders-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/LiveTradingPage.js frontend/src/App.css
git commit -m "feat: add LiveTradingPage with position overview, strategy management, orders"
```

---

### Task 7: Modify App.js — 2 tabs + wire up new components

**Files:**
- Modify: `frontend/src/App.js`

- [ ] **Step 1: Update imports**

Replace lines 1-6:
```jsx
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import BacktestDashboard from './BacktestDashboard';
import LiveTradingPage from './LiveTradingPage';
import './App.css';
```

- [ ] **Step 2: Remove the inline `StrategyDetail` component and all live-trading state/logic from App**

Remove the `StrategyDetail` function component (lines 17-141) entirely.

Remove these state variables and related logic from the `App` function:
- `portfolio`, `availableStrategies`, `activeStrategies`, `marketData`, `orders`, `orderForm`, `selectedStrategyId` state
- `fetchStatus`, `fetchStrategies`, `fetchMarketData`, `fetchCIO` callbacks (keep `fetchStatus` only for system status)
- `submitOrder`, `selectStrategy` functions
- `formatCurrency`, `pnlColor` helpers
- The polling intervals for market data and CIO in the `useEffect`

The simplified `App` function should only keep:
- `selectedBroker`, `systemStatus`, `apiConnected`, `isLoading`, `activeTab`, `submitError` state
- A simplified `fetchStatus` that only gets system status
- `startSystem`, `stopSystem` functions
- `getStatusBadge` helper

Replace the entire `App` function body with:

```jsx
function App() {
  const [selectedBroker, setSelectedBroker] = useState('paper');
  const [systemStatus, setSystemStatus] = useState('stopped');
  const [apiConnected, setApiConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('backtest');
  const [submitError, setSubmitError] = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/status`);
      setApiConnected(true);
      setSystemStatus(res.data.status);
    } catch {
      setApiConnected(false);
      setSystemStatus('stopped');
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const startSystem = async () => {
    setIsLoading(true); setSubmitError('');
    try {
      await axios.post(`${API_BASE}/start`, { broker: selectedBroker });
      await new Promise(r => setTimeout(r, 1000));
      fetchStatus();
    } catch (err) { setSubmitError(err.response?.data?.error || err.message); }
    setIsLoading(false);
  };

  const stopSystem = async () => {
    setIsLoading(true); setSubmitError('');
    try {
      await axios.post(`${API_BASE}/stop`);
      await new Promise(r => setTimeout(r, 500));
      fetchStatus();
    } catch (err) { setSubmitError(err.response?.data?.error || err.message); }
    setIsLoading(false);
  };

  const getStatusBadge = () => {
    if (!apiConnected) return { text: 'DISCONNECTED', color: 'var(--accent-red)' };
    switch (systemStatus) {
      case 'running': return { text: 'RUNNING', color: 'var(--accent-green)' };
      case 'starting': return { text: 'STARTING', color: 'var(--accent-amber)' };
      case 'stopping': return { text: 'STOPPING', color: 'var(--accent-amber)' };
      default: return { text: 'STOPPED', color: 'var(--text-muted)' };
    }
  };
  const statusBadge = getStatusBadge();

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo-text">QUANT<span className="logo-accent">SYSTEM</span></span>
          <span className="status-badge" style={{ borderColor: statusBadge.color, color: statusBadge.color }}>
            {statusBadge.text}
          </span>
        </div>
        <div className="header-right">
          <select className="broker-select" value={selectedBroker} onChange={(e) => setSelectedBroker(e.target.value)}>
            {BROKERS.map(b => (<option key={b.id} value={b.id}>{b.name}</option>))}
          </select>
          {systemStatus === 'running' ? (
            <button className="btn btn-stop" onClick={stopSystem} disabled={isLoading}>■ STOP</button>
          ) : (
            <button className="btn btn-start" onClick={startSystem} disabled={isLoading}>▶ START</button>
          )}
        </div>
      </header>

      {submitError && (
        <div style={{ background: 'var(--accent-red)', color: '#fff', padding: '8px 16px', fontSize: '13px' }}>{submitError}</div>
      )}

      <div className="tab-bar">
        <button className={`tab ${activeTab === 'backtest' ? 'active' : ''}`} onClick={() => setActiveTab('backtest')}>BACKTEST</button>
        <button className={`tab ${activeTab === 'live' ? 'active' : ''}`} onClick={() => setActiveTab('live')}>LIVE TRADING</button>
      </div>

      <main className="main">
        {activeTab === 'backtest' ? <BacktestDashboard /> : <LiveTradingPage />}
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.js
git commit -m "feat: simplify App.js to 2 tabs (backtest + live), remove inline live JSX"
```

---

### Task 8: Clean up removed files and unused CSS

**Files:**
- Delete: `frontend/src/StrategyPoolPage.js`
- Delete: `frontend/src/StrategyCard.js`
- Delete: `frontend/src/StrategyReadmeModal.js`
- Modify: `frontend/src/App.css` — remove unused CSS

- [ ] **Step 1: Delete unused component files**

```bash
rm frontend/src/StrategyPoolPage.js frontend/src/StrategyCard.js frontend/src/StrategyReadmeModal.js
```

- [ ] **Step 2: Remove unused CSS blocks from App.css**

Remove these CSS blocks that are no longer referenced by any component:
- `.strategy-pool-page` and all `.sp-*` styles (lines 807-843 area — the `.sp-section-title` is still used by StrategyManagement, so keep that one)
- `.strategy-card` and all `.strategy-card-*` styles (lines 851-912 area)
- `.btn-readme` style (lines 918-930)
- The old `.strategy-detail` and `.sd-*` styles (lines 377-495 area) — replaced by the new modal styles
- The `.btn-link` style (lines 1023-1031) — no longer used
- `.btn-backtest` style (lines 1503-1517) — no longer used in this context
- `.detail-section`, `.strategy-meta`, `.meta-item`, `.meta-label`, `.meta-value` (lines 1307-1355)
- `.backtest-metrics`, `.live-metrics`, `.metric-card` (lines 1412-1449) — but `.metric-card .metric-*` is not used, while the `.bt-metric` is used in the new modal
- `.backtest-period`, `.period-label`, `.period-value` (lines 1451-1468)
- `.performance-curve`, `.curve-placeholder` (lines 1470-1493)
- `.detail-actions` (lines 1495-1501)
- `.sp-activation-section` and `.activation-dropdown-*` styles (lines 1036-1094)
- `.dropdown-header`, `.dropdown-item`, `.dropdown-strategy-name` (lines 1096-1127)

Keep all of:
- `.sp-section-title` (used by StrategyManagement)
- `.strategy-table-container`, `.strategy-table`, `.strategy-row`, `.strategy-name-cell` (used by StrategyManagement)
- `.status-badge.active/.inactive` (used)
- `.weight-cell`, `.capital-cell`, `.pnl-cell`, `.sharpe-cell` (used)
- `.toggle-switch`, `.toggle-slider` (used)
- `.weight-bar-mini`, `.weight-bar-mini-fill` (used)
- `.btn-view-detail` (may still be useful)
- `.empty-row` (used)
- `.strategy-detail-modal` (used)
- `.readme-content` and `.readme-content *` (used by new modal)
- All `.bt-*` styles (used by BacktestDashboard and new modal)
- All `.cio-*` styles (used by CIOAssessmentPanel)
- All `.weight-bar-*` styles (used by StrategyWeightBar)
- All modal overlay/content/header/body styles (used)

- [ ] **Step 3: Build and verify no errors**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add -A frontend/src/
git commit -m "chore: remove unused components and CSS (StrategyPoolPage, StrategyCard, StrategyReadmeModal)"
```

---

### Task 9: Integration test — full build and manual smoke test

**Files:**
- None (verification only)

- [ ] **Step 1: Run a full production build**

```bash
cd frontend && npm run build
```

Expected: Build completes successfully

- [ ] **Step 2: Start the backend and verify both tabs load**

```bash
python api_server.py
```

Then open the browser at `http://localhost:5000` and verify:
1. Header shows QUANTSYSTEM + broker selector + START/STOP
2. Two tabs: BACKTEST and LIVE TRADING
3. Backtest tab: strategy dropdown is populated dynamically, selecting a strategy shows its params
4. Live Trading tab: scrollable page with Position Overview, Strategy Management, Recent Orders
5. Clicking a strategy row opens the 3-tab modal (README / Backtest / Live Performance)
6. No console errors

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: integration fixes for two-module frontend"
```
