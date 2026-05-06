# Research Pipeline Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the research pipeline into an architecture-safe, statistically rigorous, reproducible strategy research system.

**Architecture:** Keep `quant/features/research/` as orchestration and pure calculation code. Add domain ports for external research sources, market data, factor data, experiment storage, and artifacts. Implement DuckDB, filesystem, HTTP, RSS, and factor cache adapters under `quant/infrastructure/research/`.

**Tech Stack:** Python 3.10+, dataclasses, pytest, DuckDB infrastructure adapters, existing Flask composition root, existing research feature pipeline.

---

## Reference Documents

- Spec: `docs/superpowers/specs/2026-05-06-research-pipeline-upgrade-design.md`
- Root architecture: `ARCHITECTURE.md`
- Research rules: `quant/features/research/AGENTS.md`
- Domain rules: `quant/domain/AGENTS.md`
- Infrastructure rules: `quant/infrastructure/AGENTS.md`

## File Structure

### New domain ports

- Create: `quant/domain/ports/research_source.py`
- Create: `quant/domain/ports/research_market_data.py`
- Create: `quant/domain/ports/factor_data.py`
- Create: `quant/domain/ports/experiment_store.py`
- Create: `quant/domain/ports/research_artifact_store.py`
- Modify: `quant/domain/ports/__init__.py`

### Research feature changes

- Modify: `quant/features/research/models.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/features/research/scout.py`
- Modify: `quant/features/research/evaluator.py`
- Modify: `quant/features/research/config/research.yaml`
- Modify: `quant/features/research/AGENTS.md`
- Create: `quant/features/research/discovery/__init__.py`
- Create: `quant/features/research/discovery/source_hub.py`
- Create: `quant/features/research/discovery/dedup.py`
- Create: `quant/features/research/validation/__init__.py`
- Create: `quant/features/research/validation/strategy_spec_builder.py`
- Create: `quant/features/research/validation/signal_library.py`
- Create: `quant/features/research/validation/factor_validator.py`
- Create: `quant/features/research/validation/fdr.py`
- Create: `quant/features/research/validation/ff_decomposition.py`
- Create: `quant/features/research/rigor/__init__.py`
- Create: `quant/features/research/rigor/purged_cv.py`
- Create: `quant/features/research/rigor/regime_detector.py`
- Create: `quant/features/research/rigor/cost_model.py`
- Create: `quant/features/research/rigor/backtest_hub.py`
- Create: `quant/features/research/tracking/__init__.py`
- Create: `quant/features/research/tracking/run_recorder.py`
- Create: `quant/features/research/tracking/comparison.py`
- Create: `quant/features/research/ensemble/__init__.py`
- Create: `quant/features/research/ensemble/correlation_matrix.py`
- Create: `quant/features/research/ensemble/optimizer.py`
- Create: `quant/features/research/ensemble/ensemble.py`

### Infrastructure changes

- Modify: `quant/infrastructure/research/__init__.py`
- Modify: `quant/infrastructure/AGENTS.md`
- Create: `quant/infrastructure/research/duckdb_research_store.py`
- Create: `quant/infrastructure/research/duckdb_experiment_store.py`
- Create: `quant/infrastructure/research/file_artifact_store.py`
- Create: `quant/infrastructure/research/migration.py`
- Create: `quant/infrastructure/research/sources/__init__.py`
- Create: `quant/infrastructure/research/sources/arxiv_source.py`
- Create: `quant/infrastructure/research/sources/ssrn_source.py`
- Create: `quant/infrastructure/research/sources/nber_source.py`
- Create: `quant/infrastructure/research/sources/blog_source.py`
- Create: `quant/infrastructure/research/sources/rate_limiter.py`
- Create: `quant/infrastructure/research/factors/__init__.py`
- Create: `quant/infrastructure/research/factors/ff_factor_store.py`
- Create: `quant/infrastructure/research/factors/chen_zimmermann_store.py`
- Create: `quant/infrastructure/research/market_data/__init__.py`
- Create: `quant/infrastructure/research/market_data/duckdb_research_market_data.py`

### API and script wiring

- Modify: `quant/api/research_bp.py`
- Modify: `quant/scripts/run_research.py`

### Tests

- Modify: `quant/tests/test_research_pipeline.py`
- Create: `quant/tests/test_research_architecture_boundaries.py`
- Create: `quant/tests/test_research_duckdb_store.py`
- Create: `quant/tests/test_research_tracking.py`
- Create: `quant/tests/test_research_discovery.py`
- Create: `quant/tests/test_research_validation.py`
- Create: `quant/tests/test_research_rigor.py`
- Create: `quant/tests/test_research_ensemble.py`

