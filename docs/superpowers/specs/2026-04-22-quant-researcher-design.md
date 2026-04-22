# Quant Researcher Feature Design

**Date:** 2026-04-22
**Status:** Approved

---

## 1. Overview

Add a `features/research/` feature that actively searches for advanced quant strategies from curated financial sources (arXiv q-fin, SSRN, Quantpedia), evaluates them for daily-bar suitability, auto-generates strategy implementations, runs backtests, and manages a candidate pool within the existing strategy runtime state.

Supports both **on-demand** (frontend button / API call) and **scheduled** (routine background execution) modes.

---

## 2. Architecture

```
quant/features/research/
├── __init__.py              # Re-exports: ResearchEngine, CandidatePool, ResearchScheduler
├── research_engine.py       # Orchestrator: search -> evaluate -> integrate -> backtest -> pool
├── scout.py                 # StrategyScout + SourceAdapter interface + arXiv/SSRN/Quantpedia adapters
├── evaluator.py             # StrategyEvaluator using LLM adapters
├── integrator.py            # StrategyIntegrator: codegen + registration + runtime state injection
├── pool.py                  # CandidatePool: candidate lifecycle management
├── scheduler.py             # ResearchScheduler: background thread, config-driven intervals
├── models.py                # EvaluationReport, RawStrategy, ResearchConfig dataclasses
└── config/
    └── research.yaml        # Default thresholds, intervals, API endpoints
```

**Dependencies (inbound only):**
- `features/research/` depends on:
  - `features/cio/llm_adapters/` (existing LLM adapters)
  - `features/backtest/` (Backtester, BacktestResult)
  - `features/strategies/` (Strategy base class, registry, @strategy decorator)
  - `shared/utils/` (logger, config_loader)
  - `api/state/runtime.py` (AVAILABLE_STRATEGIES, STRATEGY_PARAMETERS, _save_strategy_state)

No other feature depends on `features/research/`. Hexagonal dependency rules are preserved.

---

## 3. Components

### 3.1 StrategyScout (`scout.py`)

Abstract `SourceAdapter` interface:
```python
class SourceAdapter(ABC):
    @abstractmethod
    def search(self, max_results: int = 10) -> List[RawStrategy]: ...
```

Implementations:
- `ArxivAdapter` — queries arXiv API (`http://export.arxiv.org/api/query?search_query=cat:q-fin.TR`)
- `SSRNAdapter` — scrapes SSRN search pages with rate limiting
- `QuantpediaAdapter` — uses Quantpedia API (or scraping fallback)

`RawStrategy` (dataclass):
- `title: str`
- `description: str`
- `source: str`
- `source_url: str`
- `authors: Optional[str]`
- `published_date: Optional[str]`

### 3.2 StrategyEvaluator (`evaluator.py`)

Uses existing `LLMAdapter` from `features/cio/llm_adapters/`.

`EvaluationReport` (dataclass):
- `suitability_score: float`  # 0-10, daily-bar suitability
- `complexity_score: float`   # 0-10, implementation complexity
- `data_requirement: str`     # "low", "medium", "high-frequency"
- `daily_adaptable: bool`     # whether HF strategy can be adapted to daily
- `estimated_edge: float`     # estimated annual return (decimal, e.g. 0.12)
- `recommended_symbols: List[str]`
- `strategy_type: str`        # e.g. "momentum", "mean_reversion", "stat_arb"
- `summary: str`              # LLM-generated summary

Prompt template sent to LLM:
> "Evaluate this quant strategy for daily-bar (EOD) trading. Score suitability [0-10], complexity [0-10], data requirement [low/medium/high-frequency], whether it can be adapted to daily bars [yes/no], estimated annual edge [%]. Also recommend asset class and symbols."

### 3.3 StrategyIntegrator (`integrator.py`)

Generates strategy boilerplate from `EvaluationReport`.

