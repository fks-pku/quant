# Backtest Module Decoupling Design

## Context

`engine.py` is an 818-line God Class handling 8+ distinct responsibilities:
commission, order execution, market rules, dividend processing, portfolio
creation, NAV calculation, context/order-manager internals, and CSV exporting.
Every AI bug fix session surfaces new coupling issues. Decouple into focused
modules so each has one reason to change and can be tested independently.

## Goal

Reduce `engine.py` from 818 lines to ~280 lines (pure orchestrator), extract
7 focused modules, and add unit tests for each extracted module alongside
integration tests for the engine.

## Architecture

### Target File Structure

```
quant/features/backtest/
├── entities.py           NEW  - Pure data structures + values
├── market_rules.py       NEW  - Market-specific rules registry
├── commission.py         NEW  - Commission calculation per market
├── order_executor.py     NEW  - Order execution pipeline
├── dividend_processor.py NEW  - Dividend/split + CN dividend tax
├── portfolio_factory.py  NEW  - Portfolio + RiskEngine creation
├── nav_calculator.py     NEW  - NAV computation + open positions
├── engine.py             REFACTOR (~280 lines) - Pure orchestrator
├── analytics.py          UNCHANGED
├── walkforward.py        MINOR CLEANUP
├── __init__.py
├── CLAUDE.md
```

### Module dependency chain (strict DAG, no cycles)

```
domain/models/trade.py    (existing, zero internal deps)
    ↓
entities.py               (dataclasses, dep: domain/models)
    ↓
market_rules.py           (symbol->market/lot/limit/settlement, dep: entities)
commission.py             (fee calc, dep: entities + market_rules)
    ↓
order_executor.py         (execution pipeline, dep: entities + commission + market_rules)
dividend_processor.py     (div processing, dep: entities + market_rules)
portfolio_factory.py      (setup, dep: entities)
nav_calculator.py         (nav math, dep: entities)
    ↓
engine.py                 (orchestrator, dep: all modules above)
analytics.py              (independent, dep: entities via domain)
walkforward.py            (dep: engine + analytics)
```

## Modules

### 1. `entities.py` - Pure data containers (~80 lines)

Extracted from `engine.py`: `BacktestDiagnostics`, `BacktestResult`,
`CommissionConfig`, `BacktestResultExporter`, `_BacktestContext`,
`_BacktestOrderManager`.

### 2. `market_rules.py` - Market rules registry (~60 lines)

Centralizes all `if market == "CN" / "HK" / "US"` chains:
`detect_market`, `get_lot_size`, `is_price_at_limit`, `get_settled_quantity`,
`select_currency`, `is_suspended`.

### 3. `commission.py` - Commission engine (~100 lines)

CN/HK/US commission constants + `calculate_commission(symbol, price, qty, side, config)`.

### 4. `order_executor.py` - Order execution pipeline (~130 lines)

Pipeline: slippage -> lot rounding -> volume limit -> commission -> affordability -> trade.

### 5. `dividend_processor.py` - Dividend handling (~70 lines)

`process_dividends`, `calculate_cn_dividend_tax`.

### 6. `portfolio_factory.py` - Portfolio/Risk setup (~70 lines)

`create_portfolio_contexts` - single and sub-portfolio modes.

### 7. `nav_calculator.py` - NAV + open positions (~50 lines)

`calculate_daily_nav`, `extract_open_positions`.

### 8. `engine.py` - Pure orchestrator (~280 lines)

Only owns the "when" (daily loop sequence). Every "how" delegates.

## Public API Compatibility

All current `__init__.py` exports preserved. walkforward.py and backtest_runner.py continue working unchanged.

## Test Plan

```
tests/backtest/
├── test_market_rules.py
├── test_commission.py
├── test_order_executor.py
├── test_dividend_processor.py
├── test_portfolio_factory.py
├── test_nav_calculator.py
├── test_engine.py
```
