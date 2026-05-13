# Research Feature

## Responsibilities

Automatic quant strategy research. The module discovers strategy ideas from research sources, evaluates daily-bar suitability, records research evidence, creates candidate strategy scaffolds, and manages the candidate pool.

## Public Contracts

- `ResearchEngine(config, scout, evaluator, integrator, pool, backtest_fn, strategies_dir, spec_builder, validator, rigor_hub, ensemble)` - orchestrates discovery, evaluation, validation, integration, backtest, ledger, lineage manifest, promotion dossier, and scorecard writing. Backtest, validation, rigor, ensemble, store, and artifact dependencies are injected by composition roots.
- `StrategyScout` - strategy discovery coordinator. It can use direct adapters or a `SourceHub`, scores discoveries with `discovery/quality.py`, deduplicates, ranks, and optionally filters by `scout_config.min_discovery_score`.
- `discovery/source_hub.py` - fan-out source adapter hub. It supports per-source query plans and normalizes raw source dictionaries into `RawStrategy`.
- `discovery/quality.py` - deterministic idea provenance, source quality, recency, daily-data feasibility, implementation-readiness, and risk-flag scoring. Every discovered `RawStrategy` should carry `metadata.discovery_quality` before evaluation.
- `StrategyEvaluator` - LLM-backed evaluator with deterministic professional fallback. It applies `evaluation_rubric.py` to all LLM or heuristic reports.
- `evaluation_rubric.py` - admission-score rubric. It haircuts optimistic LLM output, provides no-LLM heuristic triage, and emits `required_data_fields`, `validation_tests`, `signal_quality_score`, and `research_confidence_score`.
- `StrategyIntegrator` - writes executable candidate strategy code/config/readme from a ready `StrategySpec` when available, falls back to a manual-review candidate for unsupported formulas, and registers metadata. New candidates stay `status: candidate`.
- `CandidatePool` - candidate lifecycle manager.
- `ResearchScheduler` - daemon-thread scheduler guarded by `RLock`.
- `ResearchConfig` / `ResearchResult` - config and run result dataclasses.

## Dependencies

- Depends on `domain/ports/llm` for LLM adapters and `domain/ports/research_store` for persistence.
- Uses `shared/utils` for logger/config loading.
- Backtest and strategy registry access must be injected from API/CLI composition roots through `backtest_fn`, `rigor_hub`, and `strategies_dir`.
- Research feature code must not directly import sibling features such as backtest, trading, portfolio, or infrastructure adapters except through injected ports.

## Invariants

