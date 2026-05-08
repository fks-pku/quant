# Research Pipeline Module C: Regime Detection, Capacity Gate, SSRN Adapter, IC Decay Gate

**Date**: 2026-05-08
**Author**: Quantitative Research
**Status**: Ready for implementation
**Parent spec**: `2026-05-06-research-pipeline-upgrade-design.md`
**Scope**: Module C of 4-module upgrade. Wires regime labels into walk-forward, integrates capacity into viability, implements SSRN source, and gates on IC decay.

## Problem Statement

Four independent gaps in the research rigor pipeline:

1. **Regime detection is placeholder quality.** `regime_detector.py` computes 200-day SMA regime segments, but the results are never consumed by `backtest_hub.py`. Walk-forward splits run without regime context, so bull-only strategies pass viability gates without warning.

2. **Capacity gate is computed but ignored.** `CostEstimate.capacity_ok` is calculated in `cost_model.py` but `backtest_hub.py` does not check it when deciding `is_viable`. Strategies that exceed ADV limits can pass.

3. **SSRN source adapter is a stub.** `ssrn_source.py` logs a warning and returns `[]`. The research pipeline misses an entire academic source.

4. **IC decay is computed (Module A) but not gated.** Module A fills `ValidationReport.ic_decay` with actual values. This module wires those values into a pipeline warning: if IC decays > 50% from 1-day to 21-day horizon, the strategy is flagged.

## Design

### 1. Regime Detection Implementation

**File**: `quant/features/research/rigor/regime_detector.py`

The current `detect_regimes()` function returns regime segments. This is fine for visualization but does not label individual walk-forward splits. Add a second function:

#### New function: `label_split_regime`

```python
def label_split_regime(
    split: Dict[str, Any],
    benchmark_data: Any,
    sma_lookback: int = 200,
) -> str:
```

Logic:

- Extract `test_start` and `test_end` from the split dict.
- Slice benchmark close prices to the test window.
- Compute 200-day SMA over the benchmark series (requires history before test_start).
- Count trading days where `close > sma` vs `close < sma` within the test window.
- Return `"bull"` if bullish days >= 50%, else `"bear"`.
- Return `"unknown"` if benchmark data is insufficient (< sma_lookback rows) or on any exception.

#### Benchmark selection

The caller (backtest_hub) must supply benchmark data. A helper determines the benchmark symbol:

| Universe prefix | Benchmark |
|-----------------|-----------|
| Symbols ending in `.HK` or starting with `0-9` (CN 6-digit) | `000300` (CSI 300) |
| Symbols containing HSI | `HSI` |
| Default (US) | `SPY` |

This mapping lives in a small helper function inside `regime_detector.py`, not in a config file — it is too small to warrant config overhead.

#### Fallback: rolling return

If SMA cannot be computed (insufficient history), fall back to:

- Compute 63-day rolling return of benchmark over the test window.
- If median rolling return > 0: `"bull"`.
- If median rolling return <= 0: `"bear"`.

This ensures even short data histories get a label.

#### Per-regime breakdown

After all walk-forward splits complete, aggregate per-regime Sharpe:

```python
def compute_regime_breakdown(
    splits: List[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
```

Returns:

```python
{
    "bull": {"sharpe": 1.2, "n_splits": 3},
    "bear": {"sharpe": -0.3, "n_splits": 2},
}
```

If bear Sharpe < -0.5, set `bull_only_warning = True`.

### 2. Data Model Changes

**File**: `quant/features/research/models.py`

Add two fields to `PurgedWalkForwardResult`:

```python
@dataclass(frozen=True)
class PurgedWalkForwardResult:
    splits: List[Dict[str, Any]]
    aggregate_oos_sharpe: float
    worst_oos_sharpe: float
    deflated_sharpe_ratio: Optional[float]
    sharpe_degradation: float
    pct_profitable_splits: float
    is_viable: bool
    regime_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)
    bull_only_warning: bool = False
```