---

### Task 1: Domain Ports and Feature Models

**Files:**
- Create: `quant/domain/ports/research_source.py`
- Create: `quant/domain/ports/research_market_data.py`
- Create: `quant/domain/ports/factor_data.py`
- Create: `quant/domain/ports/experiment_store.py`
- Create: `quant/domain/ports/research_artifact_store.py`
- Modify: `quant/domain/ports/__init__.py`
- Modify: `quant/features/research/models.py`
- Test: `quant/tests/test_research_architecture_boundaries.py`

- [ ] **Step 1: Write architecture boundary tests**

Create `quant/tests/test_research_architecture_boundaries.py` with tests that scan imports and enforce:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_files(path):
    return [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]


def test_research_feature_does_not_import_infrastructure_or_sibling_features():
    files = _python_files(ROOT / "features" / "research")
    forbidden = ("quant.infrastructure", "quant.features.backtest", "quant.features.portfolio", "quant.features.strategies", "quant.features.trading")
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_infrastructure_research_does_not_import_features():
    files = _python_files(ROOT / "infrastructure" / "research")
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "quant.features" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_domain_ports_do_not_import_external_libraries_or_features():
    files = _python_files(ROOT / "domain" / "ports")
    forbidden = ("pandas", "numpy", "duckdb", "requests", "quant.features", "quant.infrastructure")
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
```

- [ ] **Step 2: Run architecture tests and verify they fail for missing new files only if imported**

Run:

```bash
python -m pytest quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS against current code, because this test guards future edits.

- [ ] **Step 3: Add domain ports**

Create each port using `ABC`, `abstractmethod`, and `Any`/dict/list signatures exactly as specified in the design spec.

Required class names:

- `ResearchSource`
- `ResearchMarketData`
- `FactorData`
- `ExperimentStore`
- `ResearchArtifactStore`

- [ ] **Step 4: Export ports**

Modify `quant/domain/ports/__init__.py` to import and expose the new port classes in `__all__`.

- [ ] **Step 5: Add feature models**

Modify `quant/features/research/models.py` and add:

- `StrategySpec`
- `ValidationReport`
- `PurgedWalkForwardResult`
- `RegimeLabel`
- `CostEstimate`
- `RunMetadata`
- `EnsembleResult`

Extend `ResearchConfig` and `ResearchResult` with the fields listed in the design spec. Preserve all existing fields and `to_dict()` output.

- [ ] **Step 6: Verify model imports**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add quant/domain/ports quant/features/research/models.py quant/tests/test_research_architecture_boundaries.py
git commit -m "feat(research): add research ports and models"
```

---

### Task 2: DuckDB Research Store Compatibility

**Files:**
- Create: `quant/infrastructure/research/duckdb_research_store.py`
- Create: `quant/infrastructure/research/migration.py`
- Modify: `quant/infrastructure/research/__init__.py`
- Modify: `quant/api/research_bp.py`
- Modify: `quant/scripts/run_research.py`
- Test: `quant/tests/test_research_duckdb_store.py`

- [ ] **Step 1: Write compatibility tests**

Create tests that run the same behavior against `FileResearchStore` and `DuckDBResearchStore`:

- `upsert_candidate()` preserves non-candidate status.
- `get_candidate()` returns candidate dict.
- `list_by_status()` filters status.
- `update_status()` stores rejection reason.
- `has_seen()` and `mark_seen()` deduplicate discoveries.
- `write_discoveries()`, `write_evaluations()`, and `save_run_result()` do not raise.

- [ ] **Step 2: Run tests and verify DuckDB implementation is missing**

Run:

```bash
python -m pytest quant/tests/test_research_duckdb_store.py -q
```

Expected: FAIL with import error for `DuckDBResearchStore`.

- [ ] **Step 3: Implement `DuckDBResearchStore`**

Implement `ResearchStore` using the schema in the design spec. Keep markdown artifact writing compatible with `FileResearchStore` by writing `discovered_strategies.md`, `strategy_evaluation.md`, and run result JSON files under the same root directory.

- [ ] **Step 4: Implement JSON migration helper**

Create a function:

```python
def migrate_file_research_store(json_path: Path | str, duckdb_store: DuckDBResearchStore) -> dict:
    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for info in data.get("candidates", {}).values():
        duckdb_store.upsert_candidate(dict(info))
    for strategy_hash, raw in data.get("seen_hashes", {}).items():
        duckdb_store.mark_seen(strategy_hash, SimpleNamespace(**raw))
    return {
        "candidates": len(data.get("candidates", {})),
        "seen_hashes": len(data.get("seen_hashes", {})),
    }
