# Research Agent — Multi-Agent Quantitative Strategy Research System

**Date:** 2026-04-19
**Status:** Approved

## Overview

Build an autonomous multi-agent system (`quant/research/`) that acts as a quantitative researcher: searches cutting-edge strategy ideas from papers, forums, and blogs; evaluates feasibility; generates strategy code; backtests and validates; produces conclusion reports. Runs on a scheduled basis (default weekly).

## Architecture Decision

**Chosen: Multi-Agent Collaboration (Approach B)**

Four specialized agents coordinated by a ResearchCoordinator via EventBus:

| Agent | Input | Output | Core Capability |
|-------|-------|--------|-----------------|
| SearcherAgent | Keywords / topic | `RawIdea` list | Parallel multi-source crawling |
| AnalystAgent | `RawIdea` list | `ScoredIdea` (scored + plan) | LLM feasibility assessment |
| CoderAgent | `ScoredIdea` | Strategy code + registration | LLM code generation with validation loop |
| EvaluatorAgent | Strategy name + params | `ResearchReport` | Backtest + WalkForward + LLM review |

ResearchCoordinator manages the state machine: `CREATED → SEARCHING → ANALYZING → CODING → EVALUATING → REPORTING → COMPLETED`

## Directory Structure

```
quant/research/
├── __init__.py
├── coordinator.py           # ResearchCoordinator — orchestrates agents
├── agents/
│   ├── __init__.py
│   ├── searcher.py           # SearcherAgent — multi-source search
│   ├── analyst.py            # AnalystAgent — feasibility scoring
│   ├── coder.py              # CoderAgent — code generation + validation
│   └── evaluator.py          # EvaluatorAgent — backtest + review
├── models.py                 # ResearchTask, RawIdea, ScoredIdea, StrategyCandidate, ResearchReport
├── sources/
│   ├── __init__.py
│   ├── base.py               # SourceAdapter ABC
│   ├── arxiv.py              # arXiv q-fin papers
│   ├── ssrn.py               # SSRN working papers
│   ├── quantconnect.py       # QuantConnect community
│   ├── twitter.py            # Twitter/X quant KOLs
│   └── cn_forums.py          # JoinQuant / RiceQuant Chinese forums
├── templates/
│   ├── strategy_template.py  # Strategy code skeleton
│   └── prompts/              # LLM prompt templates per agent
├── config/
│   └── research_config.yaml  # All research settings
├── reports/                  # Generated reports output
└── db/                       # DuckDB integration (reuses existing)
```

## Data Models

### RawIdea
- `id`: UUID
- `source`: "arxiv" | "ssrn" | "quantconnect" | "twitter" | "cn_forums"
- `source_url`: original URL
- `title`: strategy title
- `description`: core idea summary (LLM extracted)
- `published_date`: optional
- `metadata`: dict (authors, keywords, citations)
- `discovered_at`: datetime

### ScoredIdea
- `id`: UUID
- `raw_idea`: RawIdea
- `feasibility_score`: 0-100 composite
- `academic_rigor`: 0-100
- `backtestability`: 0-100
- `compatibility`: 0-100 (with existing system)
- `novelty`: 0-100 (vs registered strategies)
- `implementation_plan`: LLM-generated implementation description
- `suggested_factors`: list of existing factor names to reuse
- `suggested_params`: dict of parameter ranges for WalkForward
- `risk_assessment`: potential pitfalls
- `scored_at`: datetime

### StrategyCandidate
- `id`: UUID
- `scored_idea`: ScoredIdea
- `strategy_name`: registration name
- `code_path`: quant/strategies/<name>/strategy.py
- `config_path`: quant/strategies/<name>/config.yaml
- `registered`: bool (successfully registered)
- `created_at`: datetime

### ResearchReport
- `id`: UUID
- `candidate`: StrategyCandidate
- `backtest_metrics`: PerformanceMetrics (existing)
- `walkforward_result`: optional WalkForwardResult (existing)
- `llm_analysis`: LLM interpretation of metrics
- `recommendation`: "adopt" | "watchlist" | "reject"
- `recommendation_reasoning`: string
- `comparison`: dict vs existing strategies
- `generated_at`: datetime