These are additive fields with defaults — no breakage to existing consumers.

### 3. Capacity Gate Integration

**File**: `quant/features/research/rigor/backtest_hub.py`

Current `is_viable` decision (lines 88-91):

```python
is_viable = (
    worst_oos >= self._min_worst_oos_sharpe
    and profitable >= self._min_profitable_pct
)
```

Change to:

```python
is_viable = (
    worst_oos >= self._min_worst_oos_sharpe
    and profitable >= self._min_profitable_pct
    and self._check_capacity(split_results)
)
```

New private method:

```python
def _check_capacity(self, split_results: List[Dict[str, Any]]) -> bool:
```

Logic:

- Iterate split results. Each result has a backtest response dict from `self._runner`.
- If a response contains `trades` data (non-empty list), estimate costs via `estimate_costs()` and check `capacity_ok`.
- If ANY split has `capacity_ok = False`, return `False`.
- If NO split has trade data (MVP case), return `True` (skip capacity check, rely on Sharpe only).
- Log the capacity gate result.

This is a conservative approach: capacity gate only activates when trade data is present. In the MVP, most backtest runners may not return trade-level data, so the gate silently passes.

### 4. Regime Labels in Walk-Forward

**File**: `quant/features/research/rigor/backtest_hub.py`

Modify `run_walkforward`:

1. Accept an optional `benchmark_data: Any = None` parameter.
2. After computing all split results, call `label_split_regime()` for each split.
3. Store the regime label in each split dict: `split["regime"] = regime_label`.
4. Call `compute_regime_breakdown(split_results)` to get per-regime Sharpe aggregates.
5. Pass `regime_breakdown` and `bull_only_warning` into `PurgedWalkForwardResult`.

If `benchmark_data` is `None`, regime fields default to empty dict / `False`. This preserves backward compatibility.

### 5. SSRN Source Adapter

**File**: `quant/infrastructure/research/sources/ssrn_source.py`

Implement real SSRN search using the public-facing API endpoints.

#### API approach

SSRN does not have a fully documented public search API. Two viable approaches, tried in order:

1. **SSRN search page JSON endpoint.** SSRN's web search at `https://papers.ssrn.com/sol3/results.cfm` returns HTML. Parse it with `BeautifulSoup` or similar if available. If not available, skip to approach 2.

2. **SSRN TopPapers / NewPapers RSS-like feed.** `https://papers.ssrn.com/sol3/TopPapers.cfm` provides HTML with structured paper listings. Parse titles, authors, abstracts, and URLs.

3. **Fallback.** If both approaches fail (blocked, parse error, timeout), return empty list and log a warning.

#### Implementation contract

```python
class SSRNSource(ResearchSource):
    def __init__(self, rate_limiter: Optional[Any] = None):
        self._rate_limiter = rate_limiter
        self._base_url = "https://papers.ssrn.com"
        self._timeout = 30
        self._max_retries = 2
        self._retry_backoff = 5

    @property
    def source_name(self) -> str:
        return "ssrn"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
```

#### Requirements

- **Polite crawling**: Use the injected `rate_limiter` if available, else sleep 3-5s between requests.
- **Proper User-Agent**: Set `User-Agent: QuantResearchBot/1.0 (academic use)` header.
- **Retry with backoff**: On connection error or HTTP 429/5xx, retry up to `_max_retries` times with `_retry_backoff` second delay, doubled each retry.
- **Graceful failure**: On any exception (blocked, timeout, parse error), return `[]` and log a `logger.warning()`. Never raise.
- **Return format** matches `ResearchSource` port:
  ```python
  {
      "title": str,
      "description": str,       # abstract, truncated to 500 chars
      "source": "ssrn",
      "source_url": str,        # direct URL to paper
      "authors": str,           # comma-separated
      "published_date": str,    # ISO date or empty string
  }
  ```

#### Dependencies