Steps:
1. Normalize name: e.g. "Cross-Sectional Momentum via Machine Learning" → `cross_sectional_momentum_ml`
2. Create directory: `features/strategies/<normalized_name>/`
3. Write `strategy.py` inheriting from `Strategy` base, with `@strategy(name)` decorator
4. Generate placeholder `on_data()` logic based on `strategy_type`
5. Generate `README.md` with source attribution
6. Add entry to `AVAILABLE_STRATEGIES` with `status: "candidate"`
7. Add default parameters to `STRATEGY_PARAMETERS`
8. Call `_save_strategy_state()`

### 3.4 CandidatePool (`pool.py`)

Wraps `AVAILABLE_STRATEGIES` to filter and manage candidate lifecycle.

New statuses introduced:
- `candidate` — discovered, evaluated, auto-backtested, awaiting human review
- `rejected` — failed evaluation threshold or backtest, hidden from UI

Methods:
- `list_candidates() -> List[Dict]` — filters by status == "candidate"
- `list_rejected() -> List[Dict]` — filters by status == "rejected"
- `promote(strategy_id) -> None` — candidate → paused
- `reject(strategy_id, reason: str) -> None` — candidate → rejected
- `get_research_meta(strategy_id) -> Dict` — returns `research_meta` dict

### 3.5 ResearchScheduler (`scheduler.py`)

Background daemon thread (similar to `simulation_loop` in `runtime.py`).

Config (from `research.yaml`):
```yaml
research:
  auto_run: false
  interval_days: 7
  sources: [arxiv, ssrn]
  max_results_per_source: 10
  evaluation_threshold: 6.0
  backtest_sharpe_threshold: 0.5
  auto_backtest: true
  default_backtest_start: "2020-01-01"
  default_backtest_end: "2024-12-31"
  default_symbols: ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"]
```

Scheduler loop:
- Sleep for `interval_days`
- Trigger `ResearchEngine.run_full_pipeline()`
- Log results

### 3.6 ResearchEngine (`research_engine.py`)

Orchestrator with a single public method:

```python
class ResearchEngine:
    def __init__(self, config: ResearchConfig, llm_adapter: LLMAdapter):
        ...

    def run_full_pipeline(self, sources: Optional[List[str]] = None) -> ResearchResult:
        """Run search -> evaluate -> integrate -> backtest -> pool."""
        ...
```

`ResearchResult`:
- `discovered: int`
- `evaluated: int`
- `integrated: int`
- `backtested: int`
- `promoted_auto: int`  # if auto-promote threshold configured
- `rejected: int`
- `errors: List[str]`

---

## 4. Data Flow

```
[User clicks "Run Research" or Scheduler wakes up]
           │
           ▼
┌─────────────────────┐
│   StrategyScout     │  Queries arXiv/SSRN/Quantpedia APIs
│   (per source)      │  Returns List[RawStrategy]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ StrategyEvaluator   │  LLM evaluates each RawStrategy
│   (parallel batch)  │  Returns List[(RawStrategy, EvaluationReport)]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Filter Gate       │  suitability >= threshold
│                     │  AND (data_req != "high-frequency" OR daily_adaptable)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ StrategyIntegrator  │  Generates code, registers strategy
│                     │  Adds to AVAILABLE_STRATEGIES as candidate
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Auto-Backtest     │  Runs Backtester with default params
│                     │  (only if auto_backtest: true)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   CandidatePool     │  If Sharpe >= threshold: keep candidate
│                     │  Else: reject
└─────────────────────┘
```

---

## 5. API Endpoints (`api/research_bp.py`)

