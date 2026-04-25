# Backtest Trades Display Redesign

## Problem

Current trades table is a flat list that mixes closed trades and open positions. Issues:
- Repetitive rows with identical entry data (same symbol/date/price)
- Redundant Side column (always "BUY")
- No visibility into how positions accumulated
- Open positions buried at the bottom

## Solution: Three-Section Layout

### Section 1: Position Summary Cards (top)

One card per open position symbol, showing current exposure at a glance.

**Data source:** `result.open_positions` (already in API response) + `result.metrics.final_nav` for weight calculation.

Each card displays:
- Symbol name
- Quantity held
- Avg cost (entry_price)
- Current price (exit_price field, which holds current_price for open positions)
- Market value (quantity × current_price) — computed frontend-side
- Unrealized P&L (pnl field) with color
- Weight % (market_value / final_nav × 100) — computed frontend-side

Styling: grid of cards, each with a left border color indicator (green for profit, red for loss, cyan accent). Only shown when open positions exist.

### Section 2: Closed Trades Table (middle)

Existing table, refined:
- **Remove** Side column (redundant — all are round-trip long positions)
- **Remove** Status column (this section only shows closed trades)
- **Add** Return% column: `(exit_price - entry_price) / entry_price × 100`
- Columns: Entry Date | Exit Date | Symbol | Qty | Entry Price | Exit Price | P&L | Return% | Duration
- Data: filtered to `status === "closed"` trades only

### Section 3: Trade Timeline (bottom, collapsible)

Chronological audit trail showing ALL orders (BUY and SELL). Answers "how did this position accumulate?"

**Data source:** New `trade_timeline` field in API response (see Backend Changes).

Each row:
- Date (fill timestamp)
- Action (BUY/SELL, with color)
- Symbol
- Quantity
- Price (fill_price)
- Position (running total after this trade)
- P&L (only for SELL trades; "—" for BUY)

Default collapsed, expand via "Show Trade Timeline" toggle.

## Backend Changes

### API: `backtest_bp.py`

Add `trade_timeline` field to the result dict. This contains ALL trades (BUY + SELL) in chronological order:

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
        "price": float(t.fill_price or t.entry_price if t.side == "BUY" else t.exit_price),
        "position": int(position_tracker.get(t.symbol, 0)),
        "pnl": float(t.pnl) if t.side == "SELL" else None,
    })
```

Stored in result dict as `"trade_timeline": timeline_list`.

### No engine changes needed

All data already exists in `result.trades` (both BUY and SELL Trade objects).

## Frontend Changes

### `BacktestDashboard.js`

Replace the current single trades section with three sections:

1. **Position Cards** — grid layout, only shown when `trades.filter(t => t.status === 'open').length > 0`
2. **Closed Trades** — table with removed Side/Status columns, added Return%
3. **Trade Timeline** — collapsible section using `result.trade_timeline`

### `App.css`

- Position card styles (`.bt-position-card`, etc.)
- Timeline table styles (`.bt-timeline-table`, etc.)
- Collapse/expand toggle styles

## Files Changed

| File | Change |
|------|--------|
| `api/backtest_bp.py` | Add `trade_timeline` to result dict |
| `frontend/src/BacktestDashboard.js` | Three-section layout |
| `frontend/src/App.css` | New section styles |

## Files NOT Changed

- `features/backtest/engine.py` — no engine changes
- `domain/models/` — no model changes
- `features/backtest/analytics.py` — metrics unchanged