- `requests` (already used by `arxiv_source.py`)
- `html.parser` from stdlib for basic HTML parsing (avoid `beautifulsoup4` dependency; use regex or stdlib `html.parser` if needed)

#### Rate limiter

Use the existing `rate_limiter.py` in the same directory. If it provides a `RateLimiter.wait()` or similar interface, use it. Otherwise, implement a simple `time.sleep()` with jitter.

### 6. IC Decay Pipeline Gate

**File**: `quant/features/research/research_engine.py`

After Module A computes `ValidationReport.ic_decay` with actual values (list of IC at horizons [1, 5, 10, 21]), this module adds a warning gate in the validation phase.

In `_execute_pipeline`, after the validation pass/fail check (around line 152), add:

```python
if vreport.ic_decay:
    ic_1d = vreport.ic_decay[0] if len(vreport.ic_decay) > 0 else None
    ic_21d = vreport.ic_decay[3] if len(vreport.ic_decay) > 3 else None
    if ic_1d is not None and ic_21d is not None and ic_1d != 0:
        decay_pct = abs(ic_21d / ic_1d)
        if decay_pct < 0.5:
            vreport_errors = list(vreport.errors) + ["high_ic_decay"]
            vreport = dataclasses.replace(vreport, errors=vreport_errors)
            result.log.append(ResearchLogEntry(
                phase="validation", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="warn",
                reason=f"IC decay: {ic_1d:.4f} -> {ic_21d:.4f} ({decay_pct:.0%} remaining)",
                scores={"ic_1d": ic_1d, "ic_21d": ic_21d},
            ))
```

This is a **warning**, not a hard reject. The strategy still passes validation but is flagged in `ValidationReport.errors` and logged. Downstream consumers (researcher review, dashboard) can surface the warning.

#### Why not a hard gate?

IC decay is context-dependent. A momentum strategy may legitimately have decaying IC at longer horizons — that is the expected behavior. A mean-reversion strategy with rapidly decaying IC is more concerning. The warning allows human review rather than blanket rejection.

### 7. Benchmark Data Flow

**File**: `quant/features/research/research_engine.py`

The `RigorHub` needs benchmark data to label regimes. Two options:

1. **Inject a `ResearchMarketData` port.** The engine receives it and passes benchmark data to `run_walkforward`.
2. **Pass benchmark data from the composition root.** The API layer loads benchmark data and passes it through.

Option 1 is architecturally cleaner but requires the `ResearchMarketData` port to be implemented (Module B territory). Option 2 is simpler for now.

**Chosen approach**: Add an optional `benchmark_data: Any = None` parameter to the `_run_backtests` method. The composition root (`api/research_bp.py`) can inject it when available. If `None`, regime detection is skipped (backward compatible).

In `research_engine.py._run_backtests`:

```python
def _run_backtests(self, strategy_ids, result, benchmark_data=None):
    ...
    wf_result = self._rigor_hub.run_walkforward(
        strategy_id=sid,
        symbols=self.config.default_symbols,
        start=self.config.default_backtest_start,
        end=self.config.default_backtest_end,
        benchmark_data=benchmark_data,
    )
```

The composition root can load SPY daily bars from DuckDB and pass them as benchmark data.

## File Change Summary

| File | Change type | Description |
|------|-------------|-------------|
| `features/research/rigor/regime_detector.py` | Rewrite | Add `label_split_regime()`, `compute_regime_breakdown()`, benchmark mapping helper |
| `features/research/rigor/backtest_hub.py` | Modify | Accept `benchmark_data`, label splits, compute regime breakdown, add `_check_capacity()` gate |
| `features/research/models.py` | Modify | Add `regime_breakdown` and `bull_only_warning` fields to `PurgedWalkForwardResult` |
| `features/research/research_engine.py` | Modify | Pass `benchmark_data` through, add IC decay warning logic |
| `infrastructure/research/sources/ssrn_source.py` | Rewrite | Implement real SSRN search with polite crawling |
| `api/research_bp.py` | Modify | Load benchmark data, pass to engine |