### ResearchTask
- `id`: UUID
- `status`: state machine current state
- `topic`: optional search topic (null = broad scan)
- `max_ideas`: int, max ideas to explore per run
- `ideas`: list[RawIdea]
- `scored_ideas`: list[ScoredIdea]
- `candidates`: list[StrategyCandidate]
- `reports`: list[ResearchReport]
- `created_at`: datetime
- `completed_at`: optional datetime
- `error_log`: list[str]

## Agent Specifications

### SearcherAgent
- Accepts configured `SourceAdapter` list
- Generates search keywords via LLM based on task topic
- Parallel source search (max 3 concurrent)
- LLM extracts strategy core idea from raw content
- Semantic deduplication (similarity > 0.85 → keep more authoritative source)
- Rate limit: ≥ 3 seconds between requests per source

### AnalystAgent
- Receives `RawIdea` list
- LLM prompt includes: strategy description, registered strategies list, available factors, available data (US/HK daily/minute), system constraints (T+1, lot size, 5% volume limit)
- Returns structured JSON with 5 dimension scores + implementation plan
- Filters out ideas with `feasibility_score < 60`

### CoderAgent
- Loads strategy template from `templates/strategy_template.py`
- LLM prompt includes: ScoredIdea implementation plan, Strategy ABC interface, existing strategy as few-shot (SimpleMomentum), factor library API
- Constraints enforced in prompt: must use `@strategy()` decorator, must inherit Strategy, must implement at least one `on_*` hook, use `self.buy()`/`self.sell()`, no comments
- Validation loop: `compile()` → `import` → `StrategyRegistry.get()` → success; on failure, feed error back to LLM, max 2 retries
- Writes to `quant/strategies/<name>/strategy.py` + `config.yaml`
- Dynamic import triggers auto-discovery registration

### EvaluatorAgent
- Runs Backtester with default 24-month period
- If profitable, runs WalkForward validation
- LLM analyzes: Sharpe vs existing strategies, overfit risk, parameter stability (degradation), portfolio complementarity
- Recommendation logic:
  - Sharpe > 1.0 & WalkForward viable & correlation < 0.7 → `adopt`
  - Sharpe > 0.5 or WalkForward marginal → `watchlist`
  - Sharpe < 0 or WalkForward not viable → `reject`

### ResearchCoordinator
- Manages ResearchTask lifecycle via state machine
- Subscribes to 5 new EventBus event types:
  - `RESEARCH_SEARCH_DONE`
  - `RESEARCH_IDEA_SCORED`
  - `RESEARCH_CODE_READY`
  - `RESEARCH_REPORT_DONE`
  - `RESEARCH_ERROR`
- Parallelism: multiple Coders and Evaluators can run concurrently
- Scheduling via APScheduler (cron configurable, default Friday 20:00)
- Manual trigger: `coordinator.start_research(topic="mean reversion")`

## Information Source Adapters

### SourceAdapter ABC
- `search(keywords, max_results) → list[dict]`: raw search results
- `fetch_full_content(url) → str`: full text extraction
- `name → str`: source identifier

### arxiv.py
- arXiv API: `http://export.arxiv.org/api/query?search_query=cat:q-fin.*`
- Parse Atom XML → title, summary, authors, links
- LLM secondary filter: is it a quantifiable trading strategy (not pure theory/macro)
- Scans last 7 days of new papers

### ssrn.py
- SSRN search API or web scrape
- Keywords: "quantitative trading strategy", "alpha factor", "statistical arbitrage"

### quantconnect.py
- Community Forum algorithm sharing board
- Extract algorithm descriptions + code snippets
- Practical but lower academic rigor → Analyst auto-adjusts

### twitter.py
- Track configured KOL list + hashtag search
- Fragment content → LLM reconstructs full strategy idea

### cn_forums.py
- JoinQuant / RiceQuant APIs
- Chinese content → LLM translates to English for storage

## Event System Integration

**Modified file:** `quant/core/events.py` — add 5 new event types

No other existing files are modified. The `quant/research/` module is purely additive.

## Configuration (`research_config.yaml`)

