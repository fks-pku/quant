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
- Research orchestration keeps `discover` as a separate idea-bank stage, then splits formal research into three explicit runnable layers: 快研究（admission、StrategySpec、HFQ 信号验证、向量化组合诊断）、严格回测（项目 Backtester 执行约束）、walk-forward strict audit（滚动 OOS strict replay）。Each layer must emit a structured conclusion and can be executed independently through API/CLI stage modes. `formal`/`full` may still run the complete sequence for compatibility, but walk-forward must not run before strict backtest in complete runs. Validation failure is not an early stop; rejection is a final Go / No-Go decision after the strict flow has run.
- Default new-strategy research runs use `full`/`formal` end-to-end mode, not a standalone strict-stage report; single-stage `fast`/`strict`/`walkforward` is only for explicit stage reruns.
- Stable research assets live under `quant/infrastructure/var/research/idea_bank/` for the local idea bank and discovery summaries, and `quant/infrastructure/var/research/reports/<strategy_or_idea_id>/` for per-strategy reports. `quant/infrastructure/var/research/reports/latest/` is the API/frontend pointer to the latest full report, latest stage reports, and latest evaluation summary; `reports/latest/strategy_evaluation.md` is the compatibility summary location. New runs should not write root-level `idea_bank.*`, `discovered_strategies.md`, `strategy_evaluation.md`, `full_research_report.*`, or `last_result.json`.
- Complex research reports must be rendered as HTML. Simple operational notes, AGENTS files, and short indexes may remain Markdown.
- Each end-to-end research run must write `full_research_report.html` plus the three independent Chinese stage reports: `fast_research_report.html`, `strict_backtest_report.html`, and `walkforward_audit_report.html`, each with a timestamped run copy. The rendered canonical sample for this contract is `quant/features/strategies/xueqiu_small_cap_financial_filter/full_research_report.html`; treat it as the reference layout whenever the user asks to "研究策略", "策略研究", or produce a full strategy research report. The full report must use exactly these top-level sections: `1. Final Decision`, `2. Metric Checklist`, `3. Strategy Logic And Core Evidence`, `4. Key Risks`, `5. Appendix`, and `6. TODO：上线前还需要做什么`. The strategy-logic section must include plain-language `策略逻辑` and `止盈止损逻辑`, then core evidence. Default reports use `risk_exit.enabled=true`; disabled risk-exit runs are parameter-sensitivity/ablation artifacts and must not be rendered as default report comparisons. The active checklist is `max_adv_participation <= 5% ADV`, `total_trades > 50`, and the CAGR/MaxDD tier gate (`5%-10% CAGR => MaxDD <=15%`, `10%-15% => <=25%`, `15%-20% => <=30%`, `20%+ => <=50%`). Walk-forward evidence is retained as audit evidence but is not part of the current checklist. Parameter sensitivity is an optional incremental artifact and must not be rendered in the default full report or strict report template. Missing required checklist evidence is a fail, and strategy-type-specific non-applicable evidence must be rendered as `n/a` with a reason. User-facing reports must not emit raw `not_recorded`. Do not generate or maintain `full_research_report.md`. Any report format change must update `quant/tests/test_research_report_contract.py` in the same change.
- If the active checklist passes, the strategy belongs under `quant/features/strategies/<strategy_id>/` and the same `full_research_report.html` must be copied into that strategy directory. If the checklist fails or lacks required evidence but the research run has not reached final No-Go, keep the strategy under `quant/features/strategies/reject/<strategy_id>/`. Once a generated strategy is finally rejected, remove it from `quant/features/strategies/` and archive it under `quant/features/rejected_strategy/<strategy_id>/`. Parameter sensitivity can be run incrementally later and is not a promotion blocker unless the user explicitly raises it into the active checklist.
- Full reports generated by standalone stage reruns must render persisted hypothesis-ledger evidence from prior stages. A walk-forward-only run must not hide saved `strict_backtest` metrics, charts, or checklist values just because the current `ResearchResult.backtested` counter is zero.
- Walk-forward audit reports must show audit thresholds next to the observed value and per-metric verdict, including worst OOS Sharpe, profitable split ratio, DSR warning threshold, and capacity participation threshold, but those audit verdicts are not part of the current Go / No-Go checklist.
- Walk-forward OOS summary statistics (`aggregate_oos_sharpe`, `worst_oos_sharpe`, `pct_profitable_splits`, DSR, and regime breakdown) must exclude split windows with zero executed trades. Reports must still retain those no-trade split rows and show `total_splits`, `evaluated_splits`, and `no_trade_splits` so the denominator is auditable.
- Final Go / No-Go must evaluate `production_gate.py` when strict backtest evidence is present. The gate must record thresholds, observed metrics, failures, and warnings under `final_decision.scores.production_gate`; a strategy cannot show a final pass when the production gate fails. Walk-forward failures may be shown as audit findings, but they do not reject the strategy under the current production gate.
- CLI/API research composition roots must load `config/research.yaml` and sync production gate walk-forward thresholds into `RigorHub` config so report reruns use the current standard after threshold updates.
- Fast research reports must state the core hypothesis in Chinese and describe signal construction end-to-end: input fields, filters, signal direction, Top 20 selection, execution lag, and strict execution constraints.
- Strict backtest reports must present `策略执行逻辑` as exactly three parts: `信号详细说明`, `每日运行步骤`, and `执行约束摘要`. `信号详细说明` should explain core hypothesis, trading universe, input fields, entry/quality filters, ranking/signal rule, portfolio construction, rebalance/execution timing, exit/risk controls, and risk budget from `strategy_spec.strategy_logic` when available.
- Fast research signal-validation tables must separate observed values, generally meaningful/good reference levels, and metric explanations.
- A final strategy research recommendation cannot rely on vectorized portfolio diagnostics alone. Implemented strategies need a strict framework backtest through the injected backtest path, including realistic execution, costs, trading constraints, statistical significance, and artifact links.
- Successful candidate integration should write a promotion dossier artifact and attach artifact metadata to candidate `research_meta`.
- Rejected generated strategies must be removed from `quant/features/strategies/` and archived under `quant/features/rejected_strategy/<strategy_id>/` so they remain auditable without entering the strategy pool. Tests and audit scripts that need a final rejected strategy must import it through `quant.features.rejected_strategy...` or an explicit archive loader, not through a missing top-level `quant.features.strategies.<strategy_id>` path.
- Research signal logic, validation forward returns, IC decay, and sensitivity sweeps must use HFQ adjusted prices (`adj_*`, or raw price multiplied by `adj_factor`) when available. Raw prices are only a fallback for missing adjustment data or execution/fill accounting outside research validation.
- Any formula key emitted in a strategy report or strict grid `strategy_spec` must be registered in `validation/signal_library.py` before fast validation is treated as rerunnable evidence.
- Validation signal formulas must match generated strategy signal orientation. A candidate should not pass validation on a negative IC unless the spec/code explicitly encodes an inverse signal.
- A-share strategy recommendations must be long-only unless the implementation explicitly uses a legal shorting or hedging instrument. Long-short spreads may be reported only as non-tradable alpha diagnostics, not as deployable portfolio results.
- A-share full reports must benchmark against CSI 300 index symbol `000300` when it is present in `daily_cn_ochl`; fallback to `510300` only when `000300` is missing. Reports must state which benchmark was used and the benchmark data coverage.
- A-share long-only portfolio diagnostics use fixed Top 20 valid-signal stocks as the default top bucket. Top 1% remains a separate concentration diagnostic, not the default deployable bucket.
- Research-generated strategy candidates default to `max_position_pct=1.0` so strict Backtester results use full target gross exposure unless a study explicitly overrides sizing.
- A-share strict research backtests must attach an enabled execution cost model for every CN strategy: small-cap/low-price/cap-field strategies use `small_cap_realistic`, ETF/LOF/fund rotation uses `cn_etf_liquidity_impact`, and other A-share daily strategies use `cn_daily_liquidity_impact`.
- Strict grid reports must keep data symbols scenario-local. Benchmark, timing, hedge, ETF, or blend legs used by one scenario must not be injected into unrelated baseline scenarios, because extra symbols can change provider trading-date/data-batch behavior and distort comparisons.
- Strict grid reports must apply explicit risk constraints before headline-return ranking. If no scenario meets all target metrics, select the highest-Sharpe candidate from the risk-controlled subset rather than promoting a high-return scenario that breaches the drawdown constraint.
- Dedicated strict grid scripts must read the current strict threshold standard from `config/research.yaml` instead of hard-coding stale drawdown gates in `meets_goal` or best-scenario selection.
- Walk-forward strict audit must reuse the same strict execution-cost model family, archived strategy parameters, and configured CN initial cash (`default_initial_cash`, currently 20000) as standalone strict backtests; it must not silently fall back to fixed 5bps-only fills or stale capital.
- Walk-forward split runners for A-share cap/quality strategies must load status/daily_basic-enriched bars and the current stored candidate parameters; zero-trade all-split results are invalid unless the report proves the strategy deliberately holds cash.
- Standalone strict backtests must instantiate strategies with persisted candidate `parameters` plus the resolved `symbols`; archived rejected strategies merge archive config defaults before persisted overrides, and unsupported parameters are filtered by the strategy constructor signature rather than silently forcing defaults.
- Archived ETF category strategies must let resolved category maps override stale persisted fixed symbol lists for strict backtest, walk-forward, and report rendering. Promoted ETF representative-bucket strategies should use `audited_stable_etf_registry` by default: categories and symbols come only from the user-approved registry, while each rebalance still filters by current bar, NAV/size, liquidity, and lookback evidence. New ETF categories require explicit audit and registry update before use. ETF reports must include an ETF metadata survivorship audit when fund metadata lacks delist markers or bar-only symbols exist.
- A-share low-price/small-cap generated strategies must include the delisting risk guard in both fast validation eligibility and strict strategy execution: price floor, liquidity floor, ST/suspension/list-status filters, and daily risk exits for held positions.
- Formal A-share research defaults to strict backtests from `2016-01-01` through `2026-05-01` with `default_initial_cash=20000`, and reports must include a yearly strategy-return calendar from structured strict Backtester equity results.
- Full reports must include a lightweight PnL attribution bridge from structured validation outputs to show where signal-only returns degrade before strict Backtester execution.
- Purged walk-forward splits may run in parallel through `rigor_config.purged_walkforward.parallel_workers`; split results must remain date-ordered and each split runner must use isolated state. Composition roots may enable `prefetch_data` so split runners reuse one full-window market-data load and only slice OOS windows in memory.
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
| Rejected strategy archive | `research_engine.py` |
| Production online-readiness gate | `production_gate.py`, `research_engine.py`, `config/research.yaml` |
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
- `validation/signal_library.py` owns executable validation formula keys, including WorldQuant Alpha #001 exact daily close signal support.
- `validation/factor_validator.py` is the validation gate; sensitivity checks are opt-in through config.
- API/CLI composition roots inject `StrategySpecBuilder` and `FactorValidator` by default when `validation_enabled=True`.
- `rigor/backtest_hub.py` owns purged walk-forward, DSR, regime breakdown, and capacity viability checks.
- `production_gate.py` owns professional daily-strategy online-readiness thresholds and attaches the final production gate verdict from signal, strict backtest, walk-forward, cost/capacity, and correlation evidence.
- `validation/ff_decomposition.py` handles factor attribution from injected factor data.
- `ensemble/optimizer.py` implements equal-weight, inverse-vol, and ERC weighting with graceful fallback.