## Architecture Compliance

| Rule | Compliance |
|------|------------|
| `domain/` zero external deps | No changes to domain. Port signatures unchanged. |
| `features/` depends on domain only | `regime_detector.py` uses only stdlib + pandas (already used). `backtest_hub.py` imports from local models. |
| `infrastructure/` does not import features | `ssrn_source.py` imports only `domain.ports.research_source`. |
| Feature-to-feature no direct imports | No new cross-feature imports. |
| Domain ports return `Any` | `ResearchSource.search` already returns `List[Dict[str, Any]]`. |

## Acceptance Criteria

### Regime detection

- [ ] `label_split_regime()` returns `"bull"`, `"bear"`, or `"unknown"` for any walk-forward split
- [ ] Benchmark mapping selects `SPY` for US symbols, `000300` for CN symbols, `HSI` for HK symbols
- [ ] Fallback to rolling 63-day return when SMA history is insufficient
- [ ] Each walk-forward split dict contains a `"regime"` key after `run_walkforward` completes

### Regime breakdown

- [ ] `compute_regime_breakdown()` returns per-regime Sharpe and split count
- [ ] `PurgedWalkForwardResult.regime_breakdown` populated when benchmark data is provided
- [ ] `PurgedWalkForwardResult.bull_only_warning = True` when bear Sharpe < -0.5
- [ ] Both fields default to empty/`False` when no benchmark data (backward compatible)

### Capacity gate

- [ ] `is_viable = False` when trade data is available and `capacity_ok = False` for any split
- [ ] `is_viable` ignores capacity when no trade data is present (MVP backward compatibility)
- [ ] Capacity gate result is logged at `INFO` level

### SSRN source

- [ ] `SSRNSource.search()` returns list of dicts with required keys (`title`, `description`, `source`, `source_url`)
- [ ] Rate limiting applied (3-5s between requests)
- [ ] Retry with exponential backoff on failure
- [ ] Returns `[]` on any error (never raises)
- [ ] Proper User-Agent header set
- [ ] No new dependencies beyond `requests` (already in use)

### IC decay gate

- [ ] When `ic_decay` has >= 4 entries and IC drops > 50% from index 0 to index 3, `"high_ic_decay"` is appended to `ValidationReport.errors`
- [ ] The strategy still passes validation (warning only)
- [ ] Warning is logged with IC values and decay percentage

### General

- [ ] All existing tests pass (`python -m pytest quant/tests/ -q`)
- [ ] No imports from `quant.infrastructure` in `quant/features/research/`
- [ ] No imports from `quant.features` in `quant/infrastructure/research/`

## Dependencies on Other Modules

| Module | Dependency | Status |
|--------|------------|--------|
| Module A (IC decay computation) | `ValidationReport.ic_decay` must be populated with actual values | Must ship first or simultaneously |
| Module B (ResearchMarketData port) | Optional — enables benchmark data loading via port | Not required; fallback is composition root injection |
| Module D | No dependency | Independent |

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| SSRN blocks scraping or changes page structure | Return `[]` on any failure; log warning; SSRN can be disabled in config (`ssrn.enabled: false`) |
| Benchmark data not available in DuckDB | Regime detection is optional — defaults to empty breakdown, `bull_only_warning = False` |
| `PurgedWalkForwardResult` frozen dataclass requires `dataclasses.replace()` | Already frozen per parent spec; use `field(default_factory=dict)` and `field(default=False)` for new fields |
| IC decay list may have fewer than 4 entries | Guard with `len(vreport.ic_decay) > 3` check |
| Trade data not yet returned by backtest runner | Capacity gate skips gracefully when no trades present |

## Out of Scope

- True Deflated Sharpe Ratio implementation (parent spec defers this)
- Advanced regime detection (HMM, clustering, multi-factor regimes)
- SSRN full-text access or PDF parsing
- Frontend display of regime breakdown (API-only in this module)
- Portfolio-level regime-aware allocation
