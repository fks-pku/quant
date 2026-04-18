# Backtest Realism Improvements Design

## Problem

Current `core/backtester.py` assumes ideal trading conditions:
- No suspension handling (trades on halted stocks at last close)
- Same-day signal+execution (look-ahead bias)
- Unlimited liquidity
- No lot size enforcement
- Simplified commission model
- No data quality validation

## Scope

Pragmatic improvements to make backtests predictive of live performance. Not a full execution engine rewrite.

## Design

### 1. Data Layer

#### 1a. Suspension Detection (runtime)

Detect suspended bars at backtest time (no schema change):
- `volume == 0` AND `close == prev_close` → suspended
- Suspended symbols: skip all trading (no buy, no sell)
- Track `suspended_days` in result diagnostics

#### 1b. Data Quality Validation

Add `validate()` to `_DataFrameProvider`, run once at backtest start:
- OHLC logic: `high >= max(open,close)`, `low <= min(open,close)`
- Negative or zero prices
- 20+ consecutive bars with same close AND nonzero volume → data source issue
- Reports issues as warnings, does not block execution

### 2. Execution Layer

#### 2a. T+1 Execution (eliminate look-ahead)

Signal generated at `on_after_trading` (T-day close) → executed at T+1 open price.

Implementation:
- Pending orders from `on_after_trading` are queued
- On next trading day, orders execute at that day's `open` price
- If T+1 is suspended for that symbol → defer to T+2 (max defer: 5 days, then cancel)
- `avg_fill_delay_days` tracked in diagnostics

#### 2b. HK Realistic Commission Model

Replace flat 0.1% with component-based model:

| Component | Rate | Direction |
|-----------|------|-----------|
| Commission | 0.03% (configurable) | Both |
| Stamp duty | 0.13% | Sell only |
| SFC levy | 0.00278% | Both |
| HKEX clearing | 0.002% | Both |
| Trading fee | 0.005% | Both |
| **Buy total** | ~0.03978% | |
| **Sell total** | ~0.16978% | |

US model unchanged (per-share commission).

#### 2c. Lot Size Enforcement

Buy quantity rounded down to nearest `lot_size` multiple.
- `instrument_meta` table in DuckDB: `symbol VARCHAR, lot_size INT, market VARCHAR`
- Default lot_size: 100
- Scripts should populate this table when fetching data

#### 2d. Volume Constraint

Max buy quantity = 5% of bar's daily volume.
- Excess quantity not executed (no partial fill splitting)
- `volume_limited_trades` tracked in diagnostics

### 3. Output Layer

#### 3a. Extended Trade Record

`Trade` dataclass gains fields:
- `signal_date` - when signal was generated
- `fill_date` - actual execution date
- `fill_price` - execution price (T+1 open)
- `intended_qty` - requested quantity
- `actual_qty` - executed quantity (after lot/volume adjustments)
- `cost_breakdown` - dict {commission, stamp_duty, levy, clearing, trading_fee}

#### 3b. Backtest Diagnostics

New `BacktestDiagnostics` dataclass:
- `suspended_days` - total suspended-symbol trading days skipped
- `volume_limited_trades` - orders reduced/cancelled due to volume
- `lot_adjusted_trades` - orders adjusted for lot size
- `avg_fill_delay_days` - mean signal-to-fill delay
- `turnover_rate` - annual portfolio turnover
- `cost_drag_pct` - total costs / gross return

## Files Changed

| File | Change |
|------|--------|
| `core/backtester.py` | Major: T+1 execution, suspension, volume constraint, lot size, commission model, diagnostics |
| `core/walkforward.py` | Minor: add validate() to _DataFrameProvider |
| `data/storage_duckdb.py` | Minor: add instrument_meta table |
| `scripts/prepare_data.py` | Medium: fetch lot_size via get_static_info, store in instrument_meta |
| `models/trade.py` | Minor: extend Trade dataclass |
| `backtest_runner.py` | Minor: adapt to new interface |

## What We Are NOT Doing

- Full Almgren-Chriss market impact model
- T+0/T+1 settlement rules
- Dividend/split event handling (Futu qfq handles this)
- Borrowing/margin costs
- Stop-loss/take-profit order types
- Bar-level matching engine
