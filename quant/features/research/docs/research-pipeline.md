# Research Pipeline

This document is the canonical short description of the strategy research pipeline. When research pipeline behavior changes, update this file and `.agents/skills/quant-research-fks/SKILL.md` in the same change.

## Scope

The current research scope is A-share daily strategies. Discovery, admission, StrategySpec generation, validation, reports, generated strategy defaults, and CLI/API defaults should use A-share symbols such as `000300`, `000905`, `600519`, `000001`, and `510300`.

The pipeline is a gated funnel:

`public/local idea sources -> idea_bank -> daily A-share admission -> StrategySpec -> HFQ signal validation -> candidate integration -> strict Backtester -> walk-forward audit -> HTML reports and status transition`

## Entrypoints

- Scout only: `python quant/scripts/run_low_frequency_idea_scout.py --source config --max 5`
- Research runner: `python quant/scripts/run_research.py --mode discover|scout_formal|formal|fast|strict|walkforward|full`
- API runner: `POST /api/research/run`, defaulting to `mode=full`

`quant/domain/models/research_source_catalog.py` owns default discovery source names, query plans, feed URLs/source filters, and source-quality scores. `config/research.yaml` owns validation gate thresholds, discovery filters, backtest period, initial cash, production gate, and walk-forward worker defaults.

## Modes

| Mode | What It Runs |
|------|--------------|
| `discover` | Source search, quality scoring, deduplication, and idea-bank persistence only. |
| `scout_formal` | Runs scout, selects top local ideas, then runs formal research on those ideas. |
| `formal` | Loads ideas from the local idea bank, then runs fast research, integration, strict backtest, and walk-forward when gates pass. |
| `fast` | Single-stage fast research from the idea bank; disables strict backtest and walk-forward. |
| `strict` | Standalone strict Backtester stage for selected existing strategy candidates. |
| `walkforward` | Standalone walk-forward strict audit for selected existing strategy candidates. |
| `full` | End-to-end run: discovery plus formal research, strict backtest, walk-forward, and reports. |

Default new-strategy research uses `full` or `formal`. Single-stage `fast`, `strict`, and `walkforward` are explicit rerun/debug modes.

## Stages

### 1. Discovery

`StrategyScout` coordinates direct adapters or `SourceHub`, deduplicates raw ideas, attaches `discovery_quality`, ranks results, and applies configured quality filters. API and CLI composition roots build discovery adapters through `quant.infrastructure.research.sources.build_research_sources()`, which reads the central source catalog. The current default catalog enables arXiv, BigQuant, JointQuant, and Quantocracy; the catalog also defines SSRN, NBER, generic blogs, A-share public forum seeds, Hudson & Thames, Portfolio Optimizer, Alpha Architect, Quantpedia, and local A-share structural ideas for explicit runs.

Discovery writes stable assets under `quant/infrastructure/var/research/idea_bank/` and upserts ideas into the research store with status such as `discovered`.

### 2. Admission And StrategySpec

Formal research loads ideas from the local idea bank. The evaluator is heuristic by default in CLI runs; LLM evaluation is enabled only when explicitly requested. Stage 1 gates on daily A-share fit, not on admission score alone. Admission score, suitability score, and research confidence are diagnostics and ranking hints.

When an idea passes admission, `StrategySpecBuilder` attempts to create a ready `StrategySpec`. If the formula cannot be faithfully represented with current registered daily signal kernels and available DB fields, the idea is marked `needs_manual_spec` instead of being forced into a proxy formula.

### 3. Fast Research Gate

Fast research writes a screening strategy source under `quant/features/strategies/reject/<strategy_id>/` so validation and later report code use the same implementation source. This staging location does not mean the idea is finally rejected.

`FactorValidator` runs HFQ real-data signal validation and Top 20 after-cost portfolio diagnostics. The current pre-full gate requires:

- Current DB field compatibility.
- Calculable Rank IC.
- `Rank IC > 0.02`.
- `Top 20 after-cost Sharpe >= 0.5`.
- `Top 20 after-cost CAGR > 5%`.
- `Top 20 after-cost MaxDD < 50%`.

Ideas that fail this gate remain in the idea/hypothesis ledger and stage reports. They must not be integrated as candidates, strict-backtested, archived as final rejected strategies, or rendered as `full_research_report.html`.

### 4. Candidate Integration

