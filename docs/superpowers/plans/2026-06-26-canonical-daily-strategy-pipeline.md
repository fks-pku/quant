# Canonical Daily Strategy Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backtest, paper, and live daily strategies consume the same strategy data contract and shared close-snapshot runner, with DB used only for paper/live checkpoint persistence and external execution facts.

**Architecture:** The existing shared runner `quant/runtime/daily_strategy_runner.py` is the canonical strategy lifecycle entry for D-close signal generation. This upgrade adds a runtime-level required-field contract, teaches strategies to declare their PIT bar fields, and makes paper/live DuckDB snapshot loading use those declarations so missing data fails loudly instead of silently suppressing signals.

**Tech Stack:** Python 3.10+, DuckDB, pandas, pytest, existing hexagonal runtime/trading/backtest modules.

---

## File Structure

- `quant/runtime/daily_strategy_runner.py`: add required-field normalization, field validation, and result metadata at the shared signal runner.
- `quant/features/strategies/base.py`: add default `required_fields` declaration surface for all strategies.
- `quant/features/strategies/xueqiu_small_cap_financial_filter/strategy.py`: declare the daily_basic/status fields required for candidate filtering.
- `quant/infrastructure/data/providers/duckdb_provider.py`: pass sidecar path/config into `DuckDBStorage` and expose a field-aware provider surface for paper/live snapshots.
- `quant/infrastructure/data/storage_duckdb.py`: keep the CN daily bar sidecar fields broad enough for promoted strategies and expose available sidecar fields consistently.
- `quant/quant_system.py`: select/load snapshot bars with the combined strategy required fields so paper/live use the same PIT field contract as strict backtests.
- `quant/tests/test_daily_strategy_runner.py`: add runtime invariant tests for field contract failures.
- `quant/tests/test_daily_basic_sidecar_data.py`: extend DuckDB provider tests to cover field-aware snapshot loading.
- `quant/tests/test_xueqiu_small_cap_financial_filter.py`: assert the promoted small-cap strategy declares financial/status fields.
- `quant/features/trading/docs/trading-invariants.md`: document the paper/live/backtest data-contract invariant.
- `quant/features/backtest/docs/backtest-invariants.md`: keep existing shared-runner invariant aligned with field validation.
- `quant/infrastructure/AGENTS.md`: document that provider changes must satisfy strategy `required_fields`.

## Task 1: Runtime Required-Field Contract

- [ ] **Step 1: Write failing tests**

Add tests to `quant/tests/test_daily_strategy_runner.py`:

```python
def test_daily_snapshot_runner_rejects_missing_required_fields():
    strategy = FieldRequiredStrategy(["600519"], ["pe_ttm"])
    bars = [{"symbol": "600519", "timestamp": date(2026, 6, 22), "close": 10.0}]

    results = run_daily_snapshots([strategy], date(2026, 6, 22), bars, strict=True)

    assert results[0][1].ran is False
    assert results[0][1].missing_fields == ("pe_ttm",)
    assert strategy.after_calls == 0
```

```python
def test_daily_snapshot_runner_accepts_declared_required_fields():
    strategy = FieldRequiredStrategy(["600519"], ["pe_ttm"])
    bars = [{"symbol": "600519", "timestamp": date(2026, 6, 22), "close": 10.0, "pe_ttm": 9.5}]

    results = run_daily_snapshots([strategy], date(2026, 6, 22), bars, strict=True)

    assert results[0][1].ran is True
    assert results[0][1].missing_fields == ()
    assert strategy.after_calls == 1
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest quant/tests/test_daily_strategy_runner.py -q`

Expected before implementation: failure because `DailyRunResult` has no `missing_fields`.

- [ ] **Step 3: Implement runtime contract**

In `quant/runtime/daily_strategy_runner.py`:

```python
missing_fields = _missing_required_fields(strategy, snapshot)
if strict and (snapshot.missing_symbols or snapshot.stale_symbols or missing_fields):
    results[index] = (... DailyRunResult(..., missing_fields=missing_fields))
    continue
```