```

Return counts:

- `candidates`
- `seen_hashes`

- [ ] **Step 5: Wire optional store selection**

In API and script composition roots, select `DuckDBResearchStore` only when config requests DuckDB. Default behavior remains `FileResearchStore` until a later migration flips it.

- [ ] **Step 6: Verify compatibility**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_duckdb_store.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add quant/infrastructure/research quant/api/research_bp.py quant/scripts/run_research.py quant/tests/test_research_duckdb_store.py
git commit -m "feat(research): add duckdb research store"
```

---

### Task 3: Experiment Tracking

**Files:**
- Create: `quant/infrastructure/research/duckdb_experiment_store.py`
- Create: `quant/infrastructure/research/file_artifact_store.py`
- Create: `quant/features/research/tracking/__init__.py`
- Create: `quant/features/research/tracking/run_recorder.py`
- Create: `quant/features/research/tracking/comparison.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/api/research_bp.py`
- Test: `quant/tests/test_research_tracking.py`

- [ ] **Step 1: Write experiment store tests**

Cover:

- `start_run()` returns a non-empty run id.
- `record_metrics()` inserts metrics with `run_id`, `strategy_id`, `metric_name`, `metric_value`, `window_type`, and `window_label`.
- `complete_run()` updates status and completed timestamp.
- `list_runs()` orders newest first.
- `list_metrics()` returns stored metrics.
- `FileArtifactStore.save_json()` stores a JSON artifact and returns metadata.

- [ ] **Step 2: Run tests and verify missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_tracking.py -q
```

Expected: FAIL with import errors for tracking implementations.

- [ ] **Step 3: Implement infrastructure stores**

Implement `DuckDBExperimentStore` against `ExperimentStore`. Implement `FileArtifactStore` against `ResearchArtifactStore`. Keep all DuckDB and filesystem code in infrastructure.

- [ ] **Step 4: Implement run recorder**

`RunRecorder` must provide:

- `hash_config(config: dict) -> str`
- `hash_data(data_summary: dict) -> str`
- `get_code_version() -> str`

When git is unavailable, `get_code_version()` returns `"unknown"` instead of raising.

- [ ] **Step 5: Wire run lifecycle into `ResearchEngine`**

Add optional `experiment_store` and `artifact_store` dependencies to `ResearchEngine`. When tracking is enabled:

- Start a run before scout.
- Store `run_id` on `ResearchResult`.
- Complete the run after pipeline success.
- Mark failed if an unhandled exception escapes.

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_tracking.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add quant/features/research/tracking quant/infrastructure/research/duckdb_experiment_store.py quant/infrastructure/research/file_artifact_store.py quant/features/research/research_engine.py quant/api/research_bp.py quant/tests/test_research_tracking.py
git commit -m "feat(research): record experiment runs"
```

---

### Task 4: Discovery Hub

**Files:**
- Create: `quant/features/research/discovery/__init__.py`
- Create: `quant/features/research/discovery/source_hub.py`
- Create: `quant/features/research/discovery/dedup.py`
- Modify: `quant/features/research/scout.py`
- Create: `quant/infrastructure/research/sources/__init__.py`
- Create: `quant/infrastructure/research/sources/arxiv_source.py`
- Create: `quant/infrastructure/research/sources/nber_source.py`
- Create: `quant/infrastructure/research/sources/ssrn_source.py`
- Create: `quant/infrastructure/research/sources/blog_source.py`
- Create: `quant/infrastructure/research/sources/rate_limiter.py`
- Test: `quant/tests/test_research_discovery.py`

- [ ] **Step 1: Write source hub tests**

Cover:

- Source dicts are normalized into `RawStrategy`.
- Missing optional fields become `None`.
- Duplicate title/description pairs are removed.
- Unknown source names are skipped.
- Adapter exceptions are logged and do not crash search.

- [ ] **Step 2: Run tests and verify missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_discovery.py -q
```

Expected: FAIL with import error for `SourceHub`.

- [ ] **Step 3: Implement feature discovery hub**

`SourceHub` accepts `Dict[str, ResearchSource]`. It does not import infrastructure adapters.

- [ ] **Step 4: Keep `StrategyScout` compatible**

Modify `StrategyScout` so existing callers can still call:

```python
StrategyScout().search(sources=None, max_results=10)
```

The default constructor may keep current local adapters until API/script wiring injects infrastructure sources.

- [ ] **Step 5: Implement infrastructure source adapters**

Move external HTTP/RSS logic into infrastructure adapters. SSRN and blog adapters must return an empty list on blocked or unsupported responses instead of raising.

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_discovery.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add quant/features/research/discovery quant/features/research/scout.py quant/infrastructure/research/sources quant/tests/test_research_discovery.py
git commit -m "feat(research): add source hub"
```