Only ideas that pass the fast research gate are integrated by `StrategyIntegrator`. Integrated candidates are written as executable strategy directories with `strategy.py`, `config.yaml`, and `README.md`, then registered with status `candidate`.

Generated candidates are not activated automatically. A ready `StrategySpec.strategy_id` is the canonical ID for candidate directories, registry names, metadata, reports, and downstream backtests.

### 5. Strict Backtest

Strict backtests run through the injected project Backtester. For A-share formal research, the default period is `2016-01-01` through `2026-05-31`, and the default initial cash is `10000`.

Dedicated public-source replication runners may explicitly override initial cash when the cited source specifies a capital base or when CN lot-size plus basket-width constraints would otherwise create a mechanically invalid zero-trade run. Such overrides must be documented in the runner, strategy README, reports, and tests; they do not change `config/research.yaml` or the default formal-research cash.

Strict Backtester evidence must include realistic execution semantics and constraints: T+1 execution, costs, liquidity impact, commissions/taxes, cash, lot size, suspensions, limit up/down behavior, ADV participation, benchmark coverage, turnover, exposure, and data-quality diagnostics. CN strategies must attach an enabled execution cost model.

The current production Go / No-Go gate is evaluated from strict backtest evidence:

- `max_adv_participation <= 5% ADV`.
- `total_trades > 50`.
- CAGR/MaxDD tier gate: `5%-10% CAGR => MaxDD <= 15%`, `10%-15% => <= 25%`, `15%-20% => <= 30%`, `20%+ => <= 50%`.

### 6. Walk-Forward Audit

Walk-forward strict audit replays strict Backtester over rolling OOS splits through `RigorHub`. It records aggregate OOS Sharpe, worst OOS Sharpe, profitable split ratio, DSR, regime breakdown, capacity evidence, and no-trade split counts.

Walk-forward evidence is retained in the independent walk-forward report and the full-report Walk-forward card. Under the current standard, walk-forward findings are audit evidence and warnings, not part of the active Go / No-Go checklist.

### 7. Reports And Status Transition

Every end-to-end research run writes:

- `full_research_report.html`
- `fast_research_report.html`
- `strict_backtest_report.html`
- `walkforward_audit_report.html`
- timestamped run copies
- `reports/latest/metadata.json`
- `reports/latest/initial_screening_table.md`

Stable report paths live under `quant/infrastructure/var/research/reports/<strategy_or_idea_id>/`. `quant/infrastructure/var/research/reports/latest/` is the API/frontend pointer to the latest full report and stage reports.

The full report must use exactly these top-level expandable cards:

1. `Final Decision`
2. `策略逻辑`
3. `策略表现`
4. `重要 Metric`
5. `Walk-forward`
6. `Stability`
7. `Risk`

If the active checklist passes, the strategy belongs under `quant/features/strategies/<strategy_id>/` and the same `full_research_report.html` is copied into that strategy directory. If evidence is incomplete or the final decision has not reached Go, keep it under `quant/features/strategies/reject/<strategy_id>/`. Once a generated strategy is finally rejected, archive it under `quant/features/rejected_strategy/<strategy_id>/`.

## Persistence

The file-backed default research store writes `quant/infrastructure/var/research/research_state.json`, which holds ideas, hypotheses, candidates, and seen hashes. DuckDB-backed tracking can be enabled through `tracking_db_path`.

Important statuses include `discovered`, `stage1_rejected`, `needs_manual_spec`, `research_queue`, `validation_failed`, `validated`, `candidate`, `needs_more_validation`, `rejected`, and `error`.

## Maintenance Checklist

Any change to the following is a research pipeline contract change:

- Stage order, mode behavior, or stage independence.
- Source catalog, query plan, discovery quality filters, or A-share scope.
- Admission, StrategySpec, validation, pre-full gate, or production gate thresholds.
- Generated strategy placement, status transition, rejected-strategy archive behavior, or candidate activation semantics.
- Strict Backtester wiring, execution-cost model selection, default period, default capital, or walk-forward configuration.
- Report names, report paths, full-report cards, stage-report contract, latest pointer behavior, or persisted evidence shape.

When changing any of those, update this file, `.agents/skills/quant-research-fks/SKILL.md`, and the relevant module `AGENTS.md` entry in the same change. Report format changes must also update `quant/tests/test_research_report_contract.py`.