| Endpoint | Method | Body / Params | Response |
|----------|--------|---------------|----------|
| `/api/research/run` | POST | `{"sources": ["arxiv"], "max_results": 10}` | `{"research_id": "abc123", "status": "running"}` |
| `/api/research/status/<research_id>` | GET | — | `{"status": "completed", "result": ResearchResult}` |
| `/api/research/candidates` | GET | — | `{"candidates": [...]}` |
| `/api/research/promote/<strategy_id>` | POST | — | `{"success": true, "status": "paused"}` |
| `/api/research/reject/<strategy_id>` | POST | `{"reason": "..."}` | `{"success": true, "status": "rejected"}` |
| `/api/research/schedule` | GET | — | current schedule config |
| `/api/research/schedule` | POST | `{"auto_run": true, "interval_days": 7}` | updated config |
| `/api/research/run-scheduled` | POST | — | manually trigger scheduled pipeline |

Register blueprint in `api_server.py`.

---

## 6. Frontend Integration

Add **"Research"** tab to React Dashboard:

- **Search Panel:**
  - "Run Research Now" button
  - Source checkboxes (arXiv, SSRN, Quantpedia)
  - Max results slider
- **Candidates Table:**
  - Columns: name, source, suitability, complexity, estimated edge, backtest Sharpe, discovered date
  - Actions: Promote, Reject, View Details
- **Schedule Config:**
  - Toggle auto-research
  - Interval selector (daily / weekly / monthly / custom days)

On promote: candidate moves to main Strategies page with `status: paused`.

---

## 7. Runtime State Changes

Extend `AVAILABLE_STRATEGIES` entries with `research_meta`:

```python
'CrossSectionalMomentumML': {
    'id': 'cross_sectional_momentum_ml',
    'name': 'Cross-Sectional Momentum ML',
    'description': '...',
    'status': 'candidate',
    'priority': 9,
    'doc_file': 'cross_sectional_momentum_ml.md',
    'backtest': {...},
    'research_meta': {
        'source': 'arxiv',
        'source_url': 'https://arxiv.org/abs/...',
        'suitability_score': 8.2,
        'complexity_score': 5.0,
        'data_requirement': 'medium',
        'daily_adaptable': True,
        'estimated_edge': 0.12,
        'discovered_at': '2026-04-22T10:00:00Z',
        'evaluated_at': '2026-04-22T10:05:00Z',
        'backtest_result': {...},
    }
}
```

Update `_save_strategy_state()` to persist `research_meta`.

---

## 8. Error Handling & Rate Limiting

- **API rate limits:** arXiv (3s between requests), SSRN (5s + User-Agent rotation), Quantpedia (configurable key). Use `time.sleep()` + `random.uniform()` jitter.
- **LLM failures:** catch exceptions, return neutral EvaluationReport, log warning, skip strategy.
- **Backtest failures:** wrap in try/except, log traceback, mark candidate as `rejected` with `error_reason`.
- **Duplicate detection:** MD5 hash of normalized title + description. Skip if hash exists in `AVAILABLE_STRATEGIES`.
- **Timeouts:** all HTTP requests use 30s timeout.

---

## 9. Testing Strategy

- Unit tests for each adapter (mock HTTP responses)
- Unit tests for evaluator (mock LLM adapter)
- Unit tests for integrator (mock filesystem, verify generated code is valid Python)
- Unit tests for CandidatePool (mock runtime state)
- Integration test for full pipeline with mocked external APIs

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LLM generates invalid code | Wrap backtest in try/except; reject on SyntaxError/ImportError |
| External API changes | Adapter pattern isolates changes to one file per source |
| Runtime state bloat | Rejected candidates can be hard-deleted via existing DELETE endpoint |
| Duplicate strategies | Hash-based deduplication before integration |
| Rate limiting blocks long runs | Jittered delays + per-source rate limiters |

---

## 11. Success Criteria

- [ ] Clicking "Run Research" discovers >= 1 strategy from arXiv within 60 seconds
- [ ] Evaluated strategies have `suitability_score` and `daily_adaptable` fields
- [ ] Suitable strategies appear in candidate pool with auto-backtest results
- [ ] Candidate can be promoted to `paused` and appears in main strategies list
- [ ] Scheduled research runs automatically when `auto_run: true`
- [ ] All new code follows hexagonal architecture: no cross-feature imports except through existing abstractions
