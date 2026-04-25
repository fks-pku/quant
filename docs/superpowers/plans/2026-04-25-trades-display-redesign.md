# Trades Display Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat trades table with three-section layout: position summary cards, closed trades table (no Side/Status, add Return%), and collapsible trade timeline.

**Architecture:** API adds `trade_timeline` field with all BUY+SELL trades in chronological order. Frontend restructures trades display into three sections.

**Tech Stack:** Python (Flask API serialization), React + CSS (frontend display)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `quant/api/backtest_bp.py` | Modify | Add `trade_timeline` to API response |
| `quant/frontend/src/BacktestDashboard.js` | Modify | Replace trades section with 3 sections |
| `quant/frontend/src/App.css` | Modify | Add position cards + timeline styles |

---

### Task 1: API — Add trade_timeline to backtest result

**Files:**
- Modify: `quant/api/backtest_bp.py:123-186`

- [ ] **Step 1: Add trade_timeline serialization**

After the existing `trades_list` construction (after the open_positions loop, around line 150), add a new block that builds `timeline_list` from ALL trades:

```python
            timeline_list = []
            position_tracker = {}
            for t in sorted(result.trades, key=lambda x: x.fill_date or x.entry_time):
                if t.side == "BUY":
                    position_tracker[t.symbol] = position_tracker.get(t.symbol, 0) + t.quantity
                else:
                    position_tracker[t.symbol] = position_tracker.get(t.symbol, 0) - t.quantity
                timeline_list.append({
                    "date": str(t.fill_date or t.entry_time),
                    "action": t.side,
                    "symbol": t.symbol,
                    "quantity": int(t.quantity),
                    "price": float(t.fill_price if t.fill_price else (t.entry_price if t.side == "BUY" else t.exit_price)),
                    "position": int(position_tracker.get(t.symbol, 0)),
                    "pnl": float(t.pnl) if t.side == "SELL" else None,
                })
```

Then in the result dict (line 177-186), add `"trade_timeline": timeline_list`:

```python
                _backtest_results[backtest_id] = {
                    "status": "completed",
                    "backtest_id": backtest_id,
                    "strategy_id": strategy_id,
                    "metrics": metrics,
                    "equity_curve": equity_serializable,
                    "trades": trades_list,
                    "trade_timeline": timeline_list,
                    "description": ...,
                }
```

- [ ] **Step 2: Verify API loads**

Run: `python -c "from quant.api.backtest_bp import backtest_bp; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add quant/api/backtest_bp.py
git commit -m "feat(api): add trade_timeline with all BUY/SELL trades for audit view"
```

---

### Task 2: Frontend — Position summary cards + closed trades table + timeline

**Files:**
- Modify: `quant/frontend/src/App.css` (add new styles)
- Modify: `quant/frontend/src/BacktestDashboard.js` (replace trades section)

- [ ] **Step 1: Add CSS for new sections**

In `quant/frontend/src/App.css`, after the existing `.bt-pnl-unrealized` rule (around line 755), add:

```css
.bt-position-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.bt-position-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  border-left: 3px solid var(--accent-cyan);
}

.bt-position-card.bt-card-profit {
  border-left-color: var(--accent-green);
}

.bt-position-card.bt-card-loss {
  border-left-color: var(--accent-red);
}

.bt-card-symbol {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.bt-card-pnl {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 10px;
}

.bt-card-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 12px;
  font-size: 11px;
}

.bt-card-label {
  color: var(--text-muted);
}

.bt-card-value {
  color: var(--text-secondary);
  text-align: right;
}

.bt-timeline-toggle {
  background: none;
  border: 1px solid var(--border-color);
  color: var(--text-muted);
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  width: 100%;
  text-align: center;
  margin-bottom: 12px;
  transition: all 0.2s;
}

.bt-timeline-toggle:hover {
  border-color: var(--accent-cyan);
  color: var(--accent-cyan);
}

.bt-timeline-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.bt-timeline-table th {
  text-align: left;
  padding: 6px 10px;
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
}

.bt-timeline-table td {
  padding: 4px 10px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
}
```

- [ ] **Step 2: Replace trades section in BacktestDashboard.js**

Replace lines 390-443 in `quant/frontend/src/BacktestDashboard.js` (the entire `{result.trades && ...}` block) with:

```jsx
          {result.trades && result.trades.length > 0 && (() => {
            const openTrades = result.trades.filter(t => t.status === 'open');
            const closedTrades = result.trades.filter(t => t.status === 'closed');
            return (
              <>
                {openTrades.length > 0 && (
                  <div className="bt-position-cards">
                    {openTrades.map((t, i) => {
                      const mv = t.exit_price * t.quantity;
                      const weight = result.metrics?.final_nav ? (mv / result.metrics.final_nav * 100) : 0;
                      const pnlPct = t.entry_price > 0 ? ((t.exit_price - t.entry_price) / t.entry_price * 100) : 0;
                      return (
                        <div key={i} className={`bt-position-card ${t.pnl >= 0 ? 'bt-card-profit' : 'bt-card-loss'}`}>
                          <div className="bt-card-symbol">{t.symbol}</div>
                          <div className="bt-card-pnl" style={{ color: t.pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                            {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, isHK)}
                            <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                              ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                            </span>
                          </div>
                          <div className="bt-card-details">
                            <span className="bt-card-label">持仓</span>
                            <span className="bt-card-value">{t.quantity.toLocaleString()} 股</span>
                            <span className="bt-card-label">成本</span>
                            <span className="bt-card-value">{fmtCurrency(t.entry_price, isHK)}</span>
                            <span className="bt-card-label">现价</span>
                            <span className="bt-card-value" style={{ color: 'var(--accent-cyan)' }}>{fmtCurrency(t.exit_price, isHK)}</span>
                            <span className="bt-card-label">市值</span>
                            <span className="bt-card-value">{fmtCurrency(mv, isHK)}</span>
                            <span className="bt-card-label">权重</span>
                            <span className="bt-card-value">{weight.toFixed(1)}%</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {closedTrades.length > 0 && (
                  <div className="bt-trades">
                    <div className="bt-trades-title">已完成交易 ({closedTrades.length})</div>
                    <div className="bt-trades-scroll">
                      <table className="bt-trades-table">
                        <thead>
                          <tr>
                            <th>Entry Date</th>
                            <th>Exit Date</th>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Entry Price</th>
                            <th>Exit Price</th>
                            <th>P&L</th>
                            <th>Return%</th>
                            <th>Duration</th>
                          </tr>
                        </thead>
                        <tbody>
                          {closedTrades.map((t, i) => {
                            const entryD = new Date(t.entry_time);
                            const exitD = new Date(t.exit_time);
                            const durDays = Math.round((exitD - entryD) / 86400000);
                            const retPct = t.entry_price > 0 ? ((t.exit_price - t.entry_price) / t.entry_price * 100) : 0;
                            return (
                              <tr key={i}>
                                <td>{entryD.toLocaleDateString()}</td>
                                <td>{exitD.toLocaleDateString()}</td>
                                <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{t.symbol}</td>
                                <td>{t.quantity}</td>
                                <td>{fmtCurrency(t.entry_price, isHK)}</td>
                                <td>{fmtCurrency(t.exit_price, isHK)}</td>
                                <td style={{ color: colorPnl(t.pnl), fontWeight: 600 }}>
                                  {t.pnl >= 0 ? '+' : ''}{fmtCurrency(t.pnl, isHK)}
                                </td>
                                <td style={{ color: colorPnl(retPct), fontWeight: 600 }}>
                                  {retPct >= 0 ? '+' : ''}{retPct.toFixed(1)}%
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

                {result.trade_timeline && result.trade_timeline.length > 0 && (
                  <TimelineSection data={result.trade_timeline} isHK={isHK} />
                )}
              </>
            );
          })()}
```

- [ ] **Step 3: Add TimelineSection component**

Add this component BEFORE the `BacktestDashboard` function (around line 119, before `export default function BacktestDashboard()`):

```jsx
function TimelineSection({ data, isHK }) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button className="bt-timeline-toggle" onClick={() => setOpen(true)}>
        Show Trade Timeline ({data.length} orders)
      </button>
    );
  }
  return (
    <div className="bt-trades" style={{ marginTop: 20 }}>
      <div className="bt-trades-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Trade Timeline ({data.length} orders)</span>
        <button className="bt-timeline-toggle" style={{ width: 'auto', margin: 0, padding: '4px 12px' }} onClick={() => setOpen(false)}>
          Collapse
        </button>
      </div>
      <div className="bt-trades-scroll" style={{ maxHeight: 400 }}>
        <table className="bt-timeline-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Action</th>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Position</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody>
            {data.map((t, i) => (
              <tr key={i}>
                <td>{new Date(t.date).toLocaleDateString()}</td>
                <td style={{ color: t.action === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>{t.action}</td>
                <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                <td>{t.action === 'BUY' ? '+' : '-'}{t.quantity}</td>
                <td>{fmtCurrency(t.price, isHK)}</td>
                <td style={{ fontWeight: 600 }}>{t.position}</td>
                <td style={t.pnl != null ? { color: colorPnl(t.pnl), fontWeight: 600 } : { color: 'var(--text-muted)' }}>
                  {t.pnl != null ? `${t.pnl >= 0 ? '+' : ''}${fmtCurrency(t.pnl, isHK)}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

Note: `TimelineSection` uses `useState` which is already imported at line 1. It uses `fmtCurrency` and `colorPnl` which are module-level functions.

- [ ] **Step 4: Build frontend**

Run: `node node_modules/react-scripts/bin/react-scripts.js build`
(workdir: `D:\vk\quant\quant\frontend`)
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add quant/frontend/src/BacktestDashboard.js quant/frontend/src/App.css
git commit -m "feat(frontend): three-section trades display — position cards, closed trades, timeline"
```

---

### Task 3: End-to-end verification

- [ ] **Step 1: Restart API server, run a backtest, verify in browser**

Start server, run a backtest via browser, verify:
1. Position summary cards appear with correct data
2. Closed trades table shows without Side/Status columns, with Return%
3. "Show Trade Timeline" toggle works, shows all BUY+SELL orders chronologically
4. Timeline shows running Position count