```yaml
research:
  enabled: true
  schedule:
    cron: "0 20 * * 5"
    timezone: "Asia/Shanghai"
  search:
    sources:
      - name: arxiv
        enabled: true
        max_results: 15
        categories: ["q-fin.TR", "q-fin.PM", "q-fin.CP"]
        lookback_days: 7
      - name: ssrn
        enabled: true
        max_results: 10
      - name: quantconnect
        enabled: true
        max_results: 10
      - name: twitter
        enabled: false
      - name: cn_forums
        enabled: false
    max_concurrent_sources: 3
    rate_limit_seconds: 3
  analysis:
    min_feasibility_score: 60
    max_ideas_per_run: 5
  coding:
    max_retries: 2
    timeout_seconds: 120
    validate_import: true
  evaluation:
    default_backtest_period_months: 24
    run_walkforward: true
    adopt_threshold:
      min_sharpe: 1.0
      max_correlation_with_existing: 0.7
    watchlist_threshold:
      min_sharpe: 0.5
  llm:
    provider: "openai"
    model: "gpt-4o"
    temperature: 0.3
    max_tokens: 4096
  storage:
    reports_dir: "quant/research/reports"
    save_intermediate: true
```

## Report Output

Per research task, generates under `quant/research/reports/`:

```
reports/
└── 2026-04-19_task_<id>/
    ├── summary.md                # Human-readable weekly research summary
    ├── idea_scores.json          # All idea scoring details
    ├── strategy_<name>/
    │   ├── strategy.py           # Generated strategy code
    │   ├── config.yaml           # Strategy config
    │   ├── backtest_metrics.json
    │   ├── equity_curve.csv
    │   ├── walkforward.csv
    │   └── analysis.md           # LLM analysis report
    └── comparison.md             # vs existing strategy comparison matrix
```

## Error Handling & Recovery

| Scenario | Handling |
|----------|----------|
| Source unreachable | Skip source, log, continue others |
| LLM call failure | Graceful degradation (reuse CIO pattern), mark idea as skipped |
| Code gen syntax error | Retry up to 2x with error feedback to LLM |
| Code gen logic error (backtest crash) | Evaluator catches exception, mark reject + error reason |
| WalkForward insufficient data | Skip WF, use simple backtest only, note in report |
| Task interrupted | ResearchTask state persisted to DuckDB, on restart detect IN_PROGRESS tasks and resume from checkpoint |
| DuckDB write failure | Log alert, degrade to file-only output |

**Checkpoint recovery:** On startup, scan DuckDB for `status != 'COMPLETED'` tasks. Determine checkpoint by counting existing ideas/scored_ideas/candidates and resume from the appropriate state.

## Existing System Integration

All dependencies are injected, no global state introduced:

- **LLM:** Reuses CIO's `LLMAdapter` (OpenAI/Claude/Ollama) — constructor injection
- **Backtester:** Reuses `quant/core/backtester.py` — direct import
- **WalkForward:** Reuses `quant/core/walkforward.py` — direct import
- **Strategy Registry:** Reuses `StrategyRegistry` — CoderAgent writes to standard dir, auto-discovery handles registration
- **DuckDB:** Reuses `DuckDBStorage` — new tables added for research data
- **EventBus:** Reuses `quant/core/events.py` — 5 new event types
- **Config:** Reuses `ConfigLoader` pattern — new `research_config.yaml`

**Only existing file modified:** `quant/core/events.py` (add event types)

## New Dependencies

```
aiohttp       # Async HTTP for source crawling
apscheduler   # Cron scheduling
lxml          # arXiv XML parsing (optional, can use stdlib xml.etree)
```

## Delivery Phases

| Phase | Scope | Goal |
|-------|-------|------|
| **P0** | models + Coordinator + CoderAgent + EvaluatorAgent + templates | Core pipeline working (inject RawIdea manually) |
| **P1** | AnalystAgent + arXiv adapter + DuckDB persistence | Full closed-loop automation |
| **P2** | SSRN + QuantConnect + Twitter + cn_forums adapters | Source coverage expansion |
| **P3** | Flask API routes + checkpoint recovery + comparison matrix | Production hardening |
