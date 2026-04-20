# Strategy as Fund Manager — Design Spec

**Date:** 2026-04-19  
**Status:** Approved  
**Scope:** Live trading first (Futu + Paper Trading); backtest deferred  

## Goal

Each strategy acts like a fund manager that owns a set of positions. Positions not attributed to any strategy belong to the `default` strategy (手动交易). This concept is reflected in both the backend system and frontend UI.

## Decisions

| Question | Decision |
|----------|----------|
| Scope | Live trading (Futu + Paper) first; backtest later |
| Strategy attribution method | Order-level attribution (Approach A) |
| Capital model | Shared pool + attribution tracking only (no per-strategy budget) |
| Futu order attribution | System-placed orders carry strategy tag; external orders → `default` |
| Frontend display | Strategy position cards below account overview |
| History | Per-strategy NAV/P&L daily snapshots, stored in DuckDB |

## Architecture

### Core Concept: Order-Level Attribution

Orders are the anchor. Each order carries `strategy_name`, which propagates to fills and is used by a `StrategyPositionTracker` to derive per-strategy positions.

```
Order(strategy_name) → Fill(inherits strategy_name) → Tracker updates per-strategy position
```

### Data Model Changes

1. **OrderManager.submit_order()**: Write `strategy_name` into the Order object (field exists, currently silently dropped — 1-line fix)
2. **Fill handler**: Inherit `strategy_name` from the originating Order
3. **Trade model**: Add `strategy_name: Optional[str]` field
4. **No changes to Position model** — per-strategy positions are tracked by the new Tracker, not by tagging Position objects

### New Component: StrategyPositionTracker

In-memory service maintaining:

```python
strategy_positions: Dict[str, Dict[str, StrategyPosition]]
# key1 = strategy_name ("VolatilityRegime", "default", ...)
# key2 = symbol
```

**StrategyPosition** dataclass:
- `symbol: str`
- `strategy_name: str`
- `qty: float`
- `avg_cost: float`
- `market_value: float`
- `unrealized_pnl: float`

**Rules:**
- Same symbol can be held by multiple strategies (each tracks its own share)
- Futu returns merged positions; Tracker provides logical split view
- Calibration: if `sum(tracker positions for symbol) != Futu actual qty`, the difference goes to `default`

**Persistence:** `quant/data/strategy_positions.json` — loaded on startup, saved after every update.

### Calibration Algorithm (every position query for Futu)

```
For each symbol in Futu positions:
    total_in_tracker = sum of all strategies' qty for this symbol
    actual_in_futu = Futu returned qty
    diff = actual_in_futu - total_in_tracker
    if diff > 0:  default strategy += diff  (new manual buy)
    if diff < 0:  reduce all strategies proportionally  (external partial sell)
```

### Strategy Attribution Rules

| Source | Attribution |
|--------|------------|
| Strategy engine order (Paper or Futu) | Strategy's name (e.g., "VolatilityRegime") |
| Futu GUI / external channel | `default` (手动交易) |
| Any position Tracker has no record for | `default` |

### Strategy Daily Snapshots

New DuckDB table `strategy_snapshots`:

| Column | Type |
|--------|------|
| date | DATE |
| strategy_name | VARCHAR |
| nav | DOUBLE |
| market_value | DOUBLE |
| cash | DOUBLE |
| unrealized_pnl | DOUBLE |
| realized_pnl | DOUBLE |

- Snapshot triggered by: daily close (live) or Engine `after_trading` hook (paper)
- Loaded on startup for historical display

## API Changes

### Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `GET /api/orders` | Add `strategy` field to each order |
| `GET /api/futu/orders` | Add `strategy` field; unmatched orders → `default` |
| `GET /api/futu/positions` | Add `strategy_breakdown` dict keyed by strategy name |

