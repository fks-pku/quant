# Backtest Open Positions Display Design

## Problem

Frontend backtest dashboard only shows completed (SELL) trades. BUY orders that remain open at the end of the backtest period are invisible — filtered out by `backtest_bp.py:125` (`if t.side != "SELL": continue`).

## Decision

**Paired display** — each row represents a complete trade lifecycle. Open positions show entry info + current price with unrealized P&L, visually distinguished from closed trades.

## Data Flow

### Layer 1: Engine (features/backtest/engine.py)

BacktestResult gains an `open_positions` field:

```python
@dataclass
class BacktestResult:
    # ... existing fields ...
    open_positions: List[Dict] = field(default_factory=list)
```

Each open position dict:

```python
{
    "symbol": str,
    "quantity": float,
    "entry_price": float,    # pos.avg_cost
    "entry_time": datetime,  # from entry_times dict
    "current_price": float,  # last bar close price
    "unrealized_pnl": float, # (current_price - avg_cost) * quantity
    "market_value": float,   # quantity * current_price
}
```

Extraction point: after equity_curve computation (line ~283), before BacktestResult construction. Iterate `portfolio.positions` where `pos.quantity > 0`, pulling entry metadata from the engine's `entry_times` and `last_prices` dicts.

### Layer 2: API (api/backtest_bp.py)

- Serialize SELL trades with `"status": "closed"` (existing logic, add status field)
- Serialize open_positions with `"status": "open"` (new)
- Merge into a single `trades` list for the frontend

API response trade object (unified):

```json
{
    "entry_time": "2024-01-05 00:00:00",
    "exit_time": "2024-01-15 00:00:00",
    "symbol": "AAPL",
    "side": "BUY",
    "status": "closed",
    "entry_price": 150.0,
    "exit_price": 155.0,
    "quantity": 100,
    "pnl": 450.0
}
```

Open position trade object:

```json
{
    "entry_time": "2024-06-01 00:00:00",
    "exit_time": null,
    "symbol": "MSFT",
    "side": "BUY",
    "status": "open",
    "entry_price": 380.0,
    "exit_price": 395.0,
    "quantity": 50,
    "pnl": 750.0
}
```

### Layer 3: Frontend (frontend/src/BacktestDashboard.js)

Table columns:

| Column | Closed Trade | Open Position |
|--------|-------------|---------------|
| Status | badge "已完成" green | badge "持仓中" blue |
| Entry Date | entry_time | entry_time |
| Exit Date | exit_time | `-` |
| Symbol | symbol | symbol |
| Side | BUY (green) | BUY (green) |
| Qty | quantity | quantity |
| Entry Price | entry_price | entry_price |
| Exit Price | exit_price | current_price (blue text) |
| P&L | pnl (green/red) | unrealized_pnl (orange/blue) |

Visual distinction for open positions: left border color + light background tint.

## Files Changed

| File | Change |
|------|--------|
| `features/backtest/engine.py` | BacktestResult.open_positions field + extraction logic at end of run() |
| `api/backtest_bp.py` | Merge closed + open trades into unified list with status field |
| `frontend/src/BacktestDashboard.js` | Status column + open position row styling |

## Files NOT Changed

- `domain/models/trade.py` — Trade model untouched
- `features/backtest/analytics.py` — Performance metrics still computed from SELL trades only
- Frontend charts — Not affected
