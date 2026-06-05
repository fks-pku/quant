# Strategy Mode Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each `strategy_name × mode` own its operation, signal, order, fill, and snapshot records, and make the strategy dashboard read only that mode-scoped ledger.

**Architecture:** Add a small append-only `StrategyModeRecordStore` under infrastructure execution. Existing daily JSONL files remain compatibility inputs, but dashboard materializes them into the canonical store before rendering and then derives all mode data from the store.

**Tech Stack:** Python JSONL files, existing Flask dashboard server, pytest.

---

### Task 1: Canonical Store

**Files:**
- Create: `quant/infrastructure/execution/strategy_mode_records.py`
- Test: `quant/tests/test_strategy_operations_ledger.py`

- [ ] Add a focused store that writes files under `quant/infrastructure/var/strategy_modes/<mode>/<strategy_name>/<kind>.jsonl`.
- [ ] Support `operations`, `signals`, `orders`, `fills`, and `snapshots`.
- [ ] Add idempotent append for legacy materialization.
- [ ] Test that strategy and mode records are isolated.

### Task 2: Writers

**Files:**
- Modify: `quant/infrastructure/execution/live_recorder.py`
- Modify: `quant/infrastructure/execution/strategy_controls.py`
- Test: `quant/tests/test_live_trading_records.py`

- [ ] Make `LiveTradingRecorder` dual-write signal/order/fill/snapshot rows to the canonical store.
- [ ] Make dashboard control actions append mode-scoped operation rows.
- [ ] Test that paper and live writes land in separate mode ledgers.

### Task 3: Dashboard Source

**Files:**
- Modify: `quant/scripts/strategy_dashboard_server.py`
- Test: `quant/tests/test_strategy_dashboard_server.py`

- [ ] Before dashboard derivation, materialize legacy daily records and audit/control state into the canonical store.
- [ ] Replace dashboard record reads with canonical store reads.
- [ ] Derive control display from operation rows, using configured defaults only when no explicit operation exists.
- [ ] Test that dashboard ignores cross-strategy and cross-mode records.

### Task 4: Invariants

**Files:**
- Modify: `quant/features/trading/docs/trading-invariants.md`
- Modify: `quant/features/trading/AGENTS.md`

- [ ] Document that dashboard state uses the canonical strategy-mode ledger as its single source.
- [ ] Run targeted dashboard, recorder, ledger, invariant-doc, and encoding checks.