- New strategy code is registered as `status: candidate`; it never becomes active automatically.
- A ready `StrategySpec.strategy_id` is the canonical candidate ID for generated strategy directories, registry names, metadata, dossiers, and downstream backtests.
- Candidate admission uses `admission_score` first, falling back to `suitability_score` only when no admission score exists.
- `suitability_score` alone is not enough to admit a strategy candidate.
- High-frequency ideas require `daily_adaptable: true`; otherwise they are rejected or held for manual specification.
- Discovery records must include `discovery_quality` evidence: source type, score, matched terms, risk flags, provenance completeness, and source query metadata when available.
- Evaluation reports should carry `signal_quality_score`, `research_confidence_score`, `required_data_fields`, and `validation_tests` so downstream validation can audit look-ahead, FDR, factor exposure, IC decay, costs, and capacity.
- Every evaluated hypothesis must leave a structured ledger record with source, thesis, stage, status, decision reason, metrics, and evidence.
- Each run should write a lineage manifest and candidate scorecard artifact when artifact storage is available.
- Research orchestration has two separately runnable stages. `discover` mode only scouts multiple ideas, writes `discovered_strategies.md`, and persists them into the local `idea_bank` as future candidates; it must not run admission evaluation, StrategySpec drafting, validation, integration, backtest, or full report generation. `formal` mode loads selected local `idea_bank` rows and then runs admission evaluation, StrategySpec drafting, HFQ real-data signal validation, long-only portfolio diagnostics, strict framework backtest, benchmark comparison, and Go / No-Go one idea at a time. `full` mode may still run discovery plus formal research for compatibility.
- Stable research assets live under `quant/infrastructure/var/research/idea_bank/` for the local idea bank and discovery summaries, and `quant/infrastructure/var/research/reports/<strategy_or_idea_id>/` for each full research report. `quant/infrastructure/var/research/reports/latest/` is the API/frontend pointer to the latest report and latest evaluation summary. New runs should not write root-level `idea_bank.*`, `discovered_strategies.md`, `strategy_evaluation.md`, `full_research_report.*`, or `last_result.json`; API helpers may still read old root-level files only as fallback during migration.
- Complex research reports must be rendered as HTML. Simple operational notes, AGENTS files, and short indexes may remain Markdown.
- Each run must write a Chinese `full_research_report.html` plus a timestamped run copy. A lightweight `full_research_report.md` index may point to the HTML report for compatibility. The report must follow the canonical template contract at `quant/infrastructure/var/research/report_templates/full_research_report_template.html` exactly, including the fixed top-level structure: `1. 结论汇总`, `2. idea 来源与初筛`, `3. 信号定义`, `4. 数据来源及 benchmark 定义`, `5. 信号验证`, `6. 策略回测报告`, `7. purged walk-forward`, `8. 最终推荐与下一步计划`. Any report format change must update that template and `quant/tests/test_research_report_contract.py` in the same change.
- A final strategy research recommendation cannot rely on vectorized portfolio diagnostics alone. Implemented strategies need a strict framework backtest through the injected backtest path, including realistic execution, costs, trading constraints, statistical significance, and artifact links.
- Successful candidate integration should write a promotion dossier artifact and attach artifact metadata to candidate `research_meta`.
- Research signal logic, validation forward returns, IC decay, and sensitivity sweeps must use HFQ adjusted prices (`adj_*`, or raw price multiplied by `adj_factor`) when available. Raw prices are only a fallback for missing adjustment data or execution/fill accounting outside research validation.
- Validation signal formulas must match generated strategy signal orientation. A candidate should not pass validation on a negative IC unless the spec/code explicitly encodes an inverse signal.
- A-share strategy recommendations must be long-only unless the implementation explicitly uses a legal shorting or hedging instrument. Long-short spreads may be reported only as non-tradable alpha diagnostics, not as deployable portfolio results.
- A-share full reports must benchmark against CSI 300 index symbol `000300` when it is present in `daily_cn`; fallback to `510300` only when `000300` is missing. Reports must state which benchmark was used and the benchmark data coverage.
- Current research scope is A-share only. Strategy discovery, admission, StrategySpec universe, validation, reports, generated strategy defaults, and research CLI/API defaults must use A-share symbols such as `000300`, `000905`, `600519`, `000001`, and `510300`; do not emit US symbols such as `AAPL`, `MSFT`, `SPY`, or `QQQ` in research artifacts until US-market research is explicitly added.

## Modification Rules

| Change | Files |
|--------|-------|
| Discovery orchestration | `scout.py` |
| Source fan-out/query plans | `discovery/source_hub.py` |
| Idea/source quality scoring | `discovery/quality.py` |
| Evaluation prompt/parsing | `evaluator.py` |
| Admission-score rubric | `evaluation_rubric.py` |
| Strategy code generation | `integrator.py` |
| Candidate lifecycle | `pool.py` |
| Scheduling | `scheduler.py` |
| Pipeline orchestration/artifacts | `research_engine.py` |

## Known Pitfalls

- `StrategyIntegrator` writes files to `features/strategies/<name>/`; ensure the target directory is writable and the generated candidate remains inactive.
- `StrategyEvaluator` falls back to deterministic heuristic scoring when no LLM is configured or the LLM call fails. This should be conservative and must still emit admission evidence.
- arXiv and SSRN have rate limits or blocking behavior; adapters should fail closed and return partial results rather than crashing a run.
- Automatic backtest requires DuckDB market data; missing data should reject or pause the candidate rather than promoting it.
- Source quality is an upstream triage signal, not proof of alpha. Statistical validation remains mandatory.

## Hexagonal Boundary

- `ResearchEngine`, `StrategyIntegrator`, and `CandidatePool` depend on `quant.domain.ports.ResearchStore`.
- File and DuckDB persistence are infrastructure adapters injected by composition roots.
- Hypothesis ledger persistence is part of the `ResearchStore` port; research feature code must call the port and never write ledger files or DuckDB tables directly.
- Do not write filesystem, DuckDB, HTTP client, or other external adapter logic directly in this feature unless it is hidden behind an injected port.

## Research Rigor Modules

- `validation/cross_sectional.py` owns full-universe rank IC, ICIR, IC decay, and Fama-MacBeth statistics.
- `validation/factor_validator.py` is the validation gate; sensitivity checks are opt-in through config.
- API/CLI composition roots inject `StrategySpecBuilder` and `FactorValidator` by default when `validation_enabled=True`.
- `rigor/backtest_hub.py` owns purged walk-forward, DSR, regime breakdown, and capacity viability checks.
- `validation/ff_decomposition.py` handles factor attribution from injected factor data.
- `ensemble/optimizer.py` implements equal-weight, inverse-vol, and ERC weighting with graceful fallback.