---

### Task 5: StrategySpec and Statistical Validation

**Files:**
- Create: `quant/features/research/validation/__init__.py`
- Create: `quant/features/research/validation/strategy_spec_builder.py`
- Create: `quant/features/research/validation/signal_library.py`
- Create: `quant/features/research/validation/factor_validator.py`
- Create: `quant/features/research/validation/fdr.py`
- Create: `quant/features/research/validation/ff_decomposition.py`
- Create: `quant/infrastructure/research/market_data/duckdb_research_market_data.py`
- Create: `quant/infrastructure/research/factors/ff_factor_store.py`
- Create: `quant/infrastructure/research/factors/chen_zimmermann_store.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/features/research/evaluator.py`
- Test: `quant/tests/test_research_validation.py`

- [ ] **Step 1: Write StrategySpec builder tests**

Cover:

- Momentum evaluation maps to `momentum_close_return`.
- Mean reversion evaluation maps to `mean_reversion_close_to_ma`.
- Breakout evaluation maps to `volatility_breakout_atr`.
- Unknown strategy type returns `status="unsupported_type"`.
- Missing formula returns `status="missing_formula"`.

- [ ] **Step 2: Write FDR tests**

Use deterministic p-values:

```python
p_values = [0.001, 0.02, 0.04, 0.20]
```

Assert Benjamini-Hochberg at `alpha=0.05` marks the first three as significant and the last one as not significant.

- [ ] **Step 3: Write validator tests**

Use a small deterministic OHLCV frame returned by a fake `ResearchMarketData`. Cover:

- Close-based signal applies execution lag.
- Insufficient observations returns `status="insufficient_data"`.
- Supported formula returns a `ValidationReport`.
- Unsupported spec does not call market data.

- [ ] **Step 4: Run validation tests and verify missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_validation.py -q
```

Expected: FAIL with import errors for validation modules.

- [ ] **Step 5: Implement validation modules**

Keep formula execution inside `signal_library.py`. Do not evaluate arbitrary formula strings from config. Config maps strategy type to approved formula keys only.

- [ ] **Step 6: Wire Gate 2**

Modify `ResearchEngine`:

- Run spec builder after semantic evaluation.
- Increment `specified`.
- Increment `needs_manual_spec` and skip integration for unsupported specs.
- Run validator for `status="ready"`.
- Reject only when a ready spec fails statistical thresholds.

- [ ] **Step 7: Verify**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_validation.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add quant/features/research/validation quant/infrastructure/research/market_data quant/infrastructure/research/factors quant/features/research/research_engine.py quant/features/research/evaluator.py quant/tests/test_research_validation.py
git commit -m "feat(research): add statistical validation gate"
```

---

### Task 6: Walk-Forward Rigor

**Files:**
- Create: `quant/features/research/rigor/__init__.py`
- Create: `quant/features/research/rigor/purged_cv.py`
- Create: `quant/features/research/rigor/regime_detector.py`
- Create: `quant/features/research/rigor/cost_model.py`
- Create: `quant/features/research/rigor/backtest_hub.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/api/research_bp.py`
- Modify: `quant/scripts/run_research.py`
- Test: `quant/tests/test_research_rigor.py`

- [ ] **Step 1: Write purged CV tests**

Cover:

- Every split has `train_end < test_start`.
- Gap between train end and test start is at least `purge_days`.
- Splits are empty when data length is below minimum.
- Embargo shifts the next split as configured.

- [ ] **Step 2: Write cost model tests**

Cover:

- `capacity_ok` is false when trade value exceeds max ADV percent.
- `total_bps` increases with volatility and participation rate.
- Zero volume returns a failed capacity check without division by zero.

- [ ] **Step 3: Write RigorHub tests**

Use a fake backtest runner returning deterministic metrics. Cover:

- Runner is called once per emitted split.
- `worst_oos_sharpe` equals the minimum test split Sharpe.
- `deflated_sharpe_ratio` remains `None` in MVP.
- Candidate fails when `worst_oos_sharpe` is below threshold.