Support `strategy.required_fields` as property/list/callable, treat field names case-sensitively after stripping, and validate key presence rather than non-null values.

- [ ] **Step 4: Run passing tests**

Run: `python -m pytest quant/tests/test_daily_strategy_runner.py -q`

Expected: pass.

## Task 2: Strategy Field Declarations

- [ ] **Step 1: Write failing tests**

Add or extend strategy tests:

```python
def test_xueqiu_small_cap_declares_financial_and_status_fields():
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["600519"])
    fields = set(strategy.required_fields)

    assert {"total_mv", "circ_mv", "pe_ttm", "pe", "ps_ttm", "ps"}.issubset(fields)
    assert {"is_st", "tradable", "has_daily_bar", "is_listed", "list_status"}.issubset(fields)
```

- [ ] **Step 2: Run failing strategy test**

Run: `python -m pytest quant/tests/test_xueqiu_small_cap_financial_filter.py -q`

Expected before implementation: failure because the strategy does not declare the fields.

- [ ] **Step 3: Implement declarations**

Add default `Strategy.required_fields -> []` in `quant/features/strategies/base.py`, and add Xueqiu fields in `quant/features/strategies/xueqiu_small_cap_financial_filter/strategy.py`.

- [ ] **Step 4: Run passing strategy tests**

Run: `python -m pytest quant/tests/test_xueqiu_small_cap_financial_filter.py -q`

Expected: pass.

## Task 3: Paper/Live Snapshot Provider Uses Strategy Contract

- [ ] **Step 1: Write failing tests**

Extend `quant/tests/test_daily_basic_sidecar_data.py` with a provider-level test that reads `pe_ttm`, `ps_ttm`, `total_mv`, `circ_mv`, and status fields through the public DuckDB provider path used by `QuantSystem._load_snapshot_bars`.

- [ ] **Step 2: Run failing provider test**

Run: `python -m pytest quant/tests/test_daily_basic_sidecar_data.py -q`

Expected before implementation: failure if provider/config does not pass sidecar paths or field set consistently.

- [ ] **Step 3: Implement provider plumbing**

Modify `quant/infrastructure/data/providers/duckdb_provider.py` and `quant/quant_system.py` so snapshot loading passes the union of `strategy.required_fields` into the provider when supported. Provider/storage must return keys for all known daily_basic/status fields required by promoted strategies.

- [ ] **Step 4: Run passing provider tests**

Run: `python -m pytest quant/tests/test_daily_basic_sidecar_data.py -q`

Expected: pass.

## Task 4: Docs and Invariant Contract

- [ ] **Step 1: Update docs**

Document:

```text
Backtest, paper, and live D-close signal generation share run_daily_snapshots().
Each strategy declares required_fields.
Paper/live providers must load those PIT fields or the shared runner marks the strategy not runnable.
DB may persist checkpoints and execution facts only; it cannot alter universe, bar fields, filters, or lifecycle semantics.
```

- [ ] **Step 2: Run invariant docs tests**

Run: `python -m pytest quant/tests/test_invariant_docs_contract.py -q`

Expected: pass.

## Task 5: Focused Regression Suite

- [ ] **Step 1: Run focused tests**

Run:

```bash
python -m pytest quant/tests/test_daily_strategy_runner.py quant/tests/test_xueqiu_small_cap_financial_filter.py quant/tests/test_daily_basic_sidecar_data.py quant/tests/test_invariant_docs_contract.py -q
```

Expected: pass.

- [ ] **Step 2: Run diff hygiene**

Run:

```bash
git diff --check
```

Expected: no output.

## Self-Review

- Spec coverage: shared runner field validation covers pipeline parity; strategy field declarations cover new-strategy extensibility; provider plumbing covers paper/live data parity; docs/tests cover future regressions.
- No placeholders: all tasks name concrete files, commands, and expected outcomes.
- Type consistency: `required_fields` is a list-like property or callable; `DailyRunResult.missing_fields` is a tuple of field names.