### New Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/strategy-positions` | Real-time per-strategy position snapshot (paper trading) |
| `GET /api/strategy/<name>/history` | Historical NAV/P&L curve for a single strategy |
| `GET /api/strategy/all-history` | Historical summary for all strategies |

**`strategy_breakdown` response shape:**

```json
{
  "VolatilityRegime": {
    "holdings": [{"symbol": "HK.00700", "qty": 200, "avg_cost": 380.5, "market_value": 80000, "unrealized_pnl": 3900}],
    "total_market_value": 80000,
    "total_unrealized_pnl": 3900
  },
  "default": {
    "holdings": [...],
    "total_market_value": ...,
    "total_unrealized_pnl": ...
  }
}
```

## Frontend Changes

### StrategyPositionCards (new component)

Inserted between 账户总览 and CIO sections on the LIVE TRADING page.

Each strategy renders as a card:

```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ VolatilityRegime│ │ SimpleMomentum  │ │ 手动交易         │
│ Active          │ │ Active          │ │ Active           │
│                 │ │                 │ │                  │
│ 市值 HK$480,000 │ │ 市值 HK$120,000 │ │ 市值 HK$890,000  │
│ 盈亏 +HK$3,900  │ │ 盈亏 -HK$1,200  │ │ 盈亏 +HK$15,600  │
│ [迷你NAV曲线图] │ │ [迷你NAV曲线图] │ │ [迷你NAV曲线图]  │
│                 │ │                 │ │                  │
│ ▸ 持仓详情      │ │ ▸ 持仓详情      │ │ ▸ 持仓详情       │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

- **Card content:** strategy name, status badge, total market value, unrealized P&L (colored), sparkline NAV chart (pure SVG, no chart library), expandable holdings table
- **"手动交易" card:** always present, holds all unattributed positions
- **Layout:** flex wrap, responsive
- **Data source:** `strategy_breakdown` from positions endpoint + `/api/strategy/<name>/history` for sparkline

### Trade Records Table

Already has 策略 column — no changes needed.

## Data Flow

### Order Flow (Paper + Futu)

```
Strategy.buy(symbol, qty)
  → OrderManager.submit_order(strategy_name="VolatilityRegime")
    → Order(strategy_name="VolatilityRegime")
      → EventBus(ORDER_SUBMIT)
        → Broker.place_order()
          → EventBus(ORDER_FILL)
            → Fill(strategy_name inherited from Order)
              → StrategyPositionTracker.update(strategy_name, symbol, side, qty, price)
                → Save strategy_positions.json
```

### Query Flow (Futu Live)

```
Frontend GET /api/futu/positions
  → FutuBroker.get_positions_enriched()        ← merged positions from Futu
  → StrategyPositionTracker.calibrate(futu_positions)  ← reconcile
    → Differences → default
  → Return {holdings, strategy_breakdown}
```

### Daily Snapshot Flow

```
Scheduled task / after_trading hook
  → StrategyPositionTracker.snapshot_all()
    → For each strategy: compute nav, market_value, cash_share, unrealized_pnl
    → Write to DuckDB strategy_snapshots table
```

## Files Changed

| File | Change |
|------|--------|
| `quant/execution/order_manager.py` | 1-line fix: write strategy_name into Order |
| `quant/models/trade.py` | Add strategy_name field |
| `quant/execution/strategy_position_tracker.py` | **New** — Tracker service + StrategyPosition model |
| `quant/data/storage_duckdb.py` | Add strategy_snapshots table |
| `quant/api_server.py` | Modify orders/positions endpoints, add strategy endpoints |
| `quant/frontend/src/StrategyPositionCards.js` | **New** — Strategy card component with sparkline |
| `quant/frontend/src/LiveTradingPage.js` | Insert strategy cards section |

## Out of Scope (Deferred)

- Per-strategy independent capital pools
- Risk budget enforcement per strategy
- Backtest strategy-level NAV/P&L
- Strategy P&L history charts beyond sparkline
- Reassigning trades between strategies post-hoc