- [ ] **Step 4: Run rigor tests and verify missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_rigor.py -q
```

Expected: FAIL with import errors for rigor modules.

- [ ] **Step 5: Implement rigor modules**

Keep `backtest_hub.py` dependent only on injected callable, domain ports, and research models.

- [ ] **Step 6: Adapt composition roots**

Update `quant/api/research_bp.py` and `quant/scripts/run_research.py` to provide the new backtest runner request/response shape. Keep the legacy backtest path until all tests use the new callback.

- [ ] **Step 7: Verify**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_rigor.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add quant/features/research/rigor quant/features/research/research_engine.py quant/api/research_bp.py quant/scripts/run_research.py quant/tests/test_research_rigor.py
git commit -m "feat(research): add walk-forward rigor"
```

---

### Task 7: Ensemble and Research API

**Files:**
- Create: `quant/features/research/ensemble/__init__.py`
- Create: `quant/features/research/ensemble/correlation_matrix.py`
- Create: `quant/features/research/ensemble/optimizer.py`
- Create: `quant/features/research/ensemble/ensemble.py`
- Modify: `quant/features/research/research_engine.py`
- Modify: `quant/api/research_bp.py`
- Test: `quant/tests/test_research_ensemble.py`

- [ ] **Step 1: Write ensemble tests**

Cover:

- Fewer than two strategies returns a no-op result.
- Correlation matrix is symmetric.
- Equal-risk weights sum to 1.0.
- Max weight cap is enforced.
- Ensemble result can be serialized by `ResearchResult.to_dict()`.

- [ ] **Step 2: Run ensemble tests and verify missing implementation**

Run:

```bash
python -m pytest quant/tests/test_research_ensemble.py -q
```

Expected: FAIL with import errors for ensemble modules.

- [ ] **Step 3: Implement ensemble modules**

Use dictionaries/lists at port boundaries. Convert to pandas/numpy inside feature code only after data is returned by injected stores.

- [ ] **Step 4: Add API endpoints**

Add:

- `GET /api/research/experiments/<strategy_id>`
- `GET /api/research/experiments/<strategy_id>/<run_id>`
- `GET /api/research/compare?ids=strategy_a,strategy_b&metric=sharpe`
- `GET /api/research/ensemble`
- `POST /api/research/ensemble/rebuild`

- [ ] **Step 5: Verify**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_ensemble.py quant/tests/test_research_tracking.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add quant/features/research/ensemble quant/features/research/research_engine.py quant/api/research_bp.py quant/tests/test_research_ensemble.py
git commit -m "feat(research): add research ensemble"
```

---

### Task 8: Documentation, Encoding, and Full Verification

**Files:**
- Modify: `quant/features/research/AGENTS.md`
- Modify: `quant/domain/AGENTS.md`
- Modify: `quant/infrastructure/AGENTS.md`
- Modify: `docs/superpowers/specs/2026-05-06-research-pipeline-upgrade-design.md` only if implementation discovers a spec mismatch

- [ ] **Step 1: Update AGENTS.md files**

Update only the relevant sections:

- Research L1 contract for new modules, ports, and boundaries.
- Domain recent additions for new ports.
- Infrastructure research adapters for DuckDB, source, factor, market-data, and artifact stores.

- [ ] **Step 2: Run focused tests**

Run:

```bash
python -m pytest quant/tests/test_research_pipeline.py quant/tests/test_research_duckdb_store.py quant/tests/test_research_tracking.py quant/tests/test_research_discovery.py quant/tests/test_research_validation.py quant/tests/test_research_rigor.py quant/tests/test_research_ensemble.py quant/tests/test_research_architecture_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
python -m pytest quant/tests/ -q
```

Expected: PASS or report pre-existing unrelated failures with exact failing tests.

- [ ] **Step 4: Run encoding check**

Run:

```bash
python scripts/check_text_encoding.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quant/features/research/AGENTS.md quant/domain/AGENTS.md quant/infrastructure/AGENTS.md docs/superpowers/specs/2026-05-06-research-pipeline-upgrade-design.md
git commit -m "docs(research): update research pipeline contracts"
```

## Self-Review Checklist

- [ ] Every new external I/O implementation is under `quant/infrastructure/research/`.
- [ ] Feature code has no direct DuckDB, filesystem persistence, HTTP, or sibling feature imports.
- [ ] Domain ports expose no pandas/numpy/DuckDB/requests types.
- [ ] Unsupported statistical specs become `needs_manual_spec` rather than automatic rejects.
- [ ] `min(test_sharpe)` is named `worst_oos_sharpe`, not deflated Sharpe.
- [ ] Existing research pipeline behavior still works when all new modules are disabled.
- [ ] Full test command and encoding check have been run before completion.
